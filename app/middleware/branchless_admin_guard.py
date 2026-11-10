# app/middleware/branchless_admin_guard.py
"""
Garde « admin sans branche » : lecture seule sur les branches sans abonnement.

Règle :
- S'applique uniquement aux ADMIN sans branche (branch_id = NULL) ; les autres
  utilisateurs gardent le fonctionnement existant.
- Lecture (GET / HEAD / OPTIONS) : toujours autorisée sur les branches de son tenant.
  Si la branche ciblée n'a pas d'abonnement actif, la réponse porte les headers
  X-Read-Only-Mode / X-Subscription-Expired pour que le frontend passe en lecture seule.
- Écriture (POST / PUT / PATCH / DELETE) : autorisée seulement si TOUTES les branches
  ciblées (header X-Branch-ID, query branch_id, branch_id du corps JSON) appartiennent
  au tenant de l'admin ET ont un abonnement actif. Sinon : 403.
- Écriture sans aucune branche ciblée : 403 (branch_required).
- Les routes nécessaires pour souscrire / gérer son compte restent accessibles
  (auth, abonnements, paiements, codes d'abonnement, tenant, branches, session).

Middleware ASGI pur (pas BaseHTTPMiddleware) afin de pouvoir relire le corps JSON
puis le rejouer intact à l'application.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs
from uuid import UUID

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.core.branch_scope import (
    ADMIN_ROLES,
    SAFE_METHODS,
    BranchAccessReason,
    build_access_denied_detail,
    evaluate_branch_access,
    is_branchless_admin,
    parse_uuid,
)

logger = logging.getLogger(__name__)

# Chemins (préfixes) où l'admin doit pouvoir écrire même sans abonnement actif :
# c'est par eux qu'il souscrit, renouvelle, crée/configure ses branches.
WRITE_EXEMPT_PREFIXES = (
    "/api/v1/auth",
    "/api/v1/subscriptions",
    "/api/v1/subscription-codes",
    "/api/v1/payments",
    "/api/v1/saas/payments",
    "/api/v1/tenants",
    "/api/v1/branches",
    "/api/v1/pharmacies",
    "/api/v1/session",
    "/api/v1/user/",
    "/docs",
    "/redoc",
    "/openapi.json",
)

MAX_BODY_BYTES = 1_000_000  # corps JSON relu au maximum (au-delà : header/query seulement)
BODY_BRANCH_KEYS = ("branch_id", "active_branch_id")


def _is_write_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in WRITE_EXEMPT_PREFIXES)


def _extract_branch_ids_from_json(data: Any, found: List[UUID]) -> None:
    if isinstance(data, dict):
        for key in BODY_BRANCH_KEYS:
            parsed = parse_uuid(data.get(key))
            if parsed:
                found.append(parsed)
    elif isinstance(data, list):
        for item in data[:1000]:
            if isinstance(item, dict):
                _extract_branch_ids_from_json(item, found)


class BranchlessAdminGuardMiddleware:
    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------------ ASGI
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)

        headers: Dict[bytes, bytes] = {k.lower(): v for k, v in scope.get("headers", [])}
        token = self._bearer(headers)
        if not token:
            return await self.app(scope, receive, send)

        payload = self._decode(token)
        if not payload or payload.get("type") != "access":
            return await self.app(scope, receive, send)
        if str(payload.get("role", "")).lower() not in ADMIN_ROLES:
            return await self.app(scope, receive, send)  # pas un admin : aucun coût

        method: str = scope["method"]
        path: str = scope["path"]
        is_write = method not in SAFE_METHODS

        if is_write and _is_write_exempt(path):
            return await self.app(scope, receive, send)

        # Branches ciblées par la requête
        branch_ids: List[UUID] = []
        header_branch = parse_uuid(headers.get(b"x-branch-id", b"").decode("latin-1"))
        if header_branch:
            branch_ids.append(header_branch)
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        for raw in query.get("branch_id", []):
            parsed = parse_uuid(raw)
            if parsed:
                branch_ids.append(parsed)

        # Corps JSON (écritures) : lu puis rejoué intact
        receive_for_app = receive
        if is_write:
            body, receive_for_app = await self._buffer_body(scope, headers, receive)
            if body:
                try:
                    _extract_branch_ids_from_json(json.loads(body), branch_ids)
                except (ValueError, UnicodeDecodeError):
                    pass

        # Dédoublonnage en conservant l'ordre
        branch_ids = list(dict.fromkeys(branch_ids))

        try:
            verdict = await run_in_threadpool(
                self._evaluate, str(payload.get("sub")), branch_ids, is_write
            )
        except Exception as exc:  # ne jamais casser l'API sur une panne du garde
            logger.error("BranchlessAdminGuard: erreur d'évaluation (%s)", exc, exc_info=True)
            return await self.app(scope, receive_for_app, send)

        if verdict is None:  # pas un admin sans branche
            return await self.app(scope, receive_for_app, send)

        reason, failing_branch, read_only_branch = verdict

        if reason != BranchAccessReason.OK:
            logger.warning(
                "🔒 Admin sans branche bloqué: %s %s (%s, branche=%s)",
                method, path, reason.value, failing_branch,
            )
            response = JSONResponse(
                status_code=403,
                content=build_access_denied_detail(reason, failing_branch),
            )
            return await response(scope, receive_for_app, send)

        if read_only_branch:
            send = self._with_read_only_headers(send)
        return await self.app(scope, receive_for_app, send)

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _bearer(headers: Dict[bytes, bytes]) -> Optional[str]:
        auth = headers.get(b"authorization", b"").decode("latin-1")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip() or None
        return None

    @staticmethod
    def _decode(token: str) -> Optional[Dict[str, Any]]:
        try:
            from app.core.security import decode_token

            return decode_token(token)
        except Exception:
            return None  # token invalide/expiré : les dépendances renverront le 401

    @staticmethod
    async def _buffer_body(scope, headers: Dict[bytes, bytes], receive):
        """Lit le corps JSON (petit) et fournit un `receive` qui le rejoue."""
        content_type = headers.get(b"content-type", b"").decode("latin-1").lower()
        try:
            length = int(headers.get(b"content-length", b"-1"))
        except ValueError:
            length = -1
        if "application/json" not in content_type or length < 0 or length > MAX_BODY_BYTES:
            return b"", receive

        chunks: List[bytes] = []
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return body, replay

    @staticmethod
    def _evaluate(user_id: str, branch_ids: List[UUID], is_write: bool):
        """
        Retourne None si l'utilisateur n'est pas un admin sans branche, sinon
        (raison, branche fautive, True si lecture sur une branche sans abonnement actif).
        """
        from app.db.session import SessionLocal
        from app.models.user import User

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if user is None or not is_branchless_admin(user):
                return None

            if not branch_ids:
                if is_write:
                    return BranchAccessReason.BRANCH_REQUIRED, None, False
                return BranchAccessReason.OK, None, False

            read_only = False
            for branch_id in branch_ids:
                reason = evaluate_branch_access(db, user.tenant_id, branch_id)
                if reason == BranchAccessReason.OK:
                    continue
                if reason == BranchAccessReason.BRANCH_NOT_IN_TENANT:
                    return reason, branch_id, False  # isolation tenant : lecture aussi
                if reason in (BranchAccessReason.NO_SUBSCRIPTION, BranchAccessReason.SUBSCRIPTION_INACTIVE):
                    if is_write:
                        return reason, branch_id, False
                    read_only = True  # lecture autorisée, marquée read-only
                    continue
                if is_write:  # branche inexistante / inactive
                    return reason, branch_id, False
            return BranchAccessReason.OK, None, read_only
        finally:
            db.close()

    @staticmethod
    def _with_read_only_headers(send):
        async def wrapped(message):
            if message["type"] == "http.response.start":
                hdrs = list(message.get("headers", []))
                hdrs += [
                    (b"x-read-only-mode", b"true"),
                    (b"x-subscription-expired", b"true"),
                    (b"x-allowed-methods", b"GET, HEAD, OPTIONS"),
                ]
                message["headers"] = hdrs
            await send(message)

        return wrapped

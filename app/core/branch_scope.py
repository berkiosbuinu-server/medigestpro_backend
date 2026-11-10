# app/core/branch_scope.py
"""
Portée « branche » des utilisateurs.

Règle métier :
- Un ADMIN sans branche (branch_id = None) est un admin du tenant, pas d'une branche :
  * il peut se connecter sans abonnement (rien à vérifier au login : il doit pouvoir
    se connecter pour souscrire / renouveler) ;
  * il choisit la branche sur laquelle il travaille (header X-Branch-ID, paramètre
    branch_id ou branch_id dans le corps JSON) ;
  * il ne peut travailler (ÉCRIRE) que pour une branche de SON tenant qui possède un
    abonnement ACTIF ;
  * sur une branche sans abonnement (ou expiré / suspendu / annulé) : BLOCAGE des
    écritures (POST / PUT / PATCH / DELETE) -> lecture seule (GET / HEAD / OPTIONS) ;
  * une écriture sans branche cible est refusée (on ne sait pas pour quelle branche
    il travaille).
- L'abonnement dépend d'une branche : tous les utilisateurs rattachés à cette
  branche en bénéficient (inchangé).
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID

ADMIN_ROLES = {"admin", "tenant_admin"}
SUPER_ADMIN_ROLES = {"super_admin", "superadmin"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _role_str(user: Any) -> str:
    role = getattr(user, "role", None)
    if role is None:
        return ""
    if hasattr(role, "value"):
        role = role.value
    return str(role).lower()


def is_branchless_admin(user: Any) -> bool:
    """True si l'utilisateur est un admin (non super admin) sans branche (branch none)."""
    if user is None:
        return False
    return _role_str(user) in ADMIN_ROLES and getattr(user, "branch_id", None) is None


def is_global_actor(user: Any) -> bool:
    """Super admin ou admin sans branche : n'est lié à aucune branche pour son login."""
    return _role_str(user) in SUPER_ADMIN_ROLES or is_branchless_admin(user)


# ---------------------------------------------------------------------------
# Accès en écriture d'un admin sans branche à une branche cible
# ---------------------------------------------------------------------------

class BranchAccessReason(str, Enum):
    OK = "ok"
    BRANCH_REQUIRED = "branch_required"                  # aucune branche cible indiquée
    BRANCH_NOT_FOUND = "branch_not_found"                # branche inexistante / inactive
    BRANCH_NOT_IN_TENANT = "branch_not_in_tenant"        # branche d'un autre tenant
    NO_SUBSCRIPTION = "branch_subscription_required"     # aucun abonnement sur la branche
    SUBSCRIPTION_INACTIVE = "branch_subscription_expired"  # abonnement expiré / suspendu / annulé


_MESSAGES = {
    BranchAccessReason.BRANCH_REQUIRED: (
        "Sélectionnez une branche avec un abonnement actif pour effectuer cette action "
        "(header X-Branch-ID ou paramètre branch_id)."
    ),
    BranchAccessReason.BRANCH_NOT_FOUND: "Branche introuvable ou inactive.",
    BranchAccessReason.BRANCH_NOT_IN_TENANT: "Cette branche n'appartient pas à votre tenant.",
    BranchAccessReason.NO_SUBSCRIPTION: (
        "Cette branche n'a aucun abonnement. Mode lecture seule : aucune écriture n'est autorisée."
    ),
    BranchAccessReason.SUBSCRIPTION_INACTIVE: (
        "L'abonnement de cette branche n'est plus actif. Mode lecture seule : aucune écriture n'est autorisée."
    ),
}


def parse_uuid(value: Any) -> Optional[UUID]:
    if value is None or value == "":
        return None
    try:
        return UUID(str(value).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def evaluate_branch_access(db: Any, tenant_id: Any, branch_id: Optional[UUID]) -> BranchAccessReason:
    """
    Détermine si un admin du tenant `tenant_id` peut ÉCRIRE pour la branche `branch_id`.
    Retourne BranchAccessReason.OK uniquement si la branche appartient au tenant ET
    possède un abonnement actif.
    """
    if branch_id is None:
        return BranchAccessReason.BRANCH_REQUIRED

    # Imports tardifs : évite les dépendances circulaires au chargement des modèles
    from app.models.branch import Branch
    from app.models.branch_subscription import BranchSubscription

    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if branch is None:
        return BranchAccessReason.BRANCH_NOT_FOUND
    if str(branch.tenant_id) != str(tenant_id):
        return BranchAccessReason.BRANCH_NOT_IN_TENANT
    if getattr(branch, "is_active", True) is False:
        return BranchAccessReason.BRANCH_NOT_FOUND

    subscription = db.query(BranchSubscription).filter(
        BranchSubscription.branch_id == branch_id
    ).first()
    if subscription is None:
        return BranchAccessReason.NO_SUBSCRIPTION
    if not subscription.is_active():
        return BranchAccessReason.SUBSCRIPTION_INACTIVE
    return BranchAccessReason.OK


def build_access_denied_detail(reason: BranchAccessReason, branch_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Corps d'erreur homogène (403) renvoyé au frontend."""
    read_only = reason in (BranchAccessReason.NO_SUBSCRIPTION, BranchAccessReason.SUBSCRIPTION_INACTIVE)
    return {
        "error": reason.value,
        "message": _MESSAGES.get(reason, "Accès refusé."),
        "mode": "READ_ONLY" if read_only else "BLOCKED",
        "read_only_mode": read_only,
        "subscription_expired": read_only,
        "branch_id": str(branch_id) if branch_id else None,
        "allowed_operations": sorted(SAFE_METHODS),
        "forbidden_operations": ["POST", "PUT", "PATCH", "DELETE"],
        "renewal_url": "/api/v1/subscriptions/plans",
    }

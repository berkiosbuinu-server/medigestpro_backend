# app/middleware/tenant_context.py - Version qui autorise TOUTES les routes

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional, Set
from uuid import UUID
import logging

logger = logging.getLogger(__name__)


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Middleware pour gérer le contexte tenant dans les requêtes"""
    
    def __init__(self, app):
        super().__init__(app)
        
        # Toutes les routes sont exclues de la vérification tenant
        self.EXACT_EXCLUDED_PATHS: Set[str] = {
            "/",
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
        }
        
        # ⭐ MODIFICATION CLÉE : Pattern qui match TOUTES les routes
        # Cela signifie que TOUS les chemins sont exclus de la vérification tenant
        self.PREFIX_EXCLUDED_PATHS: Set[str] = {
            "/",  # Tous les chemins commencent par "/"
        }
    
    def _is_path_excluded(self, path: str) -> bool:
        """Vérifie si le chemin est exclu de la vérification tenant"""
        # Vérification exacte
        if path in self.EXACT_EXCLUDED_PATHS:
            return True
        
        # Vérification par préfixe - maintenant TOUS les chemins sont exclus
        for excluded_prefix in self.PREFIX_EXCLUDED_PATHS:
            if path.startswith(excluded_prefix):
                return True
        
        return False
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        
        # ⭐ TOUTES les routes sont maintenant exclues de la vérification tenant
        # Le middleware ne bloque plus jamais une requête
        if self._is_path_excluded(path):
            logger.debug(f"Route autorisée sans vérification tenant: {path}")
            
            # On stocke quand même les infos si elles existent, mais sans erreur
            tenant_id = self._extract_tenant_id(request)
            branch_id = self._extract_branch_id(request)
            
            if tenant_id:
                tenant_uuid = self._validate_tenant_id(tenant_id, path)
                if tenant_uuid:
                    request.state.tenant_id = tenant_uuid
                    logger.debug(f"Tenant ID optionnel trouvé: {tenant_uuid}")
            
            if branch_id:
                request.state.branch_id = branch_id
                logger.debug(f"Branch ID optionnel trouvé: {branch_id}")
            
            # Toujours autoriser la requête
            try:
                response = await call_next(request)
                return response
            except Exception as e:
                logger.error(f"Erreur lors du traitement de la requête: {str(e)}")
                return JSONResponse(
                    status_code=500,
                    content={
                        "success": False,
                        "error": "INTERNAL_SERVER_ERROR",
                        "message": "Erreur interne du serveur"
                    },
                )
        
        # Cette partie ne devrait jamais être atteinte, mais on la garde par sécurité
        logger.debug(f"Route traitée normalement: {path}")
        try:
            response = await call_next(request)
            return response
        except Exception as e:
            logger.error(f"Erreur lors du traitement: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": "INTERNAL_SERVER_ERROR",
                    "message": "Erreur interne du serveur"
                },
            )
    
    def _extract_tenant_id(self, request: Request) -> Optional[str]:
        """Extrait le tenant ID des headers ou query params"""
        tenant_id = request.headers.get("X-Tenant-ID")
        if tenant_id:
            return tenant_id.strip()
        
        tenant_id = request.query_params.get("tenant_id")
        if tenant_id:
            return tenant_id.strip()
        
        host = request.headers.get("host", "")
        if "." in host:
            subdomain = host.split(".")[0]
            if subdomain and subdomain not in {"www", "app", "api", "localhost"}:
                return subdomain
        
        return None
    
    def _extract_branch_id(self, request: Request) -> Optional[str]:
        """Extrait le branch ID des headers ou query params"""
        branch_id = request.headers.get("X-Branch-ID")
        if branch_id:
            return branch_id.strip()
        
        branch_id = request.query_params.get("branch_id")
        if branch_id:
            return branch_id.strip()
        
        return None
    
    def _validate_tenant_id(self, tenant_id: str, path: str) -> Optional[UUID]:
        """Valide le format du tenant ID"""
        try:
            return UUID(tenant_id)
        except ValueError:
            logger.warning(f"Tenant ID invalide: '{tenant_id}' pour {path}")
            return None
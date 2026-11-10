# app/middleware/middleware.py

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from app.db.session import SessionLocal
from app.models.branch_subscription import BranchSubscription
import logging
import re

logger = logging.getLogger(__name__)


class SubscriptionMiddleware(BaseHTTPMiddleware):
    """Middleware pour gérer le mode lecture seule sur abonnement expiré (basé sur la branche)"""
    
    # Méthodes autorisées en lecture seule
    READ_ONLY_METHODS = {'GET', 'OPTIONS', 'HEAD'}
    
    # Endpoints toujours autorisés (même en écriture)
    ALWAYS_ALLOWED_PATHS = [
        r'^/api/v1/auth/.*$',
        r'^/api/v1/subscriptions/.*$',
        r'^/api/v1/health$',
        r'^/api/v1/me$',
        r'^/api/v1/tenants/me$',
        r'^/api/v1/session/.*$',
        r'^/api/v1/pharmacies/.*/service-status$',
        r'^/api/v1/subscriptions/status$',
        r'^/api/v1/subscriptions/usage$',
        r'^/api/v1/subscriptions/plans$',
        r'^/api/v1/branches/.*/service-status$',
        r'^/api/v1/stock/alerts/.*$',  # Lecture des alertes OK
        r'^/api/v1/dashboard/.*$',      # Dashboard en lecture OK
        r'^/docs$',
        r'^/redoc$',
        r'^/openapi.json$'
    ]
    
    async def dispatch(self, request: Request, call_next):
        # Récupérer l'utilisateur depuis le token (décodé par le middleware ou dépendance)
        user = getattr(request.state, 'user', None)
        
        # Si pas d'utilisateur, essayer de décoder le token
        if not user:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.replace("Bearer ", "")
                try:
                    from jose import jwt
                    from app.core.config import settings
                    payload = jwt.decode(
                        token, 
                        settings.SECRET_KEY, 
                        algorithms=[settings.ALGORITHM],
                        options={"verify_exp": True}
                    )
                    branch_id = payload.get("branch_id")
                    
                    if branch_id:
                        db = SessionLocal()
                        try:
                            subscription = db.query(BranchSubscription).filter(
                                BranchSubscription.branch_id == branch_id
                            ).first()
                            
                            if subscription and not subscription.is_active():
                                method = request.method
                                path = request.url.path
                                
                                # Vérifier si le chemin est toujours autorisé
                                is_allowed_endpoint = any(
                                    re.match(pattern, path) for pattern in self.ALWAYS_ALLOWED_PATHS
                                )
                                
                                # Si méthode non lecture seule et pas endpoint spécial -> bloquer
                                if method not in self.READ_ONLY_METHODS and not is_allowed_endpoint:
                                    logger.warning(
                                        f"🔒 Tentative d'écriture en mode lecture seule - "
                                        f"Branch: {branch_id}, Method: {method}, Path: {path}"
                                    )
                                    return JSONResponse(
                                        status_code=403,
                                        content={
                                            "error": "subscription_expired_readonly",
                                            "message": "L'abonnement de votre succursale a expiré. Mode lecture seule uniquement.",
                                            "read_only_mode": True,
                                            "subscription_expired": True,
                                            "branch_id": str(branch_id),
                                            "action": "Renouvelez l'abonnement de votre succursale",
                                            "allowed_operations": list(self.READ_ONLY_METHODS),
                                            "forbidden_operations": ["POST", "PUT", "PATCH", "DELETE"],
                                            "renewal_url": "/api/v1/subscriptions/plans"
                                        }
                                    )
                                
                                # Ajouter un flag pour le mode lecture seule
                                request.state.read_only_mode = True
                                logger.info(f"📖 Mode lecture seule activé pour branche {branch_id}")
                        finally:
                            db.close()
                except Exception as e:
                    logger.debug(f"Erreur décodage token dans middleware: {e}")
        
        # Traiter la requête
        response = await call_next(request)
        
        # Ajouter les headers d'information
        if hasattr(request.state, 'read_only_mode') and request.state.read_only_mode:
            response.headers["X-Read-Only-Mode"] = "true"
            response.headers["X-Subscription-Expired"] = "true"
            response.headers["X-Allowed-Methods"] = ", ".join(self.READ_ONLY_METHODS)
        
        return response
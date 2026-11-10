# app/core/middleware.py

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.branch_subscription import BranchSubscription
import logging
import re

logger = logging.getLogger(__name__)


class SubscriptionCheckMiddleware(BaseHTTPMiddleware):
    """Middleware pour vérifier l'abonnement de la branche sur les endpoints protégés"""
    
    # Endpoints exemptés de la vérification (toujours autorisés)
    EXEMPT_PATHS = [
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/tenants/register",
        "/api/v1/auth/password/reset",
        "/api/v1/auth/health",
        "/api/v1/auth/api-status",
        "/api/v1/auth/verify-subscription",
        "/api/v1/auth/subscription/readonly-status",
        "/api/v1/auth/reset-active-branch",
        "/docs",
        "/redoc",
        "/openapi.json"
    ]
    
    # Endpoints autorisés en lecture seule (même avec abonnement expiré)
    READONLY_ALLOWED_PATHS = [
        r'^/api/v1/auth/.*$',
        r'^/api/v1/subscriptions/status$',
        r'^/api/v1/subscriptions/usage$',
        r'^/api/v1/subscriptions/plans$',
        r'^/api/v1/subscriptions/billing-history$',
        r'^/api/v1/health$',
        r'^/api/v1/me$',
        r'^/api/v1/tenants/me$',
        r'^/api/v1/session/.*$',
        r'^/api/v1/pharmacies/.*/service-status$',
        r'^/api/v1/pharmacies/active$',
        r'^/api/v1/branches/.*$',  # GET sur les branches
        r'^/api/v1/stock/alerts/stock$',
        r'^/api/v1/stock/alerts/expiry$',
        r'^/api/v1/dashboard/stats$',
        r'^/api/v1/dashboard/alerts$',
        r'^/api/v1/transfers/$',
        r'^/api/v1/transfers$',
        r'^/api/v1/categories/.*$',
        r'^/api/v1/products/.*$',
        r'^/api/v1/sales/.*$',
        r'^/api/v1/customers/.*$',
        r'^/api/v1/reports/.*$',
        r'^/api/v1/inventory/.*$',
        r'^/api/v1/orders/.*$',
        r'^/api/v1/users/.*$',
        r'^/api/v1/payments/.*$',
        r'^/api/v1/sync/.*$',
        r'^/api/v1/capital/.*$',
        r'^/api/v1/subscription-codes/.*$',
    ]
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method
        
        # Vérifier si le chemin est exempté (toujours autorisé)
        if any(path.startswith(exempt) for exempt in self.EXEMPT_PATHS):
            return await call_next(request)
        
        # Toujours autoriser les requêtes OPTIONS (CORS preflight)
        if method == "OPTIONS":
            return await call_next(request)
        
        # Récupérer le token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return await call_next(request)
        
        token = auth_header.replace("Bearer ", "")
        
        # Décoder le token pour obtenir le branch_id
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
            user_id = payload.get("sub")
            tenant_id = payload.get("tenant_id")
            
            if branch_id and user_id:
                db = SessionLocal()
                try:
                    # Vérifier l'abonnement de la branche
                    subscription = db.query(BranchSubscription).filter(
                        BranchSubscription.branch_id == branch_id
                    ).first()
                    
                    subscription_active = subscription.is_active() if subscription else False
                    
                    if not subscription_active:
                        logger.warning(f"⚠️ Abonnement expiré pour branche {branch_id}, utilisateur {user_id}")
                        
                        # Vérifier si le path est autorisé en lecture seule
                        is_readonly_allowed = any(
                            re.match(pattern, path) for pattern in self.READONLY_ALLOWED_PATHS
                        )
                        
                        # Méthodes de lecture seule
                        read_only_methods = {'GET', 'HEAD'}
                        
                        # Si méthode non lecture seule ET path non autorisé -> bloquer
                        if method not in read_only_methods and not is_readonly_allowed:
                            logger.info(f"🔒 Blocage écriture pour abonnement expiré: {method} {path}")
                            return JSONResponse(
                                status_code=403,
                                content={
                                    "error": "subscription_expired_readonly",
                                    "message": "L'abonnement de votre succursale a expiré. Mode lecture seule uniquement.",
                                    "read_only_mode": True,
                                    "subscription_expired": True,
                                    "branch_id": str(branch_id),
                                    "action": "Renouvelez l'abonnement de votre succursale",
                                    "allowed_operations": ["GET", "HEAD", "OPTIONS"],
                                    "forbidden_operations": ["POST", "PUT", "PATCH", "DELETE"],
                                    "renewal_url": "/api/v1/subscriptions/plans"
                                }
                            )
                        
                        # Autoriser la lecture seule
                        logger.info(f"📖 Lecture seule autorisée pour abonnement expiré: {method} {path}")
                        request.state.read_only_mode = True
                        
                finally:
                    db.close()
                    
        except jwt.ExpiredSignatureError:
            # Token expiré - laisser passer pour que le client refresh
            logger.debug(f"Token expiré pour {path}, laisser passer pour refresh")
            pass
        except Exception as e:
            logger.error(f"❌ Erreur dans middleware d'abonnement: {e}")
        
        response = await call_next(request)
        
        # Ajouter les headers de statut d'abonnement
        if hasattr(request.state, 'read_only_mode') and request.state.read_only_mode:
            response.headers["X-Read-Only-Mode"] = "true"
            response.headers["X-Subscription-Expired"] = "true"
            response.headers["X-Allowed-Methods"] = "GET, HEAD, OPTIONS"
        
        return response
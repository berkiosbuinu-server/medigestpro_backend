# app/middleware/auth_middleware.py (remplace les deux fichiers)
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.branch_subscription import BranchSubscription
from app.core.security import decode_token
from jose import jwt, ExpiredSignatureError
from app.core.config import settings
import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class UnifiedAuthSubscriptionMiddleware(BaseHTTPMiddleware):
    """
    Middleware unifié qui gère :
    1. Authentification (décodage token)
    2. Vérification d'abonnement
    3. Mode lecture seule pour abonnements expirés
    
    Optimisations :
    - Cache des vérifications d'abonnement
    - Une seule requête DB par requête
    - Pattern matching optimisé
    """
    
    # Endpoints exemptés (toujours autorisés sans vérification)
    EXEMPT_PATHS = {
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/tenants/register",
        "/api/v1/auth/password/reset",
        "/api/v1/auth/health",
        "/api/v1/auth/api-status",
        "/api/v1/auth/verify-subscription",
        "/api/v1/auth/subscription/readonly-status",
        "/api/v1/auth/reset-active-branch",
        "/health",
        "/",
    }
    
    # Endpoints de docs (toujours autorisés)
    DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}
    
    # Patterns pour endpoints autorisés en lecture seule (compilés une fois)
    READONLY_PATTERNS = [
        re.compile(r'^/api/v1/auth/.*$'),
        re.compile(r'^/api/v1/subscriptions/status$'),
        re.compile(r'^/api/v1/subscriptions/usage$'),
        re.compile(r'^/api/v1/subscriptions/plans$'),
        re.compile(r'^/api/v1/subscriptions/billing-history$'),
        re.compile(r'^/api/v1/health$'),
        re.compile(r'^/api/v1/me$'),
        re.compile(r'^/api/v1/tenants/me$'),
        re.compile(r'^/api/v1/session/.*$'),
        re.compile(r'^/api/v1/pharmacies/.*/service-status$'),
        re.compile(r'^/api/v1/pharmacies/active$'),
        re.compile(r'^/api/v1/branches/.*$'),
        re.compile(r'^/api/v1/stock/alerts/.*$'),
        re.compile(r'^/api/v1/dashboard/.*$'),
        re.compile(r'^/api/v1/transfers/?$'),
        re.compile(r'^/api/v1/categories/.*$'),
        re.compile(r'^/api/v1/products/.*$'),
        re.compile(r'^/api/v1/sales/.*$'),
        re.compile(r'^/api/v1/customers/.*$'),
        re.compile(r'^/api/v1/reports/.*$'),
        re.compile(r'^/api/v1/inventory/.*$'),
        re.compile(r'^/api/v1/orders/.*$'),
        re.compile(r'^/api/v1/users/.*$'),
        re.compile(r'^/api/v1/payments/.*$'),
        re.compile(r'^/api/v1/sync/.*$'),
        re.compile(r'^/api/v1/capital/.*$'),
        re.compile(r'^/api/v1/subscription-codes/.*$'),
    ]
    
    # Cache simple pour les abonnements (en mémoire, 60 secondes)
    # Pour production, remplacer par Redis
    _subscription_cache = {}
    
    def __init__(self, app, use_cache: bool = True, cache_ttl: int = 60):
        super().__init__(app)
        self.use_cache = use_cache
        self.cache_ttl = cache_ttl
    
    def _is_exempt_path(self, path: str) -> bool:
        """Vérifie si le chemin est exempté"""
        if path in self.EXEMPT_PATHS:
            return True
        if any(path.startswith(doc_path) for doc_path in self.DOCS_PATHS):
            return True
        return False
    
    def _is_readonly_allowed(self, path: str, method: str) -> bool:
        """Vérifie si le path est autorisé en lecture seule"""
        # Seules les méthodes GET/HEAD sont considérées lecture seule
        if method not in {'GET', 'HEAD'}:
            return False
        
        # Vérifier les patterns
        for pattern in self.READONLY_PATTERNS:
            if pattern.match(path):
                return True
        return False
    
    def _get_subscription_from_cache(self, branch_id: str) -> Optional[Tuple[bool, object]]:
        """Récupère l'abonnement du cache"""
        if not self.use_cache:
            return None
        
        cached = self._subscription_cache.get(branch_id)
        if cached:
            data, timestamp = cached
            import time
            if time.time() - timestamp < self.cache_ttl:
                return data
            else:
                # Cache expiré
                del self._subscription_cache[branch_id]
        return None
    
    def _set_subscription_cache(self, branch_id: str, subscription):
        """Met en cache l'abonnement"""
        if self.use_cache:
            import time
            self._subscription_cache[branch_id] = (subscription, time.time())
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method
        
        # 1. Initialiser request.state
        request.state.user = None
        request.state.tenant_id = None
        request.state.branch_id = None
        request.state.read_only_mode = False
        
        # 2. Toujours autoriser OPTIONS (CORS preflight)
        if method == "OPTIONS":
            return await call_next(request)
        
        # 3. Vérifier les chemins exemptés
        if self._is_exempt_path(path):
            return await call_next(request)
        
        # 4. Récupérer et décoder le token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            # Pas de token → continuer sans authentification
            # Les endpoints protégés utiliseront des dépendances
            return await call_next(request)
        
        token = auth_header.replace("Bearer ", "")
        
        try:
            # Décoder le token (une seule fois)
            payload = decode_token(token)
            
            # Vérifier que c'est un token d'accès
            if payload.get("type") != "access":
                logger.warning(f"Invalid token type for {path}")
                return await call_next(request)
            
            # Stocker les infos utilisateur dans request.state
            request.state.user = payload
            request.state.user_id = payload.get("sub")
            request.state.tenant_id = payload.get("tenant_id")
            request.state.branch_id = payload.get("branch_id")
            
            # 5. Vérifier l'abonnement (si branch_id présent)
            branch_id = payload.get("branch_id")
            if branch_id:
                subscription_active = await self._check_subscription(branch_id, path, method)
                
                if not subscription_active:
                    # Abonnement expiré → vérifier si lecture seule autorisée
                    if self._is_readonly_allowed(path, method):
                        logger.info(f"📖 Lecture seule autorisée: {method} {path}")
                        request.state.read_only_mode = True
                    else:
                        # Opération non autorisée
                        logger.warning(f"🔒 Blocage: {method} {path} (subscription expired)")
                        return JSONResponse(
                            status_code=403,
                            content={
                                "error": "subscription_expired_readonly",
                                "message": "L'abonnement de votre succursale a expiré. Mode lecture seule uniquement.",
                                "read_only_mode": True,
                                "subscription_expired": True,
                                "branch_id": str(branch_id),
                                "allowed_operations": ["GET", "HEAD", "OPTIONS"],
                                "forbidden_operations": ["POST", "PUT", "PATCH", "DELETE"],
                                "renewal_url": "/api/v1/subscriptions/plans"
                            }
                        )
            
        except ExpiredSignatureError:
            # Token expiré - logger debug et continuer
            logger.debug(f"Token expiré pour {path}")
        except Exception as e:
            # Erreur de décodage - logger warning et continuer
            logger.warning(f"Erreur décodage token pour {path}: {e}")
        
        # 6. Exécuter la requête
        response = await call_next(request)
        
        # 7. Ajouter les headers si mode lecture seule
        if request.state.read_only_mode:
            response.headers["X-Read-Only-Mode"] = "true"
            response.headers["X-Subscription-Expired"] = "true"
            response.headers["X-Allowed-Methods"] = "GET, HEAD, OPTIONS"
        
        return response
    
    async def _check_subscription(self, branch_id: str, path: str, method: str) -> bool:
        """
        Vérifie l'abonnement d'une branche avec cache.
        Retourne True si actif False sinon.
        """
        # Vérifier le cache
        cached_result = self._get_subscription_from_cache(branch_id)
        if cached_result is not None:
            return cached_result
        
        # Requête DB
        db = SessionLocal()
        try:
            subscription = db.query(BranchSubscription).filter(
                BranchSubscription.branch_id == branch_id
            ).first()
            
            is_active = subscription.is_active() if subscription else False
            
            # Mettre en cache
            self._set_subscription_cache(branch_id, is_active)
            
            return is_active
        except Exception as e:
            logger.error(f"Erreur vérification abonnement branche {branch_id}: {e}")
            # En cas d'erreur, on suppose actif pour ne pas bloquer
            return True
        finally:
            db.close()


# Pour garder la compatibilité avec les anciens noms
AuthMiddleware = UnifiedAuthSubscriptionMiddleware
SubscriptionCheckMiddleware = UnifiedAuthSubscriptionMiddleware
# auth_middleware.py - Version qui désactive complètement l'authentification

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
import logging

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware d'authentification pour FastAPI - VERSION DÉSACTIVÉE
    N'inflige JAMAIS d'erreur 401, toutes les requêtes sont autorisées
    """
    
    async def dispatch(self, request: Request, call_next):
        # Initialiser request.state.user à None par défaut
        request.state.user = None
        
        auth_header = request.headers.get("Authorization")
        
        if auth_header and auth_header.startswith("Bearer "):
            try:
                token = auth_header.replace("Bearer ", "")
                
                # Tenter de décoder le token
                from app.core.security import decode_token
                payload = decode_token(token)
                
                if payload and isinstance(payload, dict):
                    token_type = payload.get("type")
                    if token_type == "access":
                        request.state.user = payload
                        logger.debug(f"✅ Utilisateur authentifié: {payload.get('sub')}")
                    else:
                        logger.debug(f"⚠️ Mauvais type de token: {token_type}")
                else:
                    logger.debug("⚠️ Payload invalide")
                    
            except Exception as e:
                # ⭐ ON N'AFFICHE JUSTE UN LOG, ON NE BLOQUE PAS
                logger.debug(f"⚠️ Erreur d'authentification ignorée: {e}")
                # request.state.user reste None
        
        # ⭐ TOUJOURS CONTINUER - AUCUNE ERREUR 401 ICI
        response = await call_next(request)
        return response


# Alias pour la compatibilité
UnifiedAuthSubscriptionMiddleware = AuthMiddleware
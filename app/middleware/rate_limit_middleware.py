import time
from collections import defaultdict
from typing import Dict, List
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, request_limit: int = 100, window_seconds: int = 60):
        super().__init__(app)
        self.request_limit = request_limit
        self.window_seconds = window_seconds
        self.clients: Dict[str, List[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # Vous pourriez exclure certaines routes de la limitation de taux
        if request.url.path.startswith("/docs") or request.url.path.startswith("/redoc"):
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"
        now = time.time()
        
        # Nettoyer les anciennes requêtes
        self.clients[ip] = [
            timestamp for timestamp in self.clients[ip] 
            if now - timestamp < self.window_seconds
        ]
        
        # Vérifier la limite
        if len(self.clients[ip]) >= self.request_limit:
            retry_after = int(self.window_seconds - (now - self.clients[ip][0]))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Trop de requêtes. Veuillez réessayer plus tard.",
                    "retry_after": retry_after
                },
                headers={"Retry-After": str(retry_after)}
            )
        
        # Ajouter la requête actuelle
        self.clients[ip].append(now)
        
        # Limiter la taille de la liste pour éviter une croissance infinie
        if len(self.clients[ip]) > self.request_limit * 2:
            self.clients[ip] = self.clients[ip][-self.request_limit:]
        
        return await call_next(request)
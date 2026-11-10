import os
import redis.asyncio as redis
from functools import wraps
import json
import hashlib

class CacheService:
    def __init__(self):
        self.redis = None
    
    async def init(self):
        self.redis = await redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            encoding="utf-8",
            decode_responses=True,
            max_connections=50
        )
    
    async def get_or_set(self, key: str, func, ttl: int = 300):
        """Get from cache or execute function and cache result"""
        cached = await self.redis.get(key)
        if cached:
            return json.loads(cached)
        
        result = await func()
        await self.redis.setex(key, ttl, json.dumps(result))
        return result

cache_service = CacheService()

# Décorateur pour mettre en cache
def cached(ttl: int = 300, prefix: str = ""):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Générer clé unique
            key_data = f"{prefix}:{func.__name__}:{str(args)}:{str(kwargs)}"
            key = hashlib.md5(key_data.encode()).hexdigest()
            
            return await cache_service.get_or_set(key, lambda: func(*args, **kwargs), ttl)
        return wrapper
    return decorator

# Exemple d'utilisation
@cached(ttl=60, prefix="dashboard")
async def get_dashboard_stats(tenant_id: str):
    # Requête DB lourde
    pass
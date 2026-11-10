"""
app/utils/cache.py
Gestion du cache pour les rapports
"""

import json
import hashlib
from typing import Optional, Any
from datetime import datetime, timedelta
import os
import redis
import logging

logger = logging.getLogger(__name__)

# Connexion Redis (à configurer selon votre environnement)
try:
    redis_client = redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )
    redis_client.ping()
    REDIS_AVAILABLE = True
except:
    REDIS_AVAILABLE = False
    logger.warning("Redis non disponible, utilisation du cache mémoire")

# Cache mémoire fallback
memory_cache = {}


def get_cache_key(prefix: str, *args) -> str:
    """
    Génère une clé de cache
    
    Args:
        prefix: Préfixe de la clé
        *args: Arguments pour générer la clé
    
    Returns:
        Clé de cache
    """
    key_str = f"{prefix}:{':'.join(str(arg) for arg in args)}"
    return hashlib.md5(key_str.encode()).hexdigest()


def cache_report(key: str, data: Any, ttl: int = 3600) -> bool:
    """
    Met un rapport en cache
    
    Args:
        key: Clé du cache
        data: Données à mettre en cache
        ttl: Time To Live en secondes
    
    Returns:
        True si réussi
    """
    try:
        if REDIS_AVAILABLE:
            redis_client.setex(
                key,
                ttl,
                json.dumps(data, default=str)
            )
        else:
            expiry = datetime.now() + timedelta(seconds=ttl)
            memory_cache[key] = {
                "data": data,
                "expiry": expiry
            }
        
        logger.debug(f"Rapport mis en cache: {key}")
        return True
        
    except Exception as e:
        logger.error(f"Erreur lors de la mise en cache: {e}")
        return False


def get_cached_report(key: str) -> Optional[Any]:
    """
    Récupère un rapport du cache
    
    Args:
        key: Clé du cache
    
    Returns:
        Données en cache ou None
    """
    try:
        if REDIS_AVAILABLE:
            cached = redis_client.get(key)
            if cached:
                return json.loads(cached)
        else:
            if key in memory_cache:
                item = memory_cache[key]
                if item["expiry"] > datetime.now():
                    return item["data"]
                else:
                    # Nettoyer l'élément expiré
                    del memory_cache[key]
        
        return None
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du cache: {e}")
        return None


def invalidate_cache(key: str) -> bool:
    """
    Invalide un élément du cache
    
    Args:
        key: Clé du cache
    
    Returns:
        True si réussi
    """
    try:
        if REDIS_AVAILABLE:
            redis_client.delete(key)
        else:
            memory_cache.pop(key, None)
        
        logger.debug(f"Cache invalidé: {key}")
        return True
        
    except Exception as e:
        logger.error(f"Erreur lors de l'invalidation du cache: {e}")
        return False


def clear_cache(prefix: str = None) -> int:
    """
    Vide le cache
    
    Args:
        prefix: Préfixe pour filtrer (optionnel)
    
    Returns:
        Nombre d'éléments supprimés
    """
    try:
        if REDIS_AVAILABLE:
            if prefix:
                keys = redis_client.keys(f"{prefix}:*")
                if keys:
                    redis_client.delete(*keys)
                    return len(keys)
            else:
                redis_client.flushdb()
                return -1  # Indique un flush complet
        else:
            if prefix:
                keys_to_delete = [
                    k for k in memory_cache.keys()
                    if k.startswith(prefix)
                ]
                for key in keys_to_delete:
                    del memory_cache[key]
                return len(keys_to_delete)
            else:
                count = len(memory_cache)
                memory_cache.clear()
                return count
        
        return 0
        
    except Exception as e:
        logger.error(f"Erreur lors du nettoyage du cache: {e}")
        return 0
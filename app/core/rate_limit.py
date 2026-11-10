# app/core/rate_limit.py
from datetime import datetime, timedelta
import redis
import logging

logger = logging.getLogger(__name__)

# Simple in-memory rate limiter (remplacez par Redis en production)
_rate_limiter_cache = {}

def rate_limit_check(key: str, max_attempts: int = 5, window_seconds: int = 300) -> bool:
    """
    Vérifie si une action est autorisée selon les limites de taux.
    
    Args:
        key: Identifiant unique pour la limitation (ex: "sms_verify_email@example.com")
        max_attempts: Nombre maximum de tentatives dans la fenêtre
        window_seconds: Fenêtre de temps en secondes
    
    Returns:
        bool: True si autorisé, False si limité
    """
    now = datetime.utcnow()
    window_start = now - timedelta(seconds=window_seconds)
    
    # Nettoyer les anciennes entrées
    if key in _rate_limiter_cache:
        _rate_limiter_cache[key] = [
            timestamp for timestamp in _rate_limiter_cache[key]
            if timestamp > window_start
        ]
    
    # Vérifier le nombre de tentatives
    attempts = _rate_limiter_cache.get(key, [])
    if len(attempts) >= max_attempts:
        logger.warning(f"Rate limit atteint pour {key}: {len(attempts)} tentatives")
        return False
    
    # Ajouter la tentative actuelle
    attempts.append(now)
    _rate_limiter_cache[key] = attempts[-max_attempts:]  # Garder seulement les dernières
    
    return True
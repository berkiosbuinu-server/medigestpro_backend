# app/core/secrets.py
"""
Gestion des clés secrètes pour l'application
Support pour Fernet (cryptography) et autres algorithmes
"""

import os
import logging
from base64 import urlsafe_b64encode, urlsafe_b64decode
from typing import Optional, Union

logger = logging.getLogger(__name__)


def get_secret_key() -> str:
    """
    Récupère la clé secrète pour le chiffrement Fernet.
    
    Returns:
        La clé secrète sous forme de string (base64 valide pour Fernet)
        
    Raises:
        RuntimeError: Si la clé n'est pas trouvée en production
    """
    # Essayer différentes variables d'environnement
    key = os.getenv("FERNET_SECRET_KEY") or os.getenv("APP_SECRET_KEY")
    
    if not key:
        # En production, on doit avoir une clé
        is_production = os.getenv("ENV", "development").lower() in ["production", "prod"]
        
        if is_production:
            raise RuntimeError(
                "FERNET_SECRET_KEY or APP_SECRET_KEY is required in production. "
                "Please set one of these environment variables."
            )
        
        # En développement, générer une clé stable
        logger.warning("⚠️ Aucune clé secrète trouvée - Génération d'une clé de développement")
        logger.warning("⚠️ Pour la production, définir FERNET_SECRET_KEY ou APP_SECRET_KEY")
        
        # Générer une clé déterministe pour le développement
        # Cette clé est fixe pour éviter les problèmes de déchiffrement entre redémarrages
        dev_key = "medigest-dev-secret-key-2024-for-development-only-32bytes"
        
        # Marquer comme clé de développement
        get_secret_key._is_development = True
        get_secret_key._is_production = False
        
        return _ensure_fernet_key(dev_key)
    
    get_secret_key._is_development = False
    get_secret_key._is_production = True
    
    return _ensure_fernet_key(key)


def _ensure_fernet_key(key: str) -> str:
    """
    Assure que la clé est au format Fernet valide (32 bytes base64 url-safe).
    
    Args:
        key: La clé brute
        
    Returns:
        Une clé Fernet valide (string base64)
    """
    # Si la clé est déjà au format Fernet (fin par '=' et longueur appropriée)
    if key.endswith('=') and len(key) >= 32:
        # Vérifier si c'est du base64 valide
        try:
            urlsafe_b64decode(key)
            return key
        except Exception:
            pass
    
    # Convertir la clé en bytes si nécessaire
    if isinstance(key, str):
        key_bytes = key.encode('utf-8')
    else:
        key_bytes = key
    
    # Pour Fernet, la clé doit faire 32 bytes après encodage base64
    # Donc la clé brute doit faire 32 bytes
    
    if len(key_bytes) < 32:
        # Pad la clé si trop courte
        key_bytes = key_bytes.ljust(32, b'0')
    elif len(key_bytes) > 32:
        # Tronquer la clé si trop longue
        key_bytes = key_bytes[:32]
    
    # Encoder en base64 url-safe
    fernet_key = urlsafe_b64encode(key_bytes).decode('utf-8')
    
    return fernet_key


def get_raw_secret_key() -> bytes:
    """
    Récupère la clé secrète brute (non encodée en base64).
    
    Returns:
        La clé secrète brute en bytes
    """
    key = get_secret_key()
    try:
        # Décoder la clé Fernet pour obtenir la clé brute
        return urlsafe_b64decode(key)
    except Exception:
        # Si ce n'est pas du base64 valide, retourner la clé brute
        return key.encode() if isinstance(key, str) else key


def get_secret_key_bytes() -> bytes:
    """
    Récupère la clé secrète directement en bytes.
    
    Returns:
        La clé secrète en bytes (prête pour Fernet)
    """
    key = get_secret_key()
    if isinstance(key, str):
        return key.encode('utf-8')
    return key


def rotate_secret_key(new_key: Optional[str] = None) -> bool:
    """
    Effectue une rotation de la clé secrète.
    Note: Cette fonction ne ré-encrypte pas les données existantes.
    
    Args:
        new_key: Nouvelle clé (optionnelle, générée automatiquement si non fournie)
        
    Returns:
        True si la rotation a réussi, False sinon
    """
    try:
        import secrets
        import base64
        
        if new_key:
            new_key_bytes = new_key.encode() if isinstance(new_key, str) else new_key
        else:
            # Générer une nouvelle clé aléatoire de 32 bytes
            new_key_bytes = secrets.token_bytes(32)
        
        fernet_key = urlsafe_b64encode(new_key_bytes).decode('utf-8')
        
        # Mettre à jour la variable d'environnement
        os.environ["FERNET_SECRET_KEY"] = fernet_key
        
        logger.info("✅ Clé secrète rotatée avec succès")
        logger.warning("⚠️ N'oubliez pas de mettre à jour la variable d'environnement dans votre déploiement")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de la rotation de la clé: {str(e)}")
        return False


def validate_secret_key() -> dict:
    """
    Valide que la clé secrète est correctement configurée.
    
    Returns:
        Un dictionnaire avec les informations de validation
    """
    try:
        key = get_secret_key()
        
        # Vérifier le format
        is_valid_format = key.endswith('=') and len(key) >= 32
        
        # Tester le décodage base64
        try:
            decoded = urlsafe_b64decode(key)
            can_decode = True
            decoded_length = len(decoded)
        except Exception as e:
            can_decode = False
            decoded_length = 0
        
        # Vérifier si on est en développement
        is_dev = getattr(get_secret_key, '_is_development', False)
        is_prod = getattr(get_secret_key, '_is_production', False)
        
        return {
            "valid": is_valid_format and can_decode,
            "is_development_key": is_dev,
            "is_production_key": is_prod,
            "key_length": len(key),
            "decoded_length": decoded_length,
            "format_valid": is_valid_format,
            "base64_valid": can_decode,
            "warning": "Clé de développement utilisée - À changer en production" if is_dev else None
        }
        
    except Exception as e:
        return {
            "valid": False,
            "error": str(e),
            "is_development_key": False,
            "is_production_key": False
        }


def get_key_info() -> dict:
    """
    Retourne des informations sur la clé (sans l'exposer).
    """
    try:
        key_exists = bool(os.getenv("FERNET_SECRET_KEY") or os.getenv("APP_SECRET_KEY"))
        env_source = "FERNET_SECRET_KEY" if os.getenv("FERNET_SECRET_KEY") else "APP_SECRET_KEY" if os.getenv("APP_SECRET_KEY") else None
        
        validation = validate_secret_key()
        
        return {
            "configured": key_exists,
            "source": env_source,
            "environment": os.getenv("ENV", "development"),
            "validation": validation
        }
        
    except Exception as e:
        return {
            "configured": False,
            "error": str(e)
        }


# Version rétrocompatible pour l'ancien code (retourne bytes)
def get_secret_key_bytes_legacy() -> bytes:
    """
    Version legacy qui retourne bytes pour la compatibilité.
    """
    key_str = get_secret_key()
    return key_str.encode('utf-8')


# Pour la compatibilité avec l'ancienne signature
def get_secret_key_legacy() -> bytes:
    """
    Version legacy qui retourne bytes (comme l'ancienne fonction).
    """
    key = os.getenv("APP_SECRET_KEY")
    if not key:
        raise RuntimeError("APP_SECRET_KEY manquant")
    return urlsafe_b64encode(key.encode())


# ============================================================================
# TEST
# ============================================================================

def test_secrets():
    """Test unitaire du module secrets"""
    print("🧪 Test du module secrets")
    print("-" * 40)
    
    # Tester la récupération de la clé
    try:
        key = get_secret_key()
        print(f"✅ Clé récupérée: {key[:20]}... (longueur: {len(key)})")
        
        key_info = get_key_info()
        print(f"✅ Info clé: {key_info}")
        
        validation = validate_secret_key()
        print(f"✅ Validation: {validation}")
        
    except Exception as e:
        print(f"❌ Erreur: {str(e)}")


if __name__ == "__main__":
    test_secrets()
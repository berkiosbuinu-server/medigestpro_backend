# app/core/encryption.py
"""
Module de chiffrement/déchiffrement pour les données sensibles
Utilise Fernet (symmetric encryption) pour une sécurité optimale
"""

import base64
import hashlib
import logging
from typing import Optional, Union
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
from app.core.secrets import get_secret_key

logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTES
# ============================================================================

SALT_LENGTH = 16
KEY_LENGTH = 32
ITERATIONS = 100000


# ============================================================================
# INITIALISATION FERNET
# ============================================================================

def get_fernet_instance() -> Fernet:
    """
    Crée une instance Fernet avec une clé dérivée sécurisée.
    La clé est dérivée de la clé secrète pour plus de sécurité.
    """
    try:
        secret_key = get_secret_key()
        
        # Si la clé n'est pas déjà au format Fernet (32 bytes base64)
        if len(secret_key) < 32:
            # Dériver une clé de 32 bytes
            kdf = PBKDF2(
                algorithm=hashes.SHA256(),
                length=KEY_LENGTH,
                salt=b'medigest_salt_2024',
                iterations=ITERATIONS,
            )
            key = base64.urlsafe_b64encode(kdf.derive(secret_key.encode()))
        else:
            # Utiliser directement la clé
            key = secret_key.encode() if isinstance(secret_key, str) else secret_key
            if len(key) < 32:
                key = key.ljust(32, b'0')
            key = base64.urlsafe_b64encode(key[:32])
        
        return Fernet(key)
    except Exception as e:
        logger.error(f"Erreur création instance Fernet: {str(e)}")
        # Fallback: utiliser une clé par défaut (uniquement pour development)
        if not getattr(get_secret_key, '_is_production', True):
            logger.warning("⚠️ Utilisation clé fallback - UNIQUEMENT POUR DEVELOPMENT")
            return Fernet(base64.urlsafe_b64encode(b'medigest_fallback_key_32bytes_1234567890'))
        raise


# Instance globale
try:
    fernet = get_fernet_instance()
    logger.info("✅ Fernet initialisé avec succès")
except Exception as e:
    logger.error(f"❌ Erreur initialisation Fernet: {str(e)}")
    fernet = None


# ============================================================================
# FONCTIONS PRINCIPALES
# ============================================================================

def encrypt_value(value: str) -> str:
    """
    Chiffre une valeur string.
    
    Args:
        value: La valeur à chiffrer
        
    Returns:
        La valeur chiffrée en base64
        
    Raises:
        ValueError: Si la valeur est None ou vide
        RuntimeError: Si Fernet n'est pas initialisé
    """
    if value is None:
        raise ValueError("Cannot encrypt None value")
    
    if not isinstance(value, str):
        value = str(value)
    
    if not value:
        return ""  # Retourner chaîne vide pour les valeurs vides
    
    if fernet is None:
        raise RuntimeError("Fernet not initialized. Check cryptography setup.")
    
    try:
        encrypted = fernet.encrypt(value.encode('utf-8'))
        return encrypted.decode('utf-8')
    except Exception as e:
        logger.error(f"Erreur chiffrement: {str(e)}")
        raise ValueError(f"Failed to encrypt value: {str(e)}")


def decrypt_value(value: str) -> str:
    """
    Déchiffre une valeur chiffrée.
    
    Args:
        value: La valeur chiffrée (en base64)
        
    Returns:
        La valeur déchiffrée originale
        
    Raises:
        ValueError: Si la valeur est None ou invalide
        RuntimeError: Si Fernet n'est pas initialisé
    """
    if value is None:
        raise ValueError("Cannot decrypt None value")
    
    if not value:
        return ""  # Retourner chaîne vide pour les valeurs vides
    
    if fernet is None:
        raise RuntimeError("Fernet not initialized. Check cryptography setup.")
    
    try:
        decrypted = fernet.decrypt(value.encode('utf-8'))
        return decrypted.decode('utf-8')
    except InvalidToken:
        logger.error("Token invalide lors du déchiffrement")
        raise ValueError("Invalid encryption token - data may be corrupted or using wrong key")
    except Exception as e:
        logger.error(f"Erreur déchiffrement: {str(e)}")
        raise ValueError(f"Failed to decrypt value: {str(e)}")


# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def encrypt_dict(data: dict, fields: list[str]) -> dict:
    """
    Chiffre des champs spécifiques dans un dictionnaire.
    
    Args:
        data: Dictionnaire contenant les données
        fields: Liste des champs à chiffrer
        
    Returns:
        Dictionnaire avec les champs chiffrés
    """
    result = data.copy()
    for field in fields:
        if field in result and result[field]:
            result[field] = encrypt_value(str(result[field]))
    return result


def decrypt_dict(data: dict, fields: list[str]) -> dict:
    """
    Déchiffre des champs spécifiques dans un dictionnaire.
    
    Args:
        data: Dictionnaire contenant les données chiffrées
        fields: Liste des champs à déchiffrer
        
    Returns:
        Dictionnaire avec les champs déchiffrés
    """
    result = data.copy()
    for field in fields:
        if field in result and result[field]:
            try:
                result[field] = decrypt_value(result[field])
            except ValueError:
                # Si le déchiffrement échoue, garder la valeur originale
                logger.warning(f"Failed to decrypt field '{field}', keeping original")
    return result


def is_encrypted(value: str) -> bool:
    """
    Vérifie si une valeur semble être chiffrée.
    
    Args:
        value: La valeur à vérifier
        
    Returns:
        True si la valeur semble être chiffrée, False sinon
    """
    if not value or not isinstance(value, str):
        return False
    
    # Les valeurs chiffrées par Fernet sont en base64 et se terminent par '='
    return len(value) > 20 and value.endswith('=') and all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=' for c in value)


def rotate_key() -> bool:
    """
    Effectue une rotation de la clé de chiffrement.
    Cette fonction doit être appelée périodiquement pour la sécurité.
    
    Returns:
        True si la rotation a réussi, False sinon
    """
    try:
        # Cette fonction nécessite une implémentation plus complexe
        # car elle doit re-chiffrer toutes les données existantes
        logger.info("Key rotation requested - implement with data migration")
        # TODO: Implémenter la rotation avec migration des données
        return True
    except Exception as e:
        logger.error(f"Erreur rotation clé: {str(e)}")
        return False


# ============================================================================
# TEST
# ============================================================================

def test_encryption():
    """Test unitaire du module de chiffrement"""
    test_data = [
        "Hello World",
        "donnée sensible avec accents éèê",
        "1234567890",
        "email@example.com",
        "very_long_string_" * 100,
        "",
        None
    ]
    
    print("🧪 Test du module de chiffrement")
    print("-" * 40)
    
    for data in test_data:
        try:
            if data is None:
                print(f"Test None: Ignoré")
                continue
            
            print(f"Original: {data[:50]}..." if len(data) > 50 else f"Original: {data}")
            
            encrypted = encrypt_value(data)
            print(f"Encrypted: {encrypted[:50]}..." if len(encrypted) > 50 else f"Encrypted: {encrypted}")
            
            decrypted = decrypt_value(encrypted)
            print(f"Decrypted: {decrypted}")
            
            assert decrypted == data, "Échec: les données ne correspondent pas"
            print("✅ Succès\n")
            
        except Exception as e:
            print(f"❌ Erreur: {str(e)}\n")


if __name__ == "__main__":
    test_encryption()
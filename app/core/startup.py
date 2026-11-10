# app/core/startup.py

import os
import logging
from pathlib import Path
from app.core.config import settings

logger = logging.getLogger(__name__)

def setup_media_directories():
    """Crée les dossiers media nécessaires au démarrage"""
    try:
        if hasattr(settings, 'MEDIA_ROOT') and settings.MEDIA_ROOT:
            media_root = Path(settings.MEDIA_ROOT)
            media_root.mkdir(parents=True, exist_ok=True)
            
            # Créer les sous-dossiers
            receipts_dir = media_root / 'receipts'
            receipts_dir.mkdir(exist_ok=True)
            
            logos_dir = media_root / 'logos'
            logos_dir.mkdir(exist_ok=True)
            
            uploads_dir = media_root / 'uploads'
            uploads_dir.mkdir(exist_ok=True)
            
            logger.info(f"✅ Dossiers media créés: {media_root}")
            return True
        else:
            logger.warning("⚠️ MEDIA_ROOT non défini dans les settings")
            return False
    except Exception as e:
        logger.error(f"❌ Erreur création dossiers media: {e}")
        return False

def init_storage():
    """Initialise le stockage"""
    return setup_media_directories()
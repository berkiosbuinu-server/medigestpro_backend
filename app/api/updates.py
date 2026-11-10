# api/updates.py (exemple pour FastAPI)

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import json

router = APIRouter(prefix="/api/v1/updates", tags=["updates"])


class UpdateInfo(BaseModel):
    version: str
    release_date: str
    changelog: List[str]
    download_url: str
    size_mb: float
    is_critical: bool = False
    min_version_required: Optional[str] = None


class UpdateCheckResponse(BaseModel):
    update_available: bool
    current_version: str
    new_version: Optional[str] = None
    changelog: Optional[List[str]] = None
    download_url: Optional[str] = None
    size: Optional[float] = None
    release_date: Optional[str] = None
    is_critical: bool = False


# Version actuelle disponible sur le serveur
AVAILABLE_VERSIONS = {
    "stable": {
        "version": "2.1.0",
        "release_date": "2024-01-15",
        "changelog": [
            "✨ Nouveau: Interface améliorée du tableau de bord",
            "🐛 Correction: Problème de synchronisation des ventes",
            "🔧 Amélioration: Performance de la recherche produits",
            "📊 Nouveau: Graphiques de bénéfices",
            "🔒 Sécurité: Mise à jour des certificats SSL"
        ],
        "download_url": "https://backend-medigest.onrender.com/downloads/medigest_2.1.0.exe",
        "size_mb": 45.2,
        "is_critical": False,
        "min_version_required": "2.0.0"
    },
    "beta": {
        "version": "2.2.0-beta.1",
        "release_date": "2024-01-20",
        "changelog": [
            "🚀 Nouveau: Mode hors-ligne amélioré",
            "⚡ Performance: Synchronisation plus rapide",
            "🐛 Fix: Correction de bugs mineurs"
        ],
        "download_url": "https://backend-medigest.onrender.com/downloads/medigest_2.2.0-beta.exe",
        "size_mb": 46.8,
        "is_critical": False,
        "min_version_required": "2.0.0"
    },
    "dev": {
        "version": "2.3.0-dev.1",
        "release_date": "2024-01-25",
        "changelog": [
            "🔧 Version de développement - Tests en cours"
        ],
        "download_url": "https://backend-medigest.onrender.com/downloads/medigest_dev.exe",
        "size_mb": 47.5,
        "is_critical": False
    }
}


def compare_versions(v1: str, v2: str) -> int:
    """Compare deux versions. Retourne -1 si v1 < v2, 0 si égal, 1 si v1 > v2"""
    def normalize(v):
        return [int(x) for x in v.replace('-', '.').split('.')[:3]]
    
    v1_parts = normalize(v1)
    v2_parts = normalize(v2)
    
    for i in range(3):
        if i >= len(v1_parts):
            return -1
        if i >= len(v2_parts):
            return 1
        if v1_parts[i] < v2_parts[i]:
            return -1
        if v1_parts[i] > v2_parts[i]:
            return 1
    return 0


@router.get("/check", response_model=UpdateCheckResponse)
async def check_update(
    current_version: str = Query(..., description="Version actuelle de l'application"),
    channel: str = Query("stable", description="Canal de mise à jour (stable, beta, dev)")
):
    """
    Vérifie si une mise à jour est disponible.
    """
    if channel not in AVAILABLE_VERSIONS:
        channel = "stable"
    
    latest = AVAILABLE_VERSIONS[channel]
    
    # Vérifier si une mise à jour est disponible
    if compare_versions(current_version, latest["version"]) < 0:
        return UpdateCheckResponse(
            update_available=True,
            current_version=current_version,
            new_version=latest["version"],
            changelog=latest["changelog"],
            download_url=latest["download_url"],
            size=latest["size_mb"],
            release_date=latest["release_date"],
            is_critical=latest.get("is_critical", False)
        )
    
    return UpdateCheckResponse(
        update_available=False,
        current_version=current_version
    )


@router.get("/download/{version}")
async def download_update(version: str):
    """
    Télécharge la mise à jour.
    """
    for channel, info in AVAILABLE_VERSIONS.items():
        if info["version"] == version:
            # Rediriger vers le fichier
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=info["download_url"])
    
    raise HTTPException(status_code=404, detail="Version non trouvée")
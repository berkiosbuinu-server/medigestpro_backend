# app/api/endpoints/timezone.py
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
import pytz
from typing import Optional
from pydantic import BaseModel

from app.api.deps import get_current_user, get_current_tenant
from app.models.user import User
from app.models.tenant import Tenant

router = APIRouter(tags=["Timezone"])

class ServerTimeResponse(BaseModel):
    server_time_utc: str
    server_time_local: str
    timezone: str
    timezone_offset: int  # en heures
    timestamp: int  # timestamp unix

@router.get("/server-time", response_model=ServerTimeResponse)
async def get_server_time(
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant)
):
    """
    Récupère l'heure serveur avec le fuseau horaire du tenant
    """
    # Utiliser le fuseau horaire du tenant ou celui par défaut
    timezone_str = "Africa/Kinshasa"  # Par défaut RDC
    
    if current_tenant and current_tenant.settings:
        timezone_str = current_tenant.settings.get("timezone", "Africa/Kinshasa")
    
    try:
        tz = pytz.timezone(timezone_str)
    except:
        tz = pytz.UTC
        timezone_str = "UTC"
    
    now_utc = datetime.now(pytz.UTC)
    now_local = now_utc.astimezone(tz)
    
    # Calculer l'offset en heures
    offset_minutes = now_local.utcoffset().total_seconds() / 60 if now_local.utcoffset() else 0
    offset_hours = int(offset_minutes / 60)
    
    return ServerTimeResponse(
        server_time_utc=now_utc.isoformat(),
        server_time_local=now_local.isoformat(),
        timezone=timezone_str,
        timezone_offset=offset_hours,
        timestamp=int(now_utc.timestamp())
    )

@router.get("/health/time")
async def health_check_time():
    """Simple health check pour l'heure serveur"""
    return {
        "server_time": datetime.now(pytz.UTC).isoformat(),
        "status": "ok"
    }
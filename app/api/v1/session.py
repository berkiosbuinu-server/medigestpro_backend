# app/api/v1/session.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime
import secrets
import string

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.pharmacy import Pharmacy

router = APIRouter(prefix="/user", tags=["Session"])

@router.get("/current-session", status_code=status.HTTP_200_OK)
def get_current_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Récupère la session courante de l'utilisateur."""
    
    pharmacy_id = None
    pos_id = None
    pos_name = None
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_user.tenant_id,
        Pharmacy.is_active == True,
        Pharmacy.is_main == True
    ).first()
    
    if pharmacy:
        pharmacy_id = pharmacy.id
        pos_id = str(pharmacy.id)
        pos_name = pharmacy.name
    else:
        pos_id = f"POS-{str(current_user.id)[:6]}"
        pos_name = "Caisse principale"
    
    alphabet = string.digits
    session_number = ''.join(secrets.choice(alphabet) for _ in range(4))
    session_id = f"{str(current_user.id)[:6]}_{datetime.utcnow().strftime('%Y%m%d')}_{session_number}"
    
    return {
        "sessionId": session_id,
        "sessionNumber": session_number,
        "posId": pos_id,
        "posName": pos_name,
        "pharmacyId": str(pharmacy_id) if pharmacy_id else None,
        "userId": str(current_user.id),
        "userName": current_user.nom_complet,
        "userEmail": current_user.email,
        "userRole": current_user.role,
        "startedAt": datetime.utcnow().isoformat(),
        "status": "active"
    }
# app/services/subscription_code_service.py
"""
Service de gestion des codes d'abonnement.
"""

import uuid
import random
import string
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.orm import Session

from app.models.subscription_code import SubscriptionCode, SubscriptionCodeStatus
from app.models.pharmacy import Pharmacy
from app.models.user import User
from app.services.pharmacy_subscription_service import create_pharmacy_subscription


def generate_unique_code(length: int = 8) -> str:
    """
    Génère un code unique formaté XXXX-XXXX.
    """
    chars = string.ascii_uppercase + string.digits
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '')
    
    part1 = ''.join(random.choices(chars, k=4))
    part2 = ''.join(random.choices(chars, k=4))
    
    return f"{part1}-{part2}"


def create_subscription_code(
    db: Session,
    plan_type: str,
    plan_name: str,
    duration_days: int,
    created_by_user_id: uuid.UUID,
    price: int = 0,
    valid_days: int = 365,
    max_uses: int = 1,
    bulk_codes: bool = False,
    notes: Optional[str] = None
) -> SubscriptionCode:
    """
    Crée un nouveau code d'abonnement.
    """
    # Générer un code unique
    code = generate_unique_code()
    while db.query(SubscriptionCode).filter(SubscriptionCode.code == code).first():
        code = generate_unique_code()
    
    now = datetime.utcnow()
    
    subscription_code = SubscriptionCode(
        code=code,
        plan_type=plan_type,
        plan_name=plan_name,
        duration_days=duration_days,
        price=price,
        valid_from=now,
        valid_until=now + timedelta(days=valid_days),
        status=SubscriptionCodeStatus.PENDING,
        created_by_user_id=created_by_user_id,
        notes=notes,
        config={
            "max_uses": max_uses,
            "used_count": 0,
            "bulk_codes": bulk_codes,
            "created_at": now.isoformat()
        }
    )
    
    db.add(subscription_code)
    db.commit()
    db.refresh(subscription_code)
    
    return subscription_code


def activate_subscription_with_code(
    db: Session,
    code: str,
    pharmacy_id: uuid.UUID,
    user_id: uuid.UUID
) -> Dict[str, Any]:
    """
    Active un abonnement pour une pharmacie à l'aide d'un code.
    """
    # Trouver le code
    subscription_code = db.query(SubscriptionCode).filter(
        SubscriptionCode.code == code.upper().strip()
    ).first()
    
    if not subscription_code:
        raise ValueError("Code d'abonnement invalide")
    
    if not subscription_code.is_valid():
        raise ValueError("Code d'abonnement expiré ou déjà utilisé")
    
    # Vérifier la pharmacie
    pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
    if not pharmacy:
        raise ValueError("Pharmacie non trouvée")
    
    # Créer l'abonnement pour la pharmacie
    subscription = create_pharmacy_subscription(
        db=db,
        pharmacy_id=pharmacy_id,
        plan=subscription_code.plan_type,
        billing_cycle="monthly" if subscription_code.duration_days <= 30 else "yearly",
        custom_trial_days=subscription_code.duration_days if subscription_code.price == 0 else None
    )
    
    # Marquer le code comme activé
    subscription_code.mark_as_activated(pharmacy_id, user_id)
    db.commit()
    
    return {
        "success": True,
        "message": f"Abonnement {subscription_code.plan_name} activé avec succès",
        "subscription": {
            "id": str(subscription.id),
            "plan": subscription.plan.value if hasattr(subscription.plan, 'value') else str(subscription.plan),
            "plan_name": subscription.plan_name,
            "duration_days": subscription_code.duration_days,
            "end_date": subscription.end_date.isoformat()
        },
        "code": {
            "code": subscription_code.code,
            "used": True
        }
    }


def get_available_codes(
    db: Session,
    plan_type: Optional[str] = None,
    include_expired: bool = False,
    limit: int = 50,
    offset: int = 0
) -> Tuple[List[SubscriptionCode], int]:
    """
    Récupère la liste des codes disponibles.
    """
    query = db.query(SubscriptionCode)
    
    if plan_type:
        query = query.filter(SubscriptionCode.plan_type == plan_type)
    
    if not include_expired:
        query = query.filter(SubscriptionCode.status == SubscriptionCodeStatus.PENDING)
        query = query.filter(SubscriptionCode.valid_until > datetime.utcnow())
    
    total = query.count()
    codes = query.order_by(SubscriptionCode.created_at.desc()).offset(offset).limit(limit).all()
    
    return codes, total


def get_pharmacy_activated_code(
    db: Session,
    pharmacy_id: uuid.UUID
) -> Optional[SubscriptionCode]:
    """
    Récupère le code utilisé par une pharmacie.
    """
    return db.query(SubscriptionCode).filter(
        SubscriptionCode.activated_for_pharmacy_id == pharmacy_id
    ).first()
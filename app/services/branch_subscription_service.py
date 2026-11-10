# app/services/branch_subscription_service.py
"""
Service pour la gestion des abonnements par branche
"""

from datetime import datetime, timedelta
import uuid
from sqlalchemy.orm import Session
from typing import Optional
import logging

from app.models.branch_subscription import BranchSubscription, SubscriptionPlan, SubscriptionStatus
from app.models.branch import Branch

logger = logging.getLogger(__name__)


def create_trial_subscription(
    db: Session,
    branch_id: str,
    days: int = 14
) -> Optional[BranchSubscription]:
    """Crée un abonnement d'essai pour une branche"""
    
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        logger.error(f"Branche non trouvée: {branch_id}")
        return None
    
    # Vérifier si un abonnement existe déjà
    existing = db.query(BranchSubscription).filter(
        BranchSubscription.branch_id == branch_id
    ).first()
    
    if existing:
        logger.info(f"Abonnement déjà existant pour branche {branch_id}")
        return existing
    
    trial_end_date = datetime.utcnow() + timedelta(days=days)
    
    subscription = BranchSubscription(
        id=uuid.uuid4(),
        branch_id=branch_id,
        tenant_id=branch.tenant_id,
        pharmacy_id=branch.parent_pharmacy_id,
        plan=SubscriptionPlan.TRIAL.value,
        plan_name="Essai gratuit",
        start_date=datetime.utcnow(),
        end_date=trial_end_date,
        trial_end_date=trial_end_date,
        status=SubscriptionStatus.TRIAL.value,
        billing_cycle="monthly",
        price=0.0,
        auto_renew=False,
        max_products=2000,
        max_users=5,
        max_storage_mb=100
    )
    
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    
    logger.info(f"✅ Abonnement d'essai créé pour branche {branch_id}")
    return subscription


def get_branch_subscription(
    db: Session,
    branch_id: str
) -> Optional[BranchSubscription]:
    """Récupère l'abonnement d'une branche"""
    return db.query(BranchSubscription).filter(
        BranchSubscription.branch_id == branch_id
    ).first()


def is_branch_subscription_active(
    db: Session,
    branch_id: str
) -> bool:
    """Vérifie si l'abonnement de la branche est actif"""
    subscription = get_branch_subscription(db, branch_id)
    if not subscription:
        return False
    return subscription.is_active()
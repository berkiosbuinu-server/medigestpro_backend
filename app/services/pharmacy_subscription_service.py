from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from uuid import UUID

from app.models.pharmacy import Pharmacy
from app.models.pharmacy_subscription import PharmacySubscription, SubscriptionPlan, SubscriptionStatus
from app.models.user import User

# Configuration des plans
PLAN_LIMITS = {
    "trial": {
        "name": "Essai",
        "max_products": 2000,
        "max_users": 5,
        "max_branches": 0, 
        "trial_days": 14,
        "monthly_price": 0,
        "yearly_price": 0
    },
    "starter": {
        "name": "Starter",
        "max_products": 1500,
        "max_users": 5,
        "max_branches": 0, 
        "monthly_price": 5,
        "yearly_price": 48
    },
    "professional": {
        "name": "Professionnel",
        "max_products": 3000,
        "max_users": 20,
        "max_branches": 0, 
        "monthly_price": 8,
        "yearly_price": 76.8
    },
    "enterprise": {
        "name": "Entreprise",
        "max_products": 10000,
        "max_users": 20,
        "max_branches": 0, 
        "monthly_price": 15,
        "yearly_price": 144
    },
    "infinite": {
        "name": "Infinite",
        "max_products": 0,  # 0 = illimité
        "max_users": 0,    # 0 = illimité
        "max_branches": 0, 
        "monthly_price": 30,
        "yearly_price": 288
    }
}

def get_plan_limits(plan: str) -> Dict[str, Any]:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["trial"])

def create_pharmacy_subscription(
    db: Session,
    pharmacy_id: UUID,
    plan: str = "trial",
    billing_cycle: str = "monthly",
    custom_trial_days: int = None
) -> PharmacySubscription:
    limits = get_plan_limits(plan)
    now = datetime.utcnow()

    if plan == "trial":
        trial_days = custom_trial_days or limits["trial_days"]
        end_date = now + timedelta(days=trial_days)
        trial_end_date = end_date
        price = 0
    else:
        if billing_cycle == "yearly":
            end_date = now + timedelta(days=365)
            price = limits["yearly_price"]
        else:
            end_date = now + timedelta(days=30)
            price = limits["monthly_price"]
        trial_end_date = None

    subscription = PharmacySubscription(
        pharmacy_id=pharmacy_id,
        plan=plan,
        plan_name=limits["name"],
        start_date=now,
        end_date=end_date,
        trial_end_date=trial_end_date,
        status=SubscriptionStatus.ACTIVE,
        billing_cycle=billing_cycle,
        price=price,
        max_products=limits["max_products"],
        max_branches=limits.get("max_branches", 0),
        max_users=limits["max_users"]
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    return subscription

def check_pharmacy_subscription(
    db: Session,
    pharmacy_id: UUID,
    raise_if_inactive: bool = True
) -> Dict[str, Any]:
    pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
    if not pharmacy:
        raise ValueError("Pharmacie non trouvée")

    if not pharmacy.subscription:
        if raise_if_inactive:
            raise ValueError("Cette pharmacie n'a pas d'abonnement actif")
        return {"has_subscription": False, "is_active": False}

    sub = pharmacy.subscription
    is_active = sub.is_active()

    if not is_active and raise_if_inactive:
        raise ValueError(f"Abonnement expiré depuis {sub.days_remaining()} jours")

    limits = get_plan_limits(sub.plan)
    
    return {
        "has_subscription": True,
        "is_active": is_active,
        "plan": sub.plan,
        "plan_name": sub.plan_name,
        "max_products": sub.max_products,
        "max_users": sub.max_users,
        "is_unlimited_products": sub.max_products == 0,
        "is_unlimited_users": sub.max_users == 0,
        "days_remaining": sub.days_remaining(),
        "end_date": sub.end_date,
        "billing_cycle": sub.billing_cycle,
        "price": sub.price
    }

def can_add_branch(db: Session, pharmacy_id: UUID) -> bool:
    """Vérifie si une pharmacie peut ajouter une nouvelle branche"""
    sub_status = check_pharmacy_subscription(db, pharmacy_id, raise_if_inactive=False)
    
    if not sub_status["is_active"]:
        return False
    
    max_branches = sub_status.get("max_branches", 0)
    
    # 0 = illimité
    if max_branches == 0:
        return True
    
    from app.models.branch import Branch
    current_count = db.query(Branch).filter(
        Branch.parent_pharmacy_id == pharmacy_id,
        Branch.is_active == True
    ).count()
    
    return current_count < max_branches

def can_add_product(db: Session, pharmacy_id: UUID) -> bool:
    sub_status = check_pharmacy_subscription(db, pharmacy_id, raise_if_inactive=False)
    if not sub_status["is_active"]:
        return False
    
    if sub_status["is_unlimited_products"]:
        return True
    
    from app.models.product import Product
    current_count = db.query(Product).filter(Product.pharmacy_id == pharmacy_id).count()
    return current_count < sub_status["max_products"]

def can_add_user_to_pharmacy(db: Session, pharmacy_id: UUID) -> bool:
    sub_status = check_pharmacy_subscription(db, pharmacy_id, raise_if_inactive=False)
    if not sub_status["is_active"]:
        return False
    
    if sub_status["is_unlimited_users"]:
        return True
    
    from app.models.user_pharmacy import UserPharmacy
    current_count = db.query(UserPharmacy).filter(UserPharmacy.pharmacy_id == pharmacy_id).count()
    return current_count < sub_status["max_users"]
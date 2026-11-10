# app/services/branch_subscription_utils.py
"""
Utilitaires pour les abonnements de branches (sans dépendances circulaires)
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.branch import Branch
from app.models.branch_subscription import BranchSubscription
from app.models.user import User


PLAN_CONFIG = {
    "trial": {
        "name": "Essai",
        "price_monthly": 0,
        "price_yearly": 0,
        "max_products": 2000,
        "max_users": 5,
        "max_storage_mb": 100,
        "trial_days": 14,
        "features": [
            "5 Utilisateurs",
            "2000 Produits",
            "14 jours d'essai",
            "Support prioritaire"
        ]
    },
    "starter": {
        "name": "Starter",
        "price_monthly": 5,
        "price_yearly": 48,
        "max_products": 3000,
        "max_users": 5,
        "max_storage_mb": 500,
        "features": [
            "5 Utilisateurs",
            "1500 Produits",
            "Support email"
        ]
    },
    "professional": {
        "name": "Professionnel",
        "price_monthly": 8,
        "price_yearly": 76.8,
        "max_products": 4000,
        "max_users": 20,
        "max_storage_mb": 2000,
        "features": [
            "20 Utilisateurs",
            "3000 Produits",
            "Transferts inter-stocks",
            "Support prioritaire"
        ]
    },
    "enterprise": {
        "name": "Entreprise",
        "price_monthly": 15,
        "price_yearly": 144,
        "max_products": 15000,
        "max_users": 20,
        "max_storage_mb": 5000,
        "features": [
            "20 Utilisateurs",
            "10000 Produits",
            "API d'inventaire",
            "Support 24/7"
        ]
    },
    "infinite": {
        "name": "Infinite",
        "price_monthly": 30,
        "price_yearly": 288,
        "max_products": 0,
        "max_users": 0,
        "max_storage_mb": 10000,
        "features": [
            "Utilisateurs illimités",
            "Produits illimités",
            "Multi-dépôts",
            "Support dédié"
        ]
    }
}


def get_plan_config(plan_type: str) -> Dict[str, Any]:
    """Récupère la configuration d'un plan."""
    return PLAN_CONFIG.get(plan_type, PLAN_CONFIG["trial"])


def get_branch_subscription_by_id(db: Session, branch_id: UUID) -> Optional[BranchSubscription]:
    """Récupère l'abonnement d'une branche par son ID."""
    return db.query(BranchSubscription).filter(
        BranchSubscription.branch_id == branch_id
    ).first()


def format_unlimited(value: int) -> str:
    """Formate une valeur illimitée."""
    if value == 0:
        return "Illimité"
    return str(value)


def safe_percentage(current: int, limit: int) -> float:
    if limit <= 0:
        return 0.0
    return round((current / limit) * 100, 2)


def get_branch_limits_from_subscription(db: Session, branch_id: UUID) -> Dict[str, Any]:
    """
    Récupère les limites d'une branche à partir de son abonnement.
    Sans dépendance à get_current_active_user.
    """
    from app.models.branch_subscription import SubscriptionStatus
    
    if not branch_id:
        return {
            "has_subscription": False,
            "is_active": False,
            "plan": "none",
            "plan_name": "Aucun",
            "max_products": 0,
            "max_users": 0,
            "max_storage_mb": 0,
            "is_unlimited_products": False,
            "is_unlimited_users": False,
            "is_unlimited_storage": False,
            "access_mode": "read_only"
        }
    
    try:
        subscription = get_branch_subscription_by_id(db, branch_id)
        
        if not subscription:
            return {
                "has_subscription": False,
                "is_active": False,
                "plan": "none",
                "plan_name": "Aucun",
                "max_products": 0,
                "max_users": 0,
                "max_storage_mb": 0,
                "is_unlimited_products": False,
                "is_unlimited_users": False,
                "is_unlimited_storage": False,
                "access_mode": "read_only"
            }
        
        # Vérifier si l'abonnement est actif
        now = datetime.utcnow()
        is_active = subscription.status == SubscriptionStatus.ACTIVE and subscription.end_date > now
        
        # Récupérer le nom du plan
        plan_value = subscription.plan.value if hasattr(subscription.plan, 'value') else str(subscription.plan)
        plan_config = get_plan_config(plan_value)
        
        return {
            "has_subscription": True,
            "is_active": is_active,
            "plan": plan_value,
            "plan_name": subscription.plan_name or plan_config.get("name", plan_value.capitalize()),
            "max_products": subscription.max_products if subscription.max_products > 0 else 0,
            "max_users": subscription.max_users if subscription.max_users > 0 else 0,
            "max_storage_mb": subscription.max_storage_mb if subscription.max_storage_mb > 0 else 0,
            "is_unlimited_products": subscription.max_products == 0,
            "is_unlimited_users": subscription.max_users == 0,
            "is_unlimited_storage": subscription.max_storage_mb == 0,
            "price": subscription.price,
            "billing_cycle": subscription.billing_cycle,
            "end_date": subscription.end_date,
            "days_remaining": (subscription.end_date - now).days if subscription.end_date and subscription.end_date > now else 0,
            "features": plan_config.get("features", []),
            "access_mode": "full" if is_active else "read_only"
        }
        
    except Exception:
        return {
            "has_subscription": False,
            "is_active": False,
            "plan": "error",
            "plan_name": "Erreur",
            "max_products": 0,
            "max_users": 0,
            "max_storage_mb": 0,
            "is_unlimited_products": False,
            "is_unlimited_users": False,
            "is_unlimited_storage": False,
            "access_mode": "read_only"
        }


from datetime import datetime
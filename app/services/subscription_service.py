# app/services/subscription_service.py
"""
Service de gestion des abonnements.
Gère à la fois:
- UserSubscription: Abonnement individuel par utilisateur
- TenantSubscription: Abonnement du tenant/pharmacie
"""
import logging
import random
import string
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union, Tuple
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.models.subscription import Subscription as TenantSubscription
from app.models.user_subscription import UserSubscription
from app.models.subscription_code import SubscriptionCode, SubscriptionCodeStatus
from app.models.user import User
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION DES PLANS
# ============================================================================

PLAN_CONFIG = {
    "free": {
        "name": "Gratuit",
        "price_monthly": 0,
        "price_yearly": 0,
        "max_users_per_tenant": 1,
        "max_products": 100,
        "max_pharmacies": 1,
        "features": [
            "Gestion de stock de base",
            "Ventes POS",
            "Rapports quotidiens"
        ]
    },
    "starter": {
        "name": "Starter",
        "price_monthly": 5,
        "price_yearly": 48,
        "max_users_per_tenant": 1,
        "max_products": 500,
        "max_pharmacies": 1,
        "features": [
            "1 Utilisateur",
            "Gestion de stock de base",
            "Ventes POS",
            "Rapports quotidiens"
        ]
    },
    "pro": {
        "name": "Pro",
        "price_monthly": 8,
        "price_yearly": 76.8,
        "max_users_per_tenant": 5,
        "max_products": 2000,
        "max_pharmacies": 3,
        "features": [
            "5 Utilisateurs",
            "Transferts inter-stocks",
            "Gestion des dettes",
            "PWA (Mode Offline)",
            "Support Prioritaire"
        ]
    },
    "enterprise": {
        "name": "Entreprise",
        "price_monthly": 15,
        "price_yearly": 144,
        "max_users_per_tenant": 0,
        "max_products": 0,
        "max_pharmacies": 0,
        "features": [
            "Utilisateurs illimités",
            "Analytique avancée",
            "API d'inventaire",
            "Audit Logs complets",
            "Gestion multi-dépôts"
        ]
    },
    "trial": {
        "name": "Essai",
        "price_monthly": 0,
        "price_yearly": 0,
        "max_users_per_tenant": 5,
        "max_products": 500,
        "max_pharmacies": 1,
        "features": [
            "5 Utilisateurs",
            "Gestion de stock avancée",
            "Ventes POS",
            "Rapports quotidiens",
            "Période d'essai de 14 jours"
        ],
        "trial_days": 14
    }
}


# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def generate_subscription_code(length: int = 8) -> str:
    """Génère un code d'abonnement unique formaté XXXX-XXXX."""
    chars = string.ascii_uppercase + string.digits
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '')
    
    part1 = ''.join(random.choices(chars, k=4))
    part2 = ''.join(random.choices(chars, k=4))
    
    return f"{part1}-{part2}"


def get_plan_config(plan_type: str) -> Dict[str, Any]:
    """Récupère la configuration d'un plan."""
    return PLAN_CONFIG.get(plan_type, PLAN_CONFIG["free"])


def format_unlimited(value: Union[int, str]) -> Union[int, str]:
    """Formate une valeur pour l'affichage (transforme 0 en 'Illimité')."""
    if isinstance(value, int) and value == 0:
        return "Illimité"
    if isinstance(value, str) and value.lower() == "illimité":
        return "Illimité"
    return value


def is_unlimited(value: Union[int, str]) -> bool:
    """Vérifie si une valeur représente 'Illimité'."""
    if isinstance(value, str):
        return value.lower() == "illimité"
    return value == 0


def safe_percentage(current: int, limit: int) -> float:
    """Calcule un pourcentage de manière sécurisée."""
    if limit <= 0:
        return 0.0
    return round((current / limit) * 100, 2)


# ============================================================================
# FONCTIONS DE GESTION DES ABONNEMENTS UTILISATEUR (UserSubscription)
# ============================================================================

def get_user_subscription(db: Session, user_id: Union[str, UUID]) -> Optional[UserSubscription]:
    """Récupère l'abonnement d'un utilisateur."""
    if isinstance(user_id, str):
        user_id = UUID(user_id)
    
    return db.query(UserSubscription).filter(
        UserSubscription.user_id == user_id
    ).first()


def check_user_subscription(db: Session, user_id: Union[str, UUID]) -> Dict[str, Any]:
    """
    Vérifie le statut de l'abonnement d'un utilisateur.
    Retourne un dictionnaire avec les informations.
    """
    if isinstance(user_id, str):
        user_id = UUID(user_id)
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {
            "has_subscription": False,
            "is_active": False,
            "plan": "free",
            "status": "no_user",
            "message": "Utilisateur non trouvé"
        }

    subscription = get_user_subscription(db, user_id)

    if not subscription:
        return {
            "has_subscription": False,
            "is_active": False,
            "plan": "free",
            "status": "no_subscription",
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id) if user.tenant_id else None
        }

    now = datetime.utcnow()
    is_active = subscription.status == "active" and (
        subscription.end_date is None or subscription.end_date > now
    )

    days_remaining = 0
    if subscription.end_date and is_active:
        days_remaining = (subscription.end_date - now).days

    return {
        "has_subscription": True,
        "is_active": is_active,
        "plan": subscription.plan_type,
        "plan_name": subscription.plan_name,
        "status": subscription.status,
        "start_date": subscription.start_date.isoformat() if subscription.start_date else None,
        "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
        "days_remaining": max(0, days_remaining),
        "trial_end_date": subscription.trial_end_date.isoformat() if subscription.trial_end_date else None,
        "auto_renew": subscription.auto_renew,
        "billing_cycle": subscription.billing_cycle,
        "price": float(subscription.price or 0),
        "currency": subscription.currency or "EUR",
        "user_id": str(user.id),
        "tenant_id": str(user.tenant_id) if user.tenant_id else None
    }


def create_user_subscription(
    db: Session,
    user_id: Union[str, UUID],
    tenant_id: Optional[UUID] = None,
    plan_type: str = "trial"
) -> UserSubscription:
    """Crée un abonnement pour un utilisateur."""
    if isinstance(user_id, str):
        user_id = UUID(user_id)
    
    plan_config = get_plan_config(plan_type)
    trial_days = plan_config.get("trial_days", 14)
    
    now = datetime.utcnow()
    
    if plan_type == "trial":
        end_date = now + timedelta(days=trial_days)
        trial_end_date = end_date
    else:
        end_date = now + timedelta(days=30)
        trial_end_date = None

    subscription = UserSubscription(
        user_id=user_id,
        tenant_id=tenant_id,
        plan_type=plan_type,
        plan_name=plan_config["name"],
        status="active",
        start_date=now,
        end_date=end_date,
        trial_end_date=trial_end_date,
        price=plan_config.get("price_monthly", 0),
        currency="EUR",
        billing_cycle="monthly",
        auto_renew=True,
        max_users=plan_config.get("max_users_per_tenant", 1),
        max_products=plan_config.get("max_products", 100),
        max_pharmacies=plan_config.get("max_pharmacies", 1),
        config={
            "created_at": now.isoformat(),
            "plan_config": plan_config
        }
    )

    db.add(subscription)
    db.commit()
    db.refresh(subscription)

    logger.info(f"Abonnement utilisateur créé pour l'utilisateur {user_id} avec le plan {plan_type}")
    return subscription


def upgrade_user_subscription(
    db: Session,
    user_id: Union[str, UUID],
    new_plan: str,
    billing_cycle: str = "monthly",
    payment_id: Optional[str] = None,
    payment_method: Optional[str] = None,
    manual_activation: bool = False,
    activated_by: Optional[str] = None
) -> UserSubscription:
    """Met à niveau l'abonnement d'un utilisateur."""
    if isinstance(user_id, str):
        user_id = UUID(user_id)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"Utilisateur {user_id} non trouvé")

    plan_config = get_plan_config(new_plan)
    
    price_key = f"price_{billing_cycle}"
    price = plan_config.get(price_key, 0)

    now = datetime.utcnow()
    
    if billing_cycle == "yearly":
        end_date = now + timedelta(days=365)
    else:
        end_date = now + timedelta(days=30)

    existing = get_user_subscription(db, user_id)

    if existing:
        existing.plan_type = new_plan
        existing.plan_name = plan_config["name"]
        existing.status = "active"
        existing.start_date = now
        existing.end_date = end_date
        existing.price = price
        existing.billing_cycle = billing_cycle
        existing.trial_end_date = None
        existing.max_users = plan_config.get("max_users_per_tenant", 1)
        existing.max_products = plan_config.get("max_products", 100)
        existing.max_pharmacies = plan_config.get("max_pharmacies", 1)
        
        config = existing.config or {}
        config.update({
            "upgraded_at": now.isoformat(),
            "previous_plan": existing.plan_type,
            "payment_id": payment_id,
            "payment_method": payment_method,
            "manual_activation": manual_activation,
            "activated_by": activated_by
        })
        existing.config = config
        
        subscription = existing
    else:
        subscription = UserSubscription(
            user_id=user_id,
            tenant_id=user.tenant_id,
            plan_type=new_plan,
            plan_name=plan_config["name"],
            status="active",
            start_date=now,
            end_date=end_date,
            price=price,
            currency="EUR",
            billing_cycle=billing_cycle,
            auto_renew=True,
            max_users=plan_config.get("max_users_per_tenant", 1),
            max_products=plan_config.get("max_products", 100),
            max_pharmacies=plan_config.get("max_pharmacies", 1),
            config={
                "created_at": now.isoformat(),
                "payment_id": payment_id,
                "payment_method": payment_method,
                "manual_activation": manual_activation,
                "activated_by": activated_by
            }
        )
        db.add(subscription)

    db.commit()
    db.refresh(subscription)

    logger.info(f"Abonnement utilisateur mis à niveau pour l'utilisateur {user_id} vers {new_plan}")
    return subscription


def renew_user_subscription(
    db: Session,
    user_id: Union[str, UUID],
    billing_cycle: Optional[str] = None
) -> UserSubscription:
    """Renouvelle l'abonnement d'un utilisateur."""
    if isinstance(user_id, str):
        user_id = UUID(user_id)

    subscription = get_user_subscription(db, user_id)
    if not subscription:
        raise ValueError("L'utilisateur n'a pas d'abonnement à renouveler")

    now = datetime.utcnow()
    cycle = billing_cycle or subscription.billing_cycle
    
    if cycle == "yearly":
        end_date = now + timedelta(days=365)
    else:
        end_date = now + timedelta(days=30)

    subscription.start_date = now
    subscription.end_date = end_date
    subscription.status = "active"
    subscription.auto_renew = True
    
    config = subscription.config or {}
    config["renewed_at"] = now.isoformat()
    subscription.config = config

    db.commit()
    db.refresh(subscription)

    logger.info(f"Abonnement renouvelé pour l'utilisateur {user_id} jusqu'au {end_date}")
    return subscription


def cancel_user_subscription(
    db: Session,
    user_id: Union[str, UUID]
) -> UserSubscription:
    """Annule l'abonnement d'un utilisateur."""
    if isinstance(user_id, str):
        user_id = UUID(user_id)

    subscription = get_user_subscription(db, user_id)
    if not subscription:
        raise ValueError("L'utilisateur n'a pas d'abonnement")

    subscription.status = "cancelled"
    subscription.auto_renew = False
    subscription.cancelled_at = datetime.utcnow()
    
    config = subscription.config or {}
    config["cancelled_at"] = datetime.utcnow().isoformat()
    subscription.config = config

    db.commit()
    db.refresh(subscription)

    logger.info(f"Abonnement annulé pour l'utilisateur {user_id}")
    return subscription


def get_user_subscription_limits(
    db: Session,
    user_id: Union[str, UUID]
) -> Dict[str, Any]:
    """Récupère les limites de l'abonnement d'un utilisateur."""
    subscription = get_user_subscription(db, user_id)
    
    if not subscription:
        plan_config = get_plan_config("free")
        return {
            "has_subscription": False,
            "plan": "free",
            "plan_name": "Gratuit",
            "max_users": 1,
            "max_products": 100,
            "max_pharmacies": 1,
            "features": plan_config.get("features", [])
        }
    
    return {
        "has_subscription": True,
        "plan": subscription.plan_type,
        "plan_name": subscription.plan_name,
        "max_users": subscription.max_users,
        "max_products": subscription.max_products,
        "max_pharmacies": subscription.max_pharmacies,
        "features": get_plan_config(subscription.plan_type).get("features", [])
    }


# ============================================================================
# FONCTIONS DE GESTION DES ABONNEMENTS TENANT (TenantSubscription)
# ============================================================================

def get_tenant_subscription(db: Session, tenant_id: Union[str, UUID]) -> Optional[TenantSubscription]:
    """Récupère l'abonnement d'un tenant."""
    if isinstance(tenant_id, str):
        tenant_id = UUID(tenant_id)
    
    return db.query(TenantSubscription).filter(
        TenantSubscription.tenant_id == tenant_id
    ).first()


def get_tenant_subscription_by_admin(
    db: Session,
    tenant_id: Union[str, UUID]
) -> Tuple[Optional[UserSubscription], Optional[TenantSubscription]]:
    """
    Récupère les abonnements d'un tenant via son admin.
    Retourne (user_subscription, tenant_subscription).
    """
    if isinstance(tenant_id, str):
        tenant_id = UUID(tenant_id)
    
    admin = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.role == "admin",
        User.actif.is_(True)
    ).first()
    
    user_sub = None
    if admin:
        user_sub = get_user_subscription(db, admin.id)
    
    tenant_sub = get_tenant_subscription(db, tenant_id)
    
    return user_sub, tenant_sub


def check_tenant_subscription(
    db: Session,
    tenant_id: Union[str, UUID]
) -> Dict[str, Any]:
    """
    Vérifie le statut de l'abonnement d'un tenant.
    Priorise l'abonnement de l'admin (UserSubscription) s'il existe.
    """
    if isinstance(tenant_id, str):
        tenant_id = UUID(tenant_id)
    
    user_sub, tenant_sub = get_tenant_subscription_by_admin(db, tenant_id)
    
    # Priorité à l'abonnement de l'admin (UserSubscription)
    if user_sub:
        return {
            "type": "user_subscription",
            "has_subscription": True,
            "is_active": user_sub.is_active(),
            "plan": user_sub.plan_type,
            "plan_name": user_sub.plan_name,
            "status": user_sub.status,
            "start_date": user_sub.start_date.isoformat() if user_sub.start_date else None,
            "end_date": user_sub.end_date.isoformat() if user_sub.end_date else None,
            "days_remaining": user_sub.days_remaining(),
            "billing_cycle": user_sub.billing_cycle,
            "price": float(user_sub.price or 0),
            "currency": user_sub.currency or "EUR"
        }
    
    # Sinon, utiliser TenantSubscription
    if tenant_sub:
        is_active = tenant_sub.is_active()
        return {
            "type": "tenant_subscription",
            "has_subscription": True,
            "is_active": is_active,
            "plan": tenant_sub.plan.value if hasattr(tenant_sub.plan, 'value') else str(tenant_sub.plan),
            "plan_name": tenant_sub.plan_name,
            "status": tenant_sub.status.value if hasattr(tenant_sub.status, 'value') else str(tenant_sub.status),
            "start_date": tenant_sub.start_date.isoformat() if tenant_sub.start_date else None,
            "end_date": tenant_sub.end_date.isoformat() if tenant_sub.end_date else None,
            "days_remaining": tenant_sub.days_remaining(),
            "billing_cycle": tenant_sub.billing_period.value if hasattr(tenant_sub.billing_period, 'value') else str(tenant_sub.billing_period),
            "price": float(tenant_sub.current_price or 0),
            "currency": "EUR"
        }
    
    return {
        "type": "none",
        "has_subscription": False,
        "is_active": False,
        "plan": "free",
        "plan_name": "Gratuit",
        "message": "Aucun abonnement trouvé pour ce tenant"
    }


def create_tenant_subscription(
    db: Session,
    tenant_id: Union[str, UUID],
    plan_type: str = "starter",
    created_by: Optional[UUID] = None
) -> TenantSubscription:
    """Crée un abonnement pour un tenant."""
    if isinstance(tenant_id, str):
        tenant_id = UUID(tenant_id)
    
    plan_config = get_plan_config(plan_type)
    now = datetime.utcnow()
    end_date = now + timedelta(days=30)

    subscription_code = f"TEN-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

    subscription = TenantSubscription(
        tenant_id=tenant_id,
        subscription_code=subscription_code,
        plan=plan_type,
        plan_name=plan_config["name"],
        billing_period="mensuel",
        status="active",
        monthly_price=plan_config.get("price_monthly", 0),
        annual_price=plan_config.get("price_yearly", 0),
        current_price=plan_config.get("price_monthly", 0),
        start_date=now,
        end_date=end_date,
        max_users=plan_config.get("max_users_per_tenant", 1),
        max_products=plan_config.get("max_products", 100),
        auto_renew=True,
        created_by=created_by,
        features=str(plan_config.get("features", []))
    )

    db.add(subscription)
    db.commit()
    db.refresh(subscription)

    logger.info(f"Abonnement tenant créé pour le tenant {tenant_id} avec le plan {plan_type}")
    return subscription


def upgrade_tenant_subscription(
    db: Session,
    tenant_id: Union[str, UUID],
    new_plan: str,
    billing_cycle: str = "monthly",
    payment_id: Optional[str] = None
) -> TenantSubscription:
    """Met à niveau l'abonnement d'un tenant."""
    if isinstance(tenant_id, str):
        tenant_id = UUID(tenant_id)
    
    subscription = get_tenant_subscription(db, tenant_id)
    if not subscription:
        raise ValueError(f"Aucun abonnement trouvé pour le tenant {tenant_id}")
    
    plan_config = get_plan_config(new_plan)
    now = datetime.utcnow()
    
    if billing_cycle == "yearly":
        end_date = now + timedelta(days=365)
        current_price = plan_config.get("price_yearly", 0)
    else:
        end_date = now + timedelta(days=30)
        current_price = plan_config.get("price_monthly", 0)
    
    subscription.plan = new_plan
    subscription.plan_name = plan_config["name"]
    subscription.current_price = current_price
    subscription.end_date = end_date
    subscription.start_date = now
    subscription.max_users = plan_config.get("max_users_per_tenant", 1)
    subscription.max_products = plan_config.get("max_products", 100)
    
    db.commit()
    db.refresh(subscription)
    
    logger.info(f"Abonnement tenant mis à niveau pour {tenant_id} vers {new_plan}")
    return subscription


# ============================================================================
# FONCTIONS DE VÉRIFICATION DES LIMITES
# ============================================================================

def check_tenant_limits(db: Session, tenant_id: Union[str, UUID]) -> Dict[str, Any]:
    """
    Vérifie les limites d'un tenant par rapport à son abonnement.
    Priorise l'abonnement de l'admin (UserSubscription) s'il existe.
    """
    from app.models.product import Product
    from app.models.pharmacy import Pharmacy

    if isinstance(tenant_id, str):
        tenant_id = UUID(tenant_id)

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        return {"error": "Tenant non trouvé"}

    # Récupérer les abonnements
    user_sub, tenant_sub = get_tenant_subscription_by_admin(db, tenant_id)
    
    # Compter les utilisateurs actifs
    users_count = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.actif.is_(True)
    ).count()

    # Compter les produits
    products_count = db.query(Product).filter(
        Product.tenant_id == tenant_id
    ).count()

    # Compter les pharmacies
    pharmacies_count = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant_id
    ).count()

    # Déterminer le plan et les limites
    plan_type = "free"
    plan_config = PLAN_CONFIG["free"]
    subscription_type = "none"

    if user_sub:
        plan_type = user_sub.plan_type
        plan_config = get_plan_config(plan_type)
        subscription_type = "user"
    elif tenant_sub:
        plan_type = tenant_sub.plan.value if hasattr(tenant_sub.plan, 'value') else str(tenant_sub.plan)
        plan_config = get_plan_config(plan_type)
        subscription_type = "tenant"

    max_users = plan_config.get("max_users_per_tenant", 1)
    max_products = plan_config.get("max_products", 100)
    max_pharmacies = plan_config.get("max_pharmacies", 1)

    return {
        "tenant_id": str(tenant_id),
        "subscription_type": subscription_type,
        "has_subscription": subscription_type != "none",
        "plan": plan_type,
        "plan_name": plan_config["name"],
        "current": {
            "users": users_count,
            "products": products_count,
            "pharmacies": pharmacies_count
        },
        "limits": {
            "users": format_unlimited(max_users),
            "products": format_unlimited(max_products),
            "pharmacies": format_unlimited(max_pharmacies)
        },
        "percentages": {
            "users": safe_percentage(users_count, max_users),
            "products": safe_percentage(products_count, max_products),
            "pharmacies": safe_percentage(pharmacies_count, max_pharmacies)
        },
        "exceeded": {
            "users": users_count > max_users if max_users > 0 else False,
            "products": products_count > max_products if max_products > 0 else False,
            "pharmacies": pharmacies_count > max_pharmacies if max_pharmacies > 0 else False
        }
    }


def check_user_limits(
    db: Session,
    user_id: Union[str, UUID],
    resource_type: str = "products"
) -> Dict[str, Any]:
    """
    Vérifie les limites d'un utilisateur spécifique.
    Utile pour les vérifications avant création/modification.
    """
    if isinstance(user_id, str):
        user_id = UUID(user_id)
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {"error": "Utilisateur non trouvé"}
    
    # Récupérer les limites du plan
    limits = get_user_subscription_limits(db, user_id)
    
    if resource_type == "users":
        current = db.query(User).filter(
            User.tenant_id == user.tenant_id,
            User.actif.is_(True)
        ).count()
        max_value = limits.get("max_users", 1)
    elif resource_type == "products":
        from app.models.product import Product
        current = db.query(Product).filter(
            Product.tenant_id == user.tenant_id
        ).count()
        max_value = limits.get("max_products", 100)
    elif resource_type == "pharmacies":
        from app.models.pharmacy import Pharmacy
        current = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == user.tenant_id
        ).count()
        max_value = limits.get("max_pharmacies", 1)
    else:
        return {"error": f"Type de ressource inconnu: {resource_type}"}
    
    can_create = max_value == 0 or current < max_value
    remaining = format_unlimited(max(0, max_value - current) if max_value > 0 else 0)
    
    return {
        "resource_type": resource_type,
        "current": current,
        "max": format_unlimited(max_value),
        "can_create": can_create,
        "remaining": remaining,
        "percentage": safe_percentage(current, max_value),
        "has_subscription": limits.get("has_subscription", False),
        "plan": limits.get("plan", "free"),
        "plan_name": limits.get("plan_name", "Gratuit")
    }


def can_user_access_feature(user: User, feature: str) -> bool:
    """
    Vérifie si un utilisateur peut accéder à une fonctionnalité.
    """
    if not user.user_subscription:
        return False

    plan_config = get_plan_config(user.user_subscription.plan_type)
    features = plan_config.get("features", [])
    
    return any(feature.lower() in f.lower() for f in features)


# ============================================================================
# FONCTIONS DE COMPATIBILITÉ (Alias)
# ============================================================================

def upgrade_subscription(
    db: Session,
    user_id: Union[str, UUID],
    new_plan: str,
    billing_cycle: str = "monthly",
    payment_id: Optional[str] = None,
    payment_method: Optional[str] = None,
    manual_activation: bool = False,
    activated_by: Optional[str] = None
) -> UserSubscription:
    """
    Alias pour upgrade_user_subscription.
    Utilisé par subscriptions.py pour la compatibilité.
    """
    return upgrade_user_subscription(
        db=db,
        user_id=user_id,
        new_plan=new_plan,
        billing_cycle=billing_cycle,
        payment_id=payment_id,
        payment_method=payment_method,
        manual_activation=manual_activation,
        activated_by=activated_by
    )


def create_trial_subscription(
    db: Session,
    user_id: Union[str, UUID],
    tenant_id: Optional[UUID] = None
) -> UserSubscription:
    """Crée un abonnement d'essai pour un utilisateur."""
    return create_user_subscription(
        db=db,
        user_id=user_id,
        tenant_id=tenant_id,
        plan_type="trial"
    )


def check_subscription_status(db: Session, user_id: str) -> Dict[str, Any]:
    """Alias pour check_user_subscription."""
    return check_user_subscription(db, user_id)


def get_subscription_usage(db: Session, user_id: str) -> Dict[str, Any]:
    """Alias pour get_user_subscription_usage."""
    return get_user_subscription_usage(db, user_id)


# ============================================================================
# FONCTIONS DE STATISTIQUES ET RAPPORTS
# ============================================================================

def get_user_subscription_usage(db: Session, user_id: Union[str, UUID]) -> Dict[str, Any]:
    """
    Récupère les statistiques d'utilisation de l'abonnement d'un utilisateur.
    """
    from app.models.product import Product
    from app.models.pharmacy import Pharmacy

    if isinstance(user_id, str):
        user_id = UUID(user_id)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {"error": "Utilisateur non trouvé"}

    if not user.tenant_id:
        return {
            "current_products": 0,
            "max_products": "Illimité",
            "usage_percentage": 0,
            "remaining_products": "Illimité",
            "current_users": 1,
            "max_users": 1,
            "users_usage_percentage": 0,
            "remaining_users": 0,
            "current_pharmacies": 0,
            "max_pharmacies": 1,
            "pharmacies_usage_percentage": 0,
            "remaining_pharmacies": 1
        }

    products_count = db.query(Product).filter(
        Product.tenant_id == user.tenant_id
    ).count()

    users_count = db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.actif.is_(True)
    ).count()

    pharmacies_count = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == user.tenant_id
    ).count()

    if user.user_subscription:
        plan_config = get_plan_config(user.user_subscription.plan_type)
        max_products = plan_config.get("max_products", 100)
        max_users = plan_config.get("max_users_per_tenant", 1)
        max_pharmacies = plan_config.get("max_pharmacies", 1)
        subscription_info = {
            "plan_name": user.user_subscription.plan_name,
            "plan_type": user.user_subscription.plan_type,
            "status": user.user_subscription.status,
            "price": float(user.user_subscription.price or 0),
            "currency": user.user_subscription.currency or "EUR",
            "billing_cycle": user.user_subscription.billing_cycle,
            "current_period_start": user.user_subscription.start_date.isoformat() if user.user_subscription.start_date else None,
            "current_period_end": user.user_subscription.end_date.isoformat() if user.user_subscription.end_date else None,
        }
    else:
        max_products = 100
        max_users = 1
        max_pharmacies = 1
        subscription_info = None

    return {
        "current_products": products_count,
        "max_products": format_unlimited(max_products),
        "usage_percentage": safe_percentage(products_count, max_products),
        "remaining_products": format_unlimited(max(0, max_products - products_count) if max_products > 0 else 0),
        
        "current_users": users_count,
        "max_users": format_unlimited(max_users),
        "users_usage_percentage": safe_percentage(users_count, max_users),
        "remaining_users": format_unlimited(max(0, max_users - users_count) if max_users > 0 else 0),
        
        "current_pharmacies": pharmacies_count,
        "max_pharmacies": format_unlimited(max_pharmacies),
        "pharmacies_usage_percentage": safe_percentage(pharmacies_count, max_pharmacies),
        "remaining_pharmacies": format_unlimited(max(0, max_pharmacies - pharmacies_count) if max_pharmacies > 0 else 0),
        
        "subscription": subscription_info
    }


def get_available_plans(include_trial: bool = False) -> List[Dict[str, Any]]:
    """Récupère la liste des plans disponibles."""
    plans = []
    
    for key, config in PLAN_CONFIG.items():
        if key == "trial" and not include_trial:
            continue
            
        plans.append({
            "id": key,
            "name": config["name"],
            "type": key,
            "price": config["price_monthly"],
            "price_monthly": config["price_monthly"],
            "price_yearly": config["price_yearly"],
            "max_users": format_unlimited(config.get("max_users_per_tenant", 0)),
            "max_products": format_unlimited(config.get("max_products", 0)),
            "max_pharmacies": format_unlimited(config.get("max_pharmacies", 0)),
            "features": config.get("features", []),
            "is_popular": key == "pro",
            "description": f"Plan {config['name']}",
            "billing_cycle": "monthly"
        })
    
    return plans


def get_subscription_summary_for_superadmin(
    db: Session,
    tenant_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Récupère un résumé des abonnements pour le super admin.
    Inclut à la fois les abonnements tenant et utilisateur.
    """
    tenant_subs_query = db.query(TenantSubscription)
    user_subs_query = db.query(UserSubscription)
    
    if tenant_id:
        tenant_uuid = UUID(tenant_id)
        tenant_subs_query = tenant_subs_query.filter(TenantSubscription.tenant_id == tenant_uuid)
        user_subs_query = user_subs_query.filter(UserSubscription.tenant_id == tenant_uuid)
    
    tenant_subs = tenant_subs_query.all()
    user_subs = user_subs_query.all()
    
    total_revenue = 0
    for sub in tenant_subs:
        if sub.is_active():
            total_revenue += float(sub.current_price or 0)
    
    for sub in user_subs:
        if sub.is_active():
            total_revenue += float(sub.price or 0)
    
    return {
        "tenant_subscriptions": len(tenant_subs),
        "user_subscriptions": len(user_subs),
        "total_subscriptions": len(tenant_subs) + len(user_subs),
        "total_monthly_revenue": float(total_revenue),
        "projected_yearly_revenue": float(total_revenue * 12),
        "tenant_subscriptions_list": [
            {
                "id": str(sub.id),
                "tenant_id": str(sub.tenant_id),
                "plan": sub.plan.value if hasattr(sub.plan, 'value') else str(sub.plan),
                "plan_name": sub.plan_name,
                "status": sub.status.value if hasattr(sub.status, 'value') else str(sub.status),
                "start_date": sub.start_date.isoformat() if sub.start_date else None,
                "end_date": sub.end_date.isoformat() if sub.end_date else None,
                "price": float(sub.current_price or 0),
                "type": "tenant"
            }
            for sub in tenant_subs
        ],
        "user_subscriptions_list": [
            {
                "id": str(sub.id),
                "user_id": str(sub.user_id),
                "tenant_id": str(sub.tenant_id) if sub.tenant_id else None,
                "plan": sub.plan_type,
                "plan_name": sub.plan_name,
                "status": sub.status,
                "start_date": sub.start_date.isoformat() if sub.start_date else None,
                "end_date": sub.end_date.isoformat() if sub.end_date else None,
                "price": float(sub.price or 0),
                "type": "user"
            }
            for sub in user_subs
        ]
    }


def create_subscription_payment(
    db: Session,
    user_id: Union[str, UUID],
    payment_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Enregistre un paiement d'abonnement pour un utilisateur."""
    if isinstance(user_id, str):
        user_id = UUID(user_id)
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("Utilisateur non trouvé")

    plan = payment_data.get("plan")
    billing_period = payment_data.get("billing_period", "monthly")
    payment_method = payment_data.get("payment_method")
    amount = payment_data.get("amount")
    reference = payment_data.get("reference")

    subscription = upgrade_user_subscription(
        db=db,
        user_id=user.id,
        new_plan=plan,
        billing_cycle=billing_period,
        payment_id=reference,
        payment_method=payment_method
    )

    return {
        "success": True,
        "subscription_id": str(subscription.id),
        "reference": reference,
        "amount": float(amount),
        "currency": "EUR",
        "payment_method": payment_method,
        "paid_at": datetime.utcnow().isoformat(),
        "type": "user_subscription"
    }
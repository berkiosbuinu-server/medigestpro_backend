# app/api/v1/subscriptions.py - VERSION CORRIGEE

"""
Endpoints de gestion des abonnements (VERSION BRANCHE).
- Abonnement lié à la BRANCHE (pas à la pharmacie)
- Utilisateur hérite des droits de sa branche active
- Version utilisant BranchSubscription
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_active_user, get_super_admin_user
from app.models.user import User
from app.models.branch import Branch
from app.models.branch_subscription import (
    BranchSubscription, 
    SubscriptionPlan, 
    SubscriptionStatus
)
from app.models.invoice import InvoiceStatus, Invoice
from app.schemas.subscription import UpgradeSubscriptionSchema, ManualActivationSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])


# =============================================================================
# CONFIGURATION DES PLANS (harmonisée)
# =============================================================================

PLAN_CONFIG = {
    "trial": {
        "name": "Essai",
        "price_monthly": 0,
        "price_yearly": 0,
        "max_products": 3000,
        "max_users": 10,
        "max_storage_mb": 2048,
        "trial_days": 14,
        "features": [
            "10 Utilisateurs",
            "3000 Produits",
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
            "3000 Produits",
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
            "4000 Produits",
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
            "15000 Produits",
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


# =============================================================================
# HELPERS
# =============================================================================

def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def to_str_uuid(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def safe_percentage(current: int, limit: int) -> float:
    if limit <= 0:
        return 0.0
    return round((current / limit) * 100, 2)


def get_plan_config(plan_type: str) -> Dict[str, Any]:
    """Récupère la configuration d'un plan."""
    return PLAN_CONFIG.get(plan_type, PLAN_CONFIG["trial"])


def format_unlimited(value: int) -> str:
    """Formate une valeur illimitée."""
    if value == 0:
        return "Illimité"
    return str(value)


def get_branch_subscription_by_id(db: Session, branch_id: UUID) -> Optional[BranchSubscription]:
    """Récupère l'abonnement d'une branche par son ID."""
    return db.query(BranchSubscription).filter(
        BranchSubscription.branch_id == branch_id
    ).first()


# =============================================================================
# FONCTION CORRIGEE - get_branch_limits
# =============================================================================

def get_branch_limits(db: Session, user: User) -> Dict[str, Any]:
    """
    Récupère les limites de la branche active.
    VERSION CORRIGEE - sans la mauvaise relation Branch.users
    """
    
    # Verifier que l'utilisateur a une branche active
    if not user or not user.active_branch_id:
        logger.warning(f"Utilisateur {user.email if user else 'unknown'} sans branche active")
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
        # Recuperer la branche
        branch = db.query(Branch).filter(
            Branch.id == user.active_branch_id,
            Branch.is_active == True
        ).first()
        
        if not branch:
            logger.warning(f"Branche {user.active_branch_id} non trouvee pour {user.email}")
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
        
        # Recuperer l'abonnement de la branche
        subscription = get_branch_subscription_by_id(db, branch.id)
        
        if not subscription:
            logger.info(f"Pas d'abonnement pour branche {branch.id}")
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
        
        # Verifier si l'abonnement est actif
        is_active = subscription.is_active()
        
        # Recuperer le nom du plan
        plan_value = subscription.plan.value if hasattr(subscription.plan, 'value') else str(subscription.plan)
        plan_config = get_plan_config(plan_value)
        
        logger.info(f"Abonnement trouve pour branche {branch.id}: plan={plan_value}, is_active={is_active}, end_date={subscription.end_date}")
        
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
            "days_remaining": subscription.days_remaining(),
            "features": plan_config.get("features", []),
            "access_mode": "full" if is_active else "read_only"
        }
        
    except Exception as e:
        logger.error(f"Erreur dans get_branch_limits: {e}", exc_info=True)
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


def get_current_usage(db: Session, user: User) -> Dict[str, Any]:
    """Récupère l'utilisation actuelle de la branche active."""
    if not user.active_branch_id:
        return {"products": 0, "users": 0}
    
    from app.models.product import Product
    from app.models.user import User as UserModel
    
    products_count = db.query(Product).filter(
        Product.branch_id == user.active_branch_id
    ).count()
    
    # Compter les utilisateurs associes a cette branche via user_branches
    from app.models.user_branch import UserBranch
    users_count = db.query(UserBranch).filter(
        UserBranch.branch_id == user.active_branch_id,
        UserBranch.is_active == True
    ).count()
    
    return {
        "products": products_count,
        "users": users_count
    }


def update_subscription_limits(subscription: BranchSubscription, plan_config: Dict[str, Any]) -> None:
    """Met à jour les limites d'un abonnement selon la config du plan."""
    subscription.max_products = plan_config["max_products"]
    subscription.max_users = plan_config["max_users"]
    subscription.max_storage_mb = plan_config.get("max_storage_mb", 100)


# =============================================================================
# ENDPOINTS UTILISATEUR
# =============================================================================

@router.get("/status", response_model=Dict[str, Any])
async def get_subscription_status(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Statut détaillé de l'abonnement de la branche active.
    """
    logger.info("Demande du statut abonnement pour %s", current_user.email)
    
    if not current_user.active_branch_id:
        logger.warning(f"Utilisateur {current_user.email} sans branche active")
        return {
            "success": True,
            "has_subscription": False,
            "is_active": False,
            "message": "Aucune branche active selectionnee",
            "access_mode": "read_only",
            "user": {
                "id": str(current_user.id),
                "email": current_user.email,
                "role": current_user.role,
                "active_branch_id": None
            },
            "subscription": None,
            "limits": None,
            "usage": None,
            "metadata": {
                "checked_at": utc_now_iso(),
                "requires_subscription": True
            }
        }
    
    limits = get_branch_limits(db, current_user)
    usage = get_current_usage(db, current_user)
    
    is_active = limits.get("is_active", False)
    has_subscription = limits.get("has_subscription", False)
    access_mode = "full" if is_active else "read_only"
    
    logger.info(f"Statut pour {current_user.email}: has_subscription={has_subscription}, is_active={is_active}")
    
    return {
        "success": True,
        "has_subscription": has_subscription,
        "is_active": is_active,
        "access_mode": access_mode,
        "message": "Abonnement actif" if is_active else "Abonnement inactif ou expire",
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role,
            "active_branch_id": str(current_user.active_branch_id) if current_user.active_branch_id else None,
        },
        "subscription": {
            "plan": limits.get("plan"),
            "plan_name": limits.get("plan_name"),
            "price": limits.get("price", 0),
            "currency": "EUR",
            "billing_cycle": limits.get("billing_cycle"),
            "end_date": limits.get("end_date").isoformat() if limits.get("end_date") else None,
            "days_remaining": limits.get("days_remaining", 0),
            "is_trial": limits.get("plan") == "trial",
        } if has_subscription else None,
        "limits": {
            "max_products": format_unlimited(limits.get("max_products", 0)),
            "max_users": format_unlimited(limits.get("max_users", 0)),
            "max_storage_mb": format_unlimited(limits.get("max_storage_mb", 0)),
            "features": limits.get("features", []),
        } if has_subscription else None,
        "usage": {
            "current_products": usage.get("products", 0),
            "current_users": usage.get("users", 0),
            "products_percentage": safe_percentage(usage.get("products", 0), limits.get("max_products", 0)),
            "users_percentage": safe_percentage(usage.get("users", 0), limits.get("max_users", 0)),
        } if has_subscription else None,
        "metadata": {
            "checked_at": utc_now_iso(),
            "requires_upgrade": not is_active and has_subscription,
            "requires_subscription": not has_subscription
        }
    }

@router.get("/usage", response_model=Dict[str, Any])
async def get_subscription_usage(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    detailed: bool = Query(False),
) -> Dict[str, Any]:
    """
    Statistiques d'utilisation des ressources par rapport au plan.
    """
    logger.info("Demande des statistiques d'utilisation pour %s", current_user.email)
    
    if not current_user.active_branch_id:
        return {
            "success": True,
            "has_subscription": False,
            "message": "Aucune branche active sélectionnée",
            "access_mode": "read_only",
            "usage": {},
            "limits": {},
            "percentages": {},
            "alerts": [],
            "timestamp": utc_now_iso()
        }
    
    limits = get_branch_limits(db, current_user)
    usage = get_current_usage(db, current_user)
    
    if not limits.get("has_subscription"):
        return {
            "success": True,
            "has_subscription": False,
            "message": "Aucun abonnement actif pour cette branche",
            "access_mode": "read_only",
            "usage": {},
            "limits": {},
            "percentages": {},
            "alerts": [],
            "timestamp": utc_now_iso()
        }
    
    is_active = limits.get("is_active", False)
    max_products = limits.get("max_products", 0)
    max_users = limits.get("max_users", 0)
    current_products = usage.get("products", 0)
    current_users = usage.get("users", 0)
    
    products_percentage = safe_percentage(current_products, max_products) if max_products > 0 else 0
    users_percentage = safe_percentage(current_users, max_users) if max_users > 0 else 0
    
    alerts = []
    
    if max_products > 0 and products_percentage >= 80:
        alerts.append({
            "type": "products_limit",
            "severity": "critical" if products_percentage >= 95 else "warning",
            "message": f"Vous utilisez {products_percentage}% de votre limite de produits ({current_products}/{max_products}).",
            "current": current_products,
            "limit": max_products,
            "percentage": products_percentage
        })
    
    if max_users > 0 and users_percentage >= 80:
        alerts.append({
            "type": "users_limit",
            "severity": "critical" if users_percentage >= 95 else "warning",
            "message": f"Vous utilisez {users_percentage}% de votre limite d'utilisateurs ({current_users}/{max_users}).",
            "current": current_users,
            "limit": max_users,
            "percentage": users_percentage
        })
    
    if not is_active:
        alerts.append({
            "type": "subscription_expired",
            "severity": "critical",
            "message": "Votre abonnement a expiré. Veuillez le renouveler pour continuer à utiliser toutes les fonctionnalités.",
            "days_remaining": limits.get("days_remaining", 0)
        })
    
    response = {
        "success": True,
        "has_subscription": True,
        "subscription_active": is_active,
        "plan": limits.get("plan"),
        "plan_name": limits.get("plan_name"),
        "access_mode": "full" if is_active else "read_only",
        "usage": {
            "products": current_products,
            "users": current_users
        },
        "limits": {
            "products": format_unlimited(max_products),
            "users": format_unlimited(max_users),
            "storage_mb": format_unlimited(limits.get("max_storage_mb", 0))
        },
        "percentages": {
            "products": products_percentage,
            "users": users_percentage
        },
        "alerts": alerts,
        "timestamp": utc_now_iso()
    }
    
    if detailed and current_user.active_branch_id:
        from app.models.product import Product
        
        # Produits par catégorie
        products_by_category = {}
        products = db.query(Product).filter(
            Product.branch_id == current_user.active_branch_id
        ).all()
        
        for product in products:
            category = product.category or "Non catégorisé"
            products_by_category[category] = products_by_category.get(category, 0) + 1
        
        response["details"] = {
            "products_by_category": products_by_category,
            "total_categories": len(products_by_category)
        }
    
    return response


@router.get("/plans", response_model=Dict[str, List[Dict[str, Any]]])
async def get_available_plans(
    include_trial: bool = Query(False),
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Liste des plans disponibles.
    """
    plans: List[Dict[str, Any]] = []
    
    for key, config in PLAN_CONFIG.items():
        if key == "trial" and not include_trial:
            continue
        
        plans.append({
            "id": key,
            "name": config["name"],
            "price_monthly": config["price_monthly"],
            "price_yearly": config["price_yearly"],
            "max_products": config["max_products"] if config["max_products"] > 0 else "Illimité",
            "max_users": config["max_users"] if config["max_users"] > 0 else "Illimité",
            "max_storage_mb": config["max_storage_mb"] if config["max_storage_mb"] > 0 else "Illimité",
            "features": config.get("features", []),
            "is_trial": key == "trial",
            "is_popular": key == "professional"
        })
    
    logger.info("Liste des plans récupérée: %s plans", len(plans))
    return {"plans": plans}


@router.get("/my-status", response_model=Dict[str, Any])
async def get_my_subscription_status(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Statut rapide de l'abonnement (version simplifiée).
    """
    logger.info("Vérification rapide abonnement pour %s", current_user.email)
    
    if not current_user.active_branch_id:
        return {
            "has_subscription": False,
            "is_active": False,
            "access_mode": "read_only",
            "message": "Aucune branche active"
        }
    
    limits = get_branch_limits(db, current_user)
    is_active = limits.get("is_active", False)
    
    return {
        "has_subscription": limits.get("has_subscription", False),
        "is_active": is_active,
        "plan": limits.get("plan"),
        "plan_name": limits.get("plan_name"),
        "access_mode": "full" if is_active else "read_only",
        "days_remaining": limits.get("days_remaining", 0),
        "end_date": limits.get("end_date").isoformat() if limits.get("end_date") else None,
        "checked_at": utc_now_iso()
    }


@router.post("/upgrade", response_model=Dict[str, Any])
async def upgrade_branch_subscription(
    data: UpgradeSubscriptionSchema,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Met à niveau l'abonnement de la branche active.
    Réservé à l'admin.
    """
    if current_user.role != "admin":
        logger.warning("Tentative upgrade par non-admin: %s", current_user.email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seuls les administrateurs peuvent changer d'abonnement."
        )
    
    if not current_user.active_branch_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune branche active sélectionnée."
        )
    
    if data.plan not in PLAN_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Plan invalide. Options: {', '.join(PLAN_CONFIG.keys())}"
        )
    
    # Récupérer l'abonnement directement
    subscription = get_branch_subscription_by_id(db, current_user.active_branch_id)
    
    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucun abonnement trouvé pour cette branche."
        )
    
    plan_config = get_plan_config(data.plan)
    now = datetime.utcnow()
    
    if data.billing_cycle == "yearly":
        end_date = now + timedelta(days=365)
        price = plan_config["price_yearly"]
    else:
        end_date = now + timedelta(days=30)
        price = plan_config["price_monthly"]
    
    # Mettre à jour l'abonnement directement
    subscription.plan = SubscriptionPlan(data.plan)
    subscription.plan_name = plan_config["name"]
    subscription.start_date = now
    subscription.end_date = end_date
    subscription.price = price
    subscription.billing_cycle = data.billing_cycle
    subscription.status = SubscriptionStatus.ACTIVE
    update_subscription_limits(subscription, plan_config)
    subscription.updated_at = now
    
    db.commit()
    db.refresh(subscription)
    
    logger.info("Upgrade abonnement pour branche %s vers %s", current_user.active_branch_id, data.plan)
    
    return {
        "success": True,
        "message": f"Abonnement mis à niveau vers le plan {plan_config['name']}.",
        "subscription": {
            "id": str(subscription.id),
            "branch_id": str(subscription.branch_id),
            "plan": subscription.plan.value,
            "plan_name": subscription.plan_name,
            "status": subscription.status.value,
            "start_date": subscription.start_date.isoformat(),
            "end_date": subscription.end_date.isoformat(),
            "days_remaining": subscription.days_remaining(),
            "price": subscription.price,
            "billing_cycle": subscription.billing_cycle
        },
        "access_mode": "full",
        "upgraded_at": utc_now_iso()
    }


@router.get("/check-access/{feature}", response_model=Dict[str, Any])
async def check_feature_access(
    feature: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Vérifie l'accès à une fonctionnalité basé sur l'abonnement de la branche active.
    """
    if not current_user.active_branch_id:
        return {
            "feature": feature,
            "has_access": False,
            "subscription_active": False,
            "has_subscription": False,
            "plan": None,
            "mode": "READ_ONLY",
            "is_read_only": True,
            "access_denied_reason": "Aucune branche active sélectionnée",
            "checked_at": utc_now_iso()
        }
    
    limits = get_branch_limits(db, current_user)
    is_active = limits.get("is_active", False)
    has_subscription = limits.get("has_subscription", False)
    plan = limits.get("plan")
    
    # Vérifier si l'utilisateur a accès à la fonctionnalité
    write_keywords = {"create", "update", "delete", "edit", "add", "remove", "modify"}
    is_write_operation = any(keyword in feature.lower() for keyword in write_keywords)
    
    if not is_active and is_write_operation:
        has_access = False
        denied_reason = "Operation non autorisee : abonnement inactif ou expire."
    elif not has_subscription:
        has_access = False
        denied_reason = "Aucun abonnement actif pour cette branche."
    else:
        # Vérifier si la fonctionnalité est incluse dans le plan
        plan_config = get_plan_config(plan)
        features = plan_config.get("features", [])
        has_access = any(feature.lower() in f.lower() for f in features)
        denied_reason = None if has_access else f"Fonctionnalite non incluse dans le plan {plan_config['name']}."
    
    return {
        "feature": feature,
        "has_access": has_access,
        "subscription_active": is_active,
        "has_subscription": has_subscription,
        "plan": plan,
        "plan_name": limits.get("plan_name"),
        "mode": "FULL" if is_active else "READ_ONLY",
        "is_read_only": not is_active,
        "access_denied_reason": denied_reason,
        "requires_upgrade": not is_active and has_subscription,
        "checked_at": utc_now_iso()
    }

@router.get("/billing-history", response_model=Dict[str, Any])
async def get_billing_history(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """
    Historique des factures de la branche active.
    """
    logger.info("Récupération de l'historique des factures pour %s", current_user.email)
    
    if not current_user.active_branch_id:
        return {
            "success": True,
            "has_billing_history": False,
            "message": "Aucune branche active sélectionnée",
            "billing_history": [],
            "summary": {
                "total_items": 0,
                "total_spent": 0,
                "total_tax": 0,
                "last_payment": None,
                "has_unpaid_invoices": False,
                "unpaid_invoices_count": 0,
                "unpaid_total": 0
            },
            "pagination": {"limit": limit, "offset": offset, "has_more": False},
            "timestamp": utc_now_iso()
        }
    
    # ✅ Utiliser branch_id maintenant disponible
    query = db.query(Invoice).filter(
        Invoice.branch_id == current_user.active_branch_id
    )
    
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
            query = query.filter(Invoice.issue_date >= start_dt)
        except ValueError:
            pass
    
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
            query = query.filter(Invoice.issue_date <= end_dt)
        except ValueError:
            pass
    
    total = query.count()
    invoices = query.order_by(Invoice.issue_date.desc()).offset(offset).limit(limit).all()
    
    billing_items = []
    total_spent = 0
    total_tax = 0
    unpaid_count = 0
    unpaid_total = 0
    last_payment = None
    
    for invoice in invoices:
        is_paid = invoice.status == InvoiceStatus.PAID
        is_overdue = invoice.is_overdue()
        
        if is_paid:
            total_spent += invoice.total_amount
            total_tax += invoice.tax_amount
            if not last_payment or invoice.paid_at > last_payment.get("paid_at"):
                last_payment = {
                    "date": invoice.paid_at.isoformat() if invoice.paid_at else None,
                    "amount": invoice.total_amount,
                    "currency": invoice.currency
                }
        else:
            unpaid_count += 1
            unpaid_total += invoice.total_amount
        
        billing_items.append({
            "id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
            "type": invoice.invoice_type.value if hasattr(invoice.invoice_type, 'value') else str(invoice.invoice_type),
            "period_start": invoice.period_start.isoformat(),
            "period_end": invoice.period_end.isoformat(),
            "subtotal": invoice.subtotal,
            "tax_rate": invoice.tax_rate,
            "tax_amount": invoice.tax_amount,
            "discount_amount": invoice.discount_amount,
            "total_amount": invoice.total_amount,
            "currency": invoice.currency,
            "status": invoice.status.value if hasattr(invoice.status, 'value') else str(invoice.status),
            "is_paid": is_paid,
            "is_overdue": is_overdue,
            "issue_date": invoice.issue_date.isoformat(),
            "due_date": invoice.due_date.isoformat(),
            "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
            "description": invoice.description,
            "subscription_plan": invoice.subscription_plan,
            "billing_cycle": invoice.billing_cycle,
            "days_overdue": invoice.days_overdue() if is_overdue else 0,
            "payment_method": invoice.payment_method,
            "payment_reference": invoice.payment_reference
        })
    
    has_more = offset + limit < total
    
    return {
        "success": True,
        "has_billing_history": total > 0,
        "billing_history": billing_items,
        "summary": {
            "total_items": total,
            "total_spent": round(total_spent, 2),
            "total_tax": round(total_tax, 2),
            "last_payment": last_payment,
            "has_unpaid_invoices": unpaid_count > 0,
            "unpaid_invoices_count": unpaid_count,
            "unpaid_total": round(unpaid_total, 2)
        },
        "pagination": {
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "next_offset": offset + limit if has_more else None
        },
        "filters_applied": {
            "start_date": start_date,
            "end_date": end_date
        },
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "active_branch_id": str(current_user.active_branch_id) if current_user.active_branch_id else None
        },
        "timestamp": utc_now_iso()
    }
# =============================================================================
# ENDPOINTS SUPER ADMIN
# =============================================================================

@router.get("/admin/overview", response_model=Dict[str, Any])
async def get_subscriptions_overview(
    tenant_id: Optional[str] = Query(None),
    branch_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Dict[str, Any]:
    """Vue d'ensemble des abonnements pour le super admin."""
    logger.info("Vue d'ensemble abonnements demandée par %s", current_user.email)
    
    # Requête directe sur BranchSubscription
    query = db.query(BranchSubscription)
    
    if tenant_id:
        try:
            tenant_uuid = UUID(tenant_id)
            query = query.filter(BranchSubscription.tenant_id == tenant_uuid)
        except ValueError:
            pass
    
    if branch_id:
        try:
            branch_uuid = UUID(branch_id)
            query = query.filter(BranchSubscription.branch_id == branch_uuid)
        except ValueError:
            pass
    
    subscriptions = query.all()
    
    active_count = sum(1 for s in subscriptions if s.is_active())
    trial_count = sum(1 for s in subscriptions if s.plan == SubscriptionPlan.TRIAL)
    expired_count = sum(1 for s in subscriptions if not s.is_active() and s.end_date < datetime.utcnow())
    
    total_revenue = sum(s.price for s in subscriptions if s.is_active())
    
    return {
        "success": True,
        "total_subscriptions": len(subscriptions),
        "active_subscriptions": active_count,
        "trial_subscriptions": trial_count,
        "expired_subscriptions": expired_count,
        "total_monthly_revenue": round(total_revenue, 2),
        "projected_yearly_revenue": round(total_revenue * 12, 2),
        "subscriptions": [
            {
                "id": str(s.id),
                "branch_id": str(s.branch_id),
                "branch_name": s.branch.name if s.branch else None,
                "pharmacy_id": str(s.pharmacy_id) if s.pharmacy_id else None,
                "tenant_id": str(s.tenant_id),
                "plan": s.plan.value if hasattr(s.plan, 'value') else str(s.plan),
                "plan_name": s.plan_name,
                "status": s.status.value if hasattr(s.status, 'value') else str(s.status),
                "is_active": s.is_active(),
                "start_date": s.start_date.isoformat() if s.start_date else None,
                "end_date": s.end_date.isoformat() if s.end_date else None,
                "price": s.price,
                "billing_cycle": s.billing_cycle,
                "max_products": s.max_products,
                "max_users": s.max_users,
                "max_storage_mb": s.max_storage_mb,
            }
            for s in subscriptions[:100]
        ],
        "requested_by": current_user.email,
        "requested_at": utc_now_iso(),
        "filters": {"tenant_id": tenant_id, "branch_id": branch_id}
    }


@router.post("/admin/manual-activation", response_model=Dict[str, Any])
async def manual_activate_subscription(
    data: ManualActivationSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Dict[str, Any]:
    """
    Activation manuelle d'un abonnement pour une branche.
    """
    logger.info("Activation manuelle par %s pour branch_id=%s", current_user.email, data.branch_id)
    
    try:
        branch = db.query(Branch).filter(Branch.id == data.branch_id).first()
        if not branch:
            raise HTTPException(status_code=404, detail="Branche non trouvée")
        
        plan_config = get_plan_config(data.plan)
        now = datetime.utcnow()
        
        if data.billing_cycle == "yearly":
            end_date = now + timedelta(days=365)
            price = plan_config["price_yearly"]
        else:
            end_date = now + timedelta(days=30)
            price = plan_config["price_monthly"]
        
        # Vérifier si un abonnement existe déjà
        existing_subscription = get_branch_subscription_by_id(db, branch.id)
        
        if existing_subscription:
            # Mettre à jour l'existant
            existing_subscription.plan = SubscriptionPlan(data.plan)
            existing_subscription.plan_name = plan_config["name"]
            existing_subscription.start_date = now
            existing_subscription.end_date = end_date
            existing_subscription.price = price
            existing_subscription.billing_cycle = data.billing_cycle
            existing_subscription.status = SubscriptionStatus.ACTIVE
            update_subscription_limits(existing_subscription, plan_config)
            existing_subscription.updated_at = now
            subscription = existing_subscription
        else:
            # Créer un nouvel abonnement
            subscription = BranchSubscription(
                branch_id=branch.id,
                tenant_id=branch.tenant_id,
                pharmacy_id=branch.parent_pharmacy_id,
                plan=SubscriptionPlan(data.plan),
                plan_name=plan_config["name"],
                start_date=now,
                end_date=end_date,
                status=SubscriptionStatus.ACTIVE,
                billing_cycle=data.billing_cycle,
                price=price,
                max_products=plan_config["max_products"],
                max_users=plan_config["max_users"],
                max_storage_mb=plan_config.get("max_storage_mb", 100)
            )
            db.add(subscription)
            db.flush()
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Abonnement active manuellement pour la branche {branch.name}.",
            "subscription": {
                "id": str(subscription.id),
                "branch_id": str(subscription.branch_id),
                "branch_name": branch.name,
                "plan": subscription.plan.value,
                "plan_name": subscription.plan_name,
                "status": subscription.status.value,
                "start_date": subscription.start_date.isoformat(),
                "end_date": subscription.end_date.isoformat(),
                "days_remaining": subscription.days_remaining(),
                "price": subscription.price,
                "billing_cycle": subscription.billing_cycle,
                "max_products": subscription.max_products,
                "max_users": subscription.max_users,
                "max_storage_mb": subscription.max_storage_mb
            },
            "activated_by": current_user.email,
            "activated_at": utc_now_iso()
        }
    
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Erreur activation manuelle: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'activation manuelle."
        )


@router.post("/force-sync", response_model=Dict[str, Any])
async def force_subscription_sync(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Point d'entrée spécial pour forcer la synchronisation de l'abonnement
    depuis l'application mobile.
    """
    logger.info(f"Force sync subscription for {current_user.email}")
    
    if not current_user.active_branch_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune branche active sélectionnée."
        )
    
    # Requête directe sur BranchSubscription
    subscription = get_branch_subscription_by_id(db, current_user.active_branch_id)
    
    if not subscription:
        return {
            "success": True,
            "has_subscription": False,
            "is_active": False,
            "message": "Aucun abonnement trouvé",
            "force_sync": True,
            "timestamp": utc_now_iso()
        }
    
    is_active = subscription.is_active()
    
    return {
        "success": True,
        "has_subscription": True,
        "is_active": is_active,
        "access_mode": "full" if is_active else "read_only",
        "force_sync": True,
        "subscription": {
            "plan": subscription.plan.value,
            "plan_name": subscription.plan_name,
            "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
            "days_remaining": subscription.days_remaining(),
            "price": subscription.price,
            "billing_cycle": subscription.billing_cycle
        },
        "limits": {
            "max_products": subscription.max_products if subscription.max_products > 0 else "Illimité",
            "max_users": subscription.max_users if subscription.max_users > 0 else "Illimité",
            "max_storage_mb": subscription.max_storage_mb if subscription.max_storage_mb > 0 else "Illimité"
        },
        "message": "Synchronisation forcée réussie" if is_active else "Abonnement expiré - mode lecture seule",
        "timestamp": utc_now_iso()
    }

@router.get("/sync/subscription/status", response_model=Dict[str, Any])
async def sync_subscription_status(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    branch_id: Optional[str] = Query(None, description="ID de la branche (optionnel)"),
) -> Dict[str, Any]:
    """
    Endpoint de synchronisation pour l'abonnement.
    Utilisé par le sync_manager mobile.
    """
    logger.info(f"Sync subscription status pour {current_user.email}, branch_id={branch_id}")
    
    # Utiliser la branche active ou celle spécifiée
    effective_branch_id = branch_id or str(current_user.active_branch_id) if current_user.active_branch_id else None
    
    if not effective_branch_id:
        return {
            "success": True,
            "has_subscription": False,
            "is_active": False,
            "access_mode": "read_only",
            "subscription": None,
            "message": "Aucune branche active",
            "synced_at": utc_now_iso()
        }
    
    try:
        # Récupérer l'abonnement de la branche
        subscription = get_branch_subscription_by_id(db, UUID(effective_branch_id))
        
        if not subscription:
            return {
                "success": True,
                "has_subscription": False,
                "is_active": False,
                "access_mode": "read_only",
                "subscription": None,
                "branch_id": effective_branch_id,
                "message": "Aucun abonnement trouvé pour cette branche",
                "synced_at": utc_now_iso()
            }
        
        is_active = subscription.is_active()
        plan_config = get_plan_config(subscription.plan.value if hasattr(subscription.plan, 'value') else str(subscription.plan))
        
        return {
            "success": True,
            "has_subscription": True,
            "is_active": is_active,
            "access_mode": "full" if is_active else "read_only",
            "branch_id": effective_branch_id,
            "subscription": {
                "id": str(subscription.id),
                "branch_id": str(subscription.branch_id),
                "plan_name": subscription.plan_name or plan_config.get("name"),
                "plan_type": subscription.plan.value if hasattr(subscription.plan, 'value') else str(subscription.plan),
                "status": subscription.status.value if hasattr(subscription.status, 'value') else str(subscription.status),
                "current_period_start": subscription.start_date.isoformat() if subscription.start_date else None,
                "current_period_end": subscription.end_date.isoformat() if subscription.end_date else None,
                "days_remaining": subscription.days_remaining(),
                "max_products": subscription.max_products if subscription.max_products > 0 else "Illimité",
                "max_users": subscription.max_users if subscription.max_users > 0 else "Illimité",
                "max_storage_mb": subscription.max_storage_mb if subscription.max_storage_mb > 0 else "Illimité",
                "is_trial": subscription.plan == SubscriptionPlan.TRIAL,
                "trial_days_remaining": subscription.days_remaining() if subscription.plan == SubscriptionPlan.TRIAL else 0,
                "auto_renew": True,
                "billing_cycle": subscription.billing_cycle,
                "price": float(subscription.price) if subscription.price else 0,
                "currency": "EUR"
            },
            "limits": {
                "max_products": subscription.max_products if subscription.max_products > 0 else "Illimité",
                "max_users": subscription.max_users if subscription.max_users > 0 else "Illimité",
            },
            "usage": {
                "current_products": 0,
                "current_users": 0,
            },
            "synced_at": utc_now_iso()
        }
        
    except Exception as e:
        logger.error(f"Erreur sync_subscription_status: {e}", exc_info=True)
        return {
            "success": False,
            "has_subscription": False,
            "is_active": False,
            "access_mode": "read_only",
            "error": str(e),
            "synced_at": utc_now_iso()
        }


# =============================================================================
# ENDPOINTS TECHNIQUES
# =============================================================================

@router.get("/health", response_model=Dict[str, Any])
async def health_check() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "subscriptions-api",
        "version": "3.0.0",
        "architecture": "branch_based",
        "timestamp": utc_now_iso(),
        "plans_available": list(PLAN_CONFIG.keys()),
    }
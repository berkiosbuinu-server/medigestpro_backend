# app/api/v1/endpoints/subscriptions.py - VERSION CORRIGEE

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime, timedelta
import logging

from app.db.session import get_db
from app.models.tenant import Tenant
from app.models.branch_subscription import BranchSubscription, SubscriptionPlan, SubscriptionStatus
from app.models.branch import Branch
from app.models.user import User
from app.schemas.subscription import (
    SubscriptionCreate, SubscriptionUpdate, 
    SubscriptionInDB, SubscriptionResponse
)
from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission

router = APIRouter(prefix="/tenants/{tenant_id}/subscription", tags=["subscriptions"])
logger = logging.getLogger(__name__)


@router.get("/", response_model=SubscriptionInDB, summary="Détails de l'abonnement")
@require_permission("view_subscription")
async def get_subscription(
    tenant_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les détails de l'abonnement du tenant.
    Note: Dans l'architecture actuelle, l'abonnement est lié à la BRANCHE.
    Cette endpoint retourne un agrégat pour le tenant.
    """
    # Vérifier si l'utilisateur a accès à ce tenant
    if str(current_user.tenant_id) != str(tenant_id) and current_user.role not in ['admin', 'super_admin']:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    # Récupérer la première branche active du tenant (ou la branche de l'utilisateur)
    branch_id = current_user.active_branch_id or current_user.branch_id
    
    if branch_id:
        subscription = db.query(BranchSubscription).filter(
            BranchSubscription.branch_id == branch_id
        ).first()
        
        if subscription:
            return SubscriptionInDB(
                tenant_id=tenant_id,
                plan_name=subscription.plan_name,
                plan_type=subscription.plan.value if hasattr(subscription.plan, 'value') else str(subscription.plan),
                status=subscription.status.value if hasattr(subscription.status, 'value') else str(subscription.status),
                max_users=subscription.max_users,
                max_products=subscription.max_products,
                features={},  # À enrichir selon le plan
                billing_cycle=subscription.billing_cycle,
                price=subscription.price,
                currency="EUR",
                current_period_start=subscription.start_date,
                current_period_end=subscription.end_date,
                is_active=subscription.is_active()
            )
    
    # Retourner un abonnement par défaut si aucun n'existe
    return SubscriptionInDB(
        tenant_id=tenant_id,
        plan_name="Gratuit",
        plan_type="free",
        status="active",
        max_users=1,
        max_products=3000,
        features={},
        billing_cycle="monthly",
        price=0.0,
        currency="EUR",
        current_period_start=None,
        current_period_end=None,
        is_active=True
    )


@router.post("/", response_model=SubscriptionResponse, summary="Créer/mettre à jour un abonnement")
@require_permission("manage_subscription")
async def create_or_update_subscription(
    tenant_id: UUID,
    subscription_data: SubscriptionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Crée ou met à jour un abonnement pour une branche.
    """
    # Vérifier les permissions
    if current_user.role not in ['admin', 'super_admin']:
        raise HTTPException(status_code=403, detail="Permission insuffisante")
    
    # Vérifier si le tenant existe
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant non trouvé")
    
    # Déterminer la branche cible
    branch_id = subscription_data.branch_id if hasattr(subscription_data, 'branch_id') and subscription_data.branch_id else current_user.active_branch_id
    
    if not branch_id:
        raise HTTPException(status_code=400, detail="Aucune branche spécifiée pour l'abonnement")
    
    # Vérifier si un abonnement existe déjà pour cette branche
    existing_subscription = db.query(BranchSubscription).filter(
        BranchSubscription.branch_id == branch_id
    ).first()
    
    # Mapper le plan vers SubscriptionPlan
    plan_mapping = {
        "free": SubscriptionPlan.STARTER,
        "starter": SubscriptionPlan.STARTER,
        "standard": SubscriptionPlan.PROFESSIONAL,
        "pro": SubscriptionPlan.PROFESSIONAL,
        "professional": SubscriptionPlan.PROFESSIONAL,
        "enterprise": SubscriptionPlan.ENTERPRISE,
        "infinite": SubscriptionPlan.INFINITE,
        "trial": SubscriptionPlan.TRIAL
    }
    
    plan_type = plan_mapping.get(subscription_data.plan_type, SubscriptionPlan.STARTER)
    
    # Configurations des plans
    plan_configs = {
        SubscriptionPlan.STARTER: {"max_products": 3000, "max_users": 5, "price": 5},
        SubscriptionPlan.PROFESSIONAL: {"max_products": 4000, "max_users": 20, "price": 8},
        SubscriptionPlan.ENTERPRISE: {"max_products": 15000, "max_users": 20, "price": 15},
        SubscriptionPlan.INFINITE: {"max_products": 0, "max_users": 0, "price": 30},
        SubscriptionPlan.TRIAL: {"max_products": 2000, "max_users": 10, "price": 0}
    }
    
    config = plan_configs.get(plan_type, plan_configs[SubscriptionPlan.STARTER])
    
    now = datetime.utcnow()
    end_date = now + timedelta(days=30) if subscription_data.billing_cycle == "monthly" else now + timedelta(days=365)
    
    try:
        if existing_subscription:
            # Mettre à jour l'abonnement existant
            existing_subscription.plan = plan_type
            existing_subscription.plan_name = subscription_data.plan_name or plan_type.value.capitalize()
            existing_subscription.status = SubscriptionStatus.ACTIVE
            existing_subscription.start_date = now
            existing_subscription.end_date = end_date
            existing_subscription.billing_cycle = subscription_data.billing_cycle
            existing_subscription.price = config["price"]
            existing_subscription.max_products = config["max_products"]
            existing_subscription.max_users = config["max_users"]
            existing_subscription.updated_at = now
            
            db.commit()
            db.refresh(existing_subscription)
            
            return SubscriptionResponse(
                message="Abonnement mis à jour avec succès",
                subscription=SubscriptionInDB(
                    tenant_id=tenant_id,
                    plan_name=existing_subscription.plan_name,
                    plan_type=existing_subscription.plan.value if hasattr(existing_subscription.plan, 'value') else str(existing_subscription.plan),
                    status=existing_subscription.status.value if hasattr(existing_subscription.status, 'value') else str(existing_subscription.status),
                    max_users=existing_subscription.max_users,
                    max_products=existing_subscription.max_products,
                    features={},
                    billing_cycle=existing_subscription.billing_cycle,
                    price=existing_subscription.price,
                    currency="EUR",
                    current_period_start=existing_subscription.start_date,
                    current_period_end=existing_subscription.end_date,
                    is_active=existing_subscription.is_active()
                )
            )
        else:
            # Créer un nouvel abonnement
            branch = db.query(Branch).filter(Branch.id == branch_id).first()
            
            subscription = BranchSubscription(
                branch_id=branch_id,
                tenant_id=tenant_id,
                pharmacy_id=branch.parent_pharmacy_id if branch else None,
                plan=plan_type,
                plan_name=subscription_data.plan_name or plan_type.value.capitalize(),
                status=SubscriptionStatus.ACTIVE,
                start_date=now,
                end_date=end_date,
                billing_cycle=subscription_data.billing_cycle,
                price=config["price"],
                max_products=config["max_products"],
                max_users=config["max_users"],
                max_storage_mb=500,
                created_at=now,
                updated_at=now
            )
            
            db.add(subscription)
            db.commit()
            db.refresh(subscription)
            
            return SubscriptionResponse(
                message="Abonnement créé avec succès",
                subscription=SubscriptionInDB(
                    tenant_id=tenant_id,
                    plan_name=subscription.plan_name,
                    plan_type=subscription.plan.value if hasattr(subscription.plan, 'value') else str(subscription.plan),
                    status=subscription.status.value if hasattr(subscription.status, 'value') else str(subscription.status),
                    max_users=subscription.max_users,
                    max_products=subscription.max_products,
                    features={},
                    billing_cycle=subscription.billing_cycle,
                    price=subscription.price,
                    currency="EUR",
                    current_period_start=subscription.start_date,
                    current_period_end=subscription.end_date,
                    is_active=subscription.is_active()
                )
            )
            
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur gestion abonnement: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur: {str(e)}")


@router.get("/plans", summary="Liste des plans disponibles")
async def get_available_plans():
    """
    Récupère la liste des plans d'abonnement disponibles.
    Basé sur PLAN_CONFIG du fichier subscriptions.py original.
    """
    plans = [
        {
            "name": "Trial",
            "type": "trial",
            "description": "Essai gratuit 14 jours",
            "max_users": 10,
            "max_products": 3000,
            "price": 0.0,
            "currency": "EUR",
            "billing_cycle": "monthly",
            "features": ["10 Utilisateurs", "3000 Produits", "14 jours d'essai", "Support prioritaire"]
        },
        {
            "name": "Starter",
            "type": "starter",
            "description": "Plan de base pour petites structures",
            "max_users": 5,
            "max_products": 3000,
            "price": 5.0,
            "currency": "EUR",
            "billing_cycle": "monthly",
            "features": ["5 Utilisateurs", "3000 Produits", "Support email"]
        },
        {
            "name": "Professionnel",
            "type": "professional",
            "description": "Plan professionnel pour pharmacies actives",
            "max_users": 20,
            "max_products": 4000,
            "price": 8.0,
            "currency": "EUR",
            "billing_cycle": "monthly",
            "features": ["20 Utilisateurs", "4000 Produits", "Transferts inter-stocks", "Support prioritaire"]
        },
        {
            "name": "Entreprise",
            "type": "enterprise",
            "description": "Solution complète pour grandes structures",
            "max_users": 20,
            "max_products": 15000,
            "price": 15.0,
            "currency": "EUR",
            "billing_cycle": "monthly",
            "features": ["20 Utilisateurs", "15000 Produits", "API d'inventaire", "Support 24/7"]
        },
        {
            "name": "Infinite",
            "type": "infinite",
            "description": "Solution illimitée",
            "max_users": "Illimité",
            "max_products": "Illimité",
            "price": 30.0,
            "currency": "EUR",
            "billing_cycle": "monthly",
            "features": ["Utilisateurs illimités", "Produits illimités", "Multi-dépôts", "Support dédié"]
        }
    ]
    
    return {"plans": plans}


def sync_expired_subscriptions(db: Session):
    """
    Synchronise les abonnements expirés.
    À appeler périodiquement (cron/job).
    """
    expired_subs = db.query(BranchSubscription).filter(
        BranchSubscription.end_date < datetime.utcnow(),
        BranchSubscription.status != SubscriptionStatus.EXPIRED
    ).all()
    
    for sub in expired_subs:
        sub.status = SubscriptionStatus.EXPIRED
        logger.info(f"Abonnement expiré marqué: branch_id={sub.branch_id}, end_date={sub.end_date}")
    
    db.commit()
    return len(expired_subs)
# app/api/v1/superadmin_subscriptions.py
"""
Endpoints de gestion des abonnements pour les super administrateurs.
Permet l'activation manuelle, la prolongation d'essais et la vue d'ensemble.
Gère les abonnements des PHARMACIES/BRANCHES uniquement (pas par utilisateur).
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func
from typing import Optional, Dict, Any, List
from uuid import UUID
import logging
import json
from datetime import datetime, timedelta
from decimal import Decimal

from app.api.deps import get_db, verify_super_admin
from app.models.user import User
from app.models.tenant import Tenant
from app.models.pharmacy import Pharmacy
from app.models.pharmacy_subscription import (
    PharmacySubscription, SubscriptionPlan, SubscriptionStatus
)
from app.schemas.subscription import ManualActivationSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/super-admin/subscriptions", tags=["Super Admin - Subscriptions"])

# Configuration des plans (cohérente avec subscription_codes.py)
PLAN_CONFIG = {
    "starter": {
        "name": "Starter",
        "price_monthly": 49.99,
        "price_yearly": 479.99,
        "max_products": 500,
        "max_users": 2,
        "max_branches": 1,
    },
    "professional": {
        "name": "Professional",
        "price_monthly": 89.99,
        "price_yearly": 899.99,
        "max_products": 5000,
        "max_users": 10,
        "max_branches": 3,
    },
    "enterprise": {
        "name": "Enterprise",
        "price_monthly": 149.99,
        "price_yearly": 1499.99,
        "max_products": 0,  # Illimité
        "max_users": 0,     # Illimité
        "max_branches": 0,  # Illimité
    }
}


# =============================================================================
# UTILITAIRES
# =============================================================================

def get_pharmacy_subscription(db: Session, pharmacy_id: UUID) -> Optional[PharmacySubscription]:
    """Récupère l'abonnement actif d'une pharmacie"""
    return db.query(PharmacySubscription).filter(
        PharmacySubscription.pharmacy_id == pharmacy_id
    ).first()


def calculate_end_date(duration_days: int) -> datetime:
    """Calcule la date de fin à partir de la durée en jours"""
    return datetime.utcnow() + timedelta(days=duration_days)


def get_plan_limits(plan_type: str) -> Dict[str, Any]:
    """Retourne les limites d'un plan"""
    config = PLAN_CONFIG.get(plan_type, PLAN_CONFIG["professional"])
    return {
        "max_products": config.get("max_products", 0) or 0,
        "max_users": config.get("max_users", 0) or 0,
        "max_branches": config.get("max_branches", 0) or 0,
    }


# =============================================================================
# VUE D'ENSEMBLE
# =============================================================================

@router.get("/overview", response_model=Dict[str, Any])
async def get_subscriptions_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    tenant_id: Optional[str] = Query(None, description="Filtrer par ID de tenant (optionnel)"),
    pharmacy_id: Optional[str] = Query(None, description="Filtrer par ID de pharmacie (optionnel)")
) -> Dict[str, Any]:
    """
    Vue d'ensemble des abonnements des pharmacies/branches.
    """
    logger.info(f"Demande de vue d'ensemble des abonnements par {current_user.email}")
    
    try:
        # Construire la requête de base pour les pharmacies
        pharmacy_query = db.query(Pharmacy)
        
        if tenant_id:
            pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
        
        if pharmacy_id:
            pharmacy_query = pharmacy_query.filter(Pharmacy.id == pharmacy_id)
        
        pharmacies = pharmacy_query.all()
        
        # Statistiques
        pharmacy_subscriptions = []
        total_pharmacies = len(pharmacies)
        trial_pharmacies = 0
        active_paid_pharmacies = 0
        expired_pharmacies = 0
        plans_distribution = {"starter": 0, "professional": 0, "enterprise": 0}
        
        for pharmacy in pharmacies:
            sub = get_pharmacy_subscription(db, pharmacy.id)
            
            if sub:
                sub_data = {
                    "pharmacy_id": str(pharmacy.id),
                    "pharmacy_name": pharmacy.name,
                    "pharmacy_code": pharmacy.pharmacy_code,
                    "tenant_id": str(pharmacy.tenant_id) if pharmacy.tenant_id else None,
                    "tenant_name": pharmacy.tenant.nom_pharmacie if pharmacy.tenant else None,
                    "subscription_id": str(sub.id),
                    "plan": sub.plan.value,
                    "plan_name": sub.plan_name,
                    "status": sub.status.value,
                    "is_active": sub.is_active(),
                    "start_date": sub.start_date.isoformat() if sub.start_date else None,
                    "end_date": sub.end_date.isoformat() if sub.end_date else None,
                    "days_remaining": sub.days_remaining(),
                    "current_price": float(sub.price) if sub.price else 0,
                    "max_products": sub.max_products,
                    "max_users": sub.max_users,
                    "max_branches": sub.max_branches,
                }
                pharmacy_subscriptions.append(sub_data)
                
                # Mise à jour des statistiques
                if sub.status == SubscriptionStatus.ACTIVE:
                    active_paid_pharmacies += 1
                    if sub.plan.value in plans_distribution:
                        plans_distribution[sub.plan.value] += 1
                elif sub.status == SubscriptionStatus.EXPIRED:
                    expired_pharmacies += 1
            else:
                # Pharmacie sans abonnement
                pharmacy_subscriptions.append({
                    "pharmacy_id": str(pharmacy.id),
                    "pharmacy_name": pharmacy.name,
                    "pharmacy_code": pharmacy.pharmacy_code,
                    "tenant_id": str(pharmacy.tenant_id) if pharmacy.tenant_id else None,
                    "tenant_name": pharmacy.tenant.nom_pharmacie if pharmacy.tenant else None,
                    "subscription_id": None,
                    "plan": None,
                    "plan_name": None,
                    "status": "no_subscription",
                    "is_active": False,
                    "start_date": None,
                    "end_date": None,
                    "days_remaining": None,
                    "current_price": 0,
                    "max_products": 0,
                    "max_users": 0,
                    "max_branches": 0,
                })
        
        # Calcul des revenus projetés (uniquement abonnements actifs payants)
        monthly_revenue = sum([
            PLAN_CONFIG.get(sub["plan"], {}).get("price_monthly", 0)
            for sub in pharmacy_subscriptions
            if sub.get("is_active", False) and sub.get("plan") and sub.get("plan") != "starter"
        ])
        
        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "requested_by": current_user.email,
            "filter": {
                "tenant_id": tenant_id if tenant_id else None,
                "pharmacy_id": pharmacy_id if pharmacy_id else None
            },
            "pharmacies": {
                "total": total_pharmacies,
                "active_paid": active_paid_pharmacies,
                "expired": expired_pharmacies,
                "conversion_rate": round(active_paid_pharmacies / max(total_pharmacies, 1) * 100, 2),
                "subscriptions": pharmacy_subscriptions
            },
            "plans_distribution": plans_distribution,
            "revenue": {
                "monthly": round(monthly_revenue, 2),
                "yearly": round(monthly_revenue * 12, 2),
                "average_per_pharmacy": round(monthly_revenue / max(active_paid_pharmacies, 1), 2)
            }
        }
        
        logger.info(f"Vue d'ensemble récupérée: {total_pharmacies} pharmacies, {active_paid_pharmacies} actives")
        return result
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération de la vue d'ensemble: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "overview_failed",
                "message": "Erreur lors de la récupération des données d'abonnement"
            }
        )


# =============================================================================
# ACTIVATION MANUELLE (PAIEMENT CASH) - POUR PHARMACIE/BRANCHE
# =============================================================================

@router.post("/manual-activation/pharmacy", response_model=Dict[str, Any])
async def manual_activate_pharmacy_subscription(
    pharmacy_id: UUID,
    data: ManualActivationSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """
    Activation manuelle d'un abonnement pour une pharmacie/branche (paiement cash).
    Réservé aux super administrateurs pour les paiements hors ligne.
    
    Args:
        pharmacy_id: ID de la pharmacie à activer
        data: Informations d'activation manuelle (plan, période, montant)
        
    Returns:
        Confirmation de l'activation avec détails de l'abonnement
    """
    logger.info(f"Activation manuelle d'abonnement par {current_user.email} pour pharmacie {pharmacy_id}")
    
    # Vérifier que la pharmacie existe
    pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "pharmacy_not_found", "message": "Pharmacie non trouvée"}
        )
    
    # Vérifier que le plan existe
    plan_type = data.plan.value if hasattr(data.plan, 'value') else data.plan
    if plan_type not in PLAN_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_plan",
                "message": f"Le plan {plan_type} n'existe pas. Plans disponibles: {list(PLAN_CONFIG.keys())}"
            }
        )
    
    try:
        # Déterminer la durée en jours
        if data.billing_cycle == "yearly":
            duration_days = 365
        else:
            duration_days = 30
        
        plan_config = PLAN_CONFIG.get(plan_type, PLAN_CONFIG["professional"])
        end_date = calculate_end_date(duration_days)
        limits = get_plan_limits(plan_type)
        
        # Déterminer le prix
        price = data.amount if data.amount else (
            plan_config.get("price_yearly") if data.billing_cycle == "yearly" else plan_config.get("price_monthly")
        )
        
        # Vérifier si un abonnement existe déjà
        existing_sub = get_pharmacy_subscription(db, pharmacy.id)
        
        if existing_sub:
            # Mettre à jour l'abonnement existant
            existing_sub.plan = SubscriptionPlan(plan_type)
            existing_sub.plan_name = plan_config["name"]
            existing_sub.start_date = datetime.utcnow()
            existing_sub.end_date = end_date
            existing_sub.status = SubscriptionStatus.ACTIVE
            existing_sub.billing_cycle = data.billing_cycle
            existing_sub.price = float(price)
            existing_sub.currency = data.currency or "EUR"
            existing_sub.max_products = limits["max_products"]
            existing_sub.max_users = limits["max_users"]
            existing_sub.max_branches = limits["max_branches"]
            existing_sub.updated_at = datetime.utcnow()
            
            subscription = existing_sub
            logger.info(f"Mise à jour de l'abonnement existant pour pharmacie {pharmacy.name}")
        else:
            # Créer un nouvel abonnement
            subscription = PharmacySubscription(
                pharmacy_id=pharmacy.id,
                plan=SubscriptionPlan(plan_type),
                plan_name=plan_config["name"],
                start_date=datetime.utcnow(),
                end_date=end_date,
                status=SubscriptionStatus.ACTIVE,
                billing_cycle=data.billing_cycle,
                price=float(price),
                currency=data.currency or "EUR",
                max_products=limits["max_products"],
                max_users=limits["max_users"],
                max_branches=limits["max_branches"]
            )
            db.add(subscription)
        
        db.flush()
        
        # Ajouter une note dans les métadonnées de la pharmacie si possible
        # (Optionnel: loguer l'activation)
        
        db.commit()
        db.refresh(subscription)
        
        response = {
            "message": f"Abonnement activé manuellement pour la pharmacie {pharmacy.name}",
            "success": True,
            "subscription": {
                "id": str(subscription.id),
                "pharmacy_id": str(pharmacy.id),
                "pharmacy_name": pharmacy.name,
                "pharmacy_code": pharmacy.pharmacy_code,
                "tenant_id": str(pharmacy.tenant_id) if pharmacy.tenant_id else None,
                "tenant_name": pharmacy.tenant.nom_pharmacie if pharmacy.tenant else None,
                "plan": subscription.plan.value,
                "plan_name": subscription.plan_name,
                "status": subscription.status.value,
                "billing_cycle": subscription.billing_cycle,
                "start_date": subscription.start_date.isoformat(),
                "end_date": subscription.end_date.isoformat(),
                "days_remaining": subscription.days_remaining(),
                "current_price": float(subscription.price),
                "max_products": subscription.max_products,
                "max_users": subscription.max_users,
                "max_branches": subscription.max_branches,
                "activated_by": current_user.email,
                "activated_at": datetime.utcnow().isoformat()
            },
            "plan_details": {
                "duration_days": duration_days,
                "price": float(price),
                "currency": data.currency or "EUR"
            }
        }
        
        logger.info(f"Activation manuelle réussie pour pharmacie {pharmacy.name} (plan: {plan_type})")
        return response
        
    except ValueError as e:
        db.rollback()
        logger.error(f"Erreur de validation lors de l'activation manuelle: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_request", "message": str(e)}
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur inattendue lors de l'activation manuelle: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "activation_failed", "message": "Erreur lors de l'activation manuelle"}
        )


# =============================================================================
# ACTIVATION PAR TENANT (TOUTES LES PHARMACIES D'UN TENANT)
# =============================================================================

@router.post("/manual-activation/tenant", response_model=Dict[str, Any])
async def manual_activate_tenant_pharmacies(
    tenant_id: UUID,
    data: ManualActivationSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """
    Active un abonnement pour TOUTES les pharmacies d'un tenant.
    Utile pour les chaînes de pharmacies.
    
    Args:
        tenant_id: ID du tenant
        data: Informations d'activation manuelle
        
    Returns:
        Confirmation de l'activation pour chaque pharmacie
    """
    logger.info(f"Activation massive pour tenant {tenant_id} par {current_user.email}")
    
    # Vérifier que le tenant existe
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "tenant_not_found", "message": "Tenant non trouvé"}
        )
    
    # Récupérer toutes les pharmacies du tenant
    pharmacies = db.query(Pharmacy).filter(Pharmacy.tenant_id == tenant_id).all()
    
    if not pharmacies:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "no_pharmacies", "message": "Aucune pharmacie trouvée pour ce tenant"}
        )
    
    plan_type = data.plan.value if hasattr(data.plan, 'value') else data.plan
    
    if plan_type not in PLAN_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_plan", "message": f"Le plan {plan_type} n'existe pas"}
        )
    
    try:
        # Déterminer la durée en jours
        duration_days = 365 if data.billing_cycle == "yearly" else 30
        plan_config = PLAN_CONFIG.get(plan_type, PLAN_CONFIG["professional"])
        end_date = calculate_end_date(duration_days)
        limits = get_plan_limits(plan_type)
        
        price = data.amount if data.amount else (
            plan_config.get("price_yearly") if data.billing_cycle == "yearly" else plan_config.get("price_monthly")
        )
        
        results = []
        success_count = 0
        
        for pharmacy in pharmacies:
            existing_sub = get_pharmacy_subscription(db, pharmacy.id)
            
            if existing_sub:
                existing_sub.plan = SubscriptionPlan(plan_type)
                existing_sub.plan_name = plan_config["name"]
                existing_sub.start_date = datetime.utcnow()
                existing_sub.end_date = end_date
                existing_sub.status = SubscriptionStatus.ACTIVE
                existing_sub.billing_cycle = data.billing_cycle
                existing_sub.price = float(price)
                existing_sub.max_products = limits["max_products"]
                existing_sub.max_users = limits["max_users"]
                existing_sub.max_branches = limits["max_branches"]
                existing_sub.updated_at = datetime.utcnow()
                subscription = existing_sub
            else:
                subscription = PharmacySubscription(
                    pharmacy_id=pharmacy.id,
                    plan=SubscriptionPlan(plan_type),
                    plan_name=plan_config["name"],
                    start_date=datetime.utcnow(),
                    end_date=end_date,
                    status=SubscriptionStatus.ACTIVE,
                    billing_cycle=data.billing_cycle,
                    price=float(price),
                    currency=data.currency or "EUR",
                    max_products=limits["max_products"],
                    max_users=limits["max_users"],
                    max_branches=limits["max_branches"]
                )
                db.add(subscription)
            
            results.append({
                "pharmacy_id": str(pharmacy.id),
                "pharmacy_name": pharmacy.name,
                "subscription_id": str(subscription.id) if subscription.id else "pending",
                "status": "activated"
            })
            success_count += 1
        
        db.commit()
        
        return {
            "message": f"Abonnement activé pour {success_count} pharmacies du tenant {tenant.nom_pharmacie}",
            "success": True,
            "tenant_id": str(tenant_id),
            "tenant_name": tenant.nom_pharmacie,
            "total_pharmacies": len(pharmacies),
            "activated_count": success_count,
            "subscriptions": results,
            "plan_details": {
                "plan": plan_type,
                "plan_name": plan_config["name"],
                "billing_cycle": data.billing_cycle,
                "duration_days": duration_days,
                "price": float(price),
                "currency": data.currency or "EUR"
            },
            "activated_by": current_user.email,
            "activated_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de l'activation massive: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "bulk_activation_failed", "message": str(e)}
        )


# =============================================================================
# DÉTAILS D'UNE PHARMACIE
# =============================================================================

@router.get("/pharmacy/{pharmacy_id}", response_model=Dict[str, Any])
async def get_pharmacy_subscription_details(
    pharmacy_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """
    Récupère les détails complets de l'abonnement d'une pharmacie.
    """
    logger.info(f"Demande des détails d'abonnement pour pharmacie {pharmacy_id} par {current_user.email}")
    
    try:
        pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
        if not pharmacy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "pharmacy_not_found", "message": "Pharmacie non trouvée"}
            )
        
        subscription = get_pharmacy_subscription(db, pharmacy.id)
        
        result = {
            "pharmacy_id": str(pharmacy.id),
            "pharmacy_name": pharmacy.name,
            "pharmacy_code": pharmacy.pharmacy_code,
            "tenant_id": str(pharmacy.tenant_id) if pharmacy.tenant_id else None,
            "tenant_name": pharmacy.tenant.nom_pharmacie if pharmacy.tenant else None,
            "is_active": pharmacy.is_active,
            "subscription": None
        }
        
        if subscription:
            result["subscription"] = {
                "id": str(subscription.id),
                "plan": subscription.plan.value,
                "plan_name": subscription.plan_name,
                "status": subscription.status.value,
                "is_active": subscription.is_active(),
                "billing_cycle": subscription.billing_cycle,
                "start_date": subscription.start_date.isoformat() if subscription.start_date else None,
                "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
                "days_remaining": subscription.days_remaining(),
                "price": float(subscription.price),
                "currency": subscription.currency,
                "max_products": subscription.max_products,
                "max_users": subscription.max_users,
                "max_branches": subscription.max_branches,
                "created_at": subscription.created_at.isoformat() if subscription.created_at else None,
                "updated_at": subscription.updated_at.isoformat() if subscription.updated_at else None
            }
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "fetch_failed", "message": "Erreur lors de la récupération"}
        )


# =============================================================================
# LISTE DES ABONNEMENTS PAR TENANT
# =============================================================================

@router.get("/tenant/{tenant_id}", response_model=Dict[str, Any])
async def get_tenant_pharmacy_subscriptions(
    tenant_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """
    Récupère tous les abonnements des pharmacies d'un tenant.
    """
    logger.info(f"Demande des abonnements du tenant {tenant_id} par {current_user.email}")
    
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "tenant_not_found", "message": "Tenant non trouvé"}
            )
        
        pharmacies = db.query(Pharmacy).filter(Pharmacy.tenant_id == tenant_id).all()
        
        pharmacy_subs = []
        active_count = 0
        expired_count = 0
        no_subscription_count = 0
        
        for pharmacy in pharmacies:
            sub = get_pharmacy_subscription(db, pharmacy.id)
            
            sub_info = {
                "pharmacy_id": str(pharmacy.id),
                "pharmacy_name": pharmacy.name,
                "pharmacy_code": pharmacy.pharmacy_code,
                "is_active": pharmacy.is_active,
            }
            
            if sub:
                sub_info.update({
                    "subscription_id": str(sub.id),
                    "plan": sub.plan.value,
                    "plan_name": sub.plan_name,
                    "status": sub.status.value,
                    "end_date": sub.end_date.isoformat() if sub.end_date else None,
                    "days_remaining": sub.days_remaining(),
                    "price": float(sub.price)
                })
                
                if sub.is_active():
                    active_count += 1
                else:
                    expired_count += 1
            else:
                sub_info["subscription_id"] = None
                sub_info["status"] = "no_subscription"
                no_subscription_count += 1
            
            pharmacy_subs.append(sub_info)
        
        return {
            "tenant_id": str(tenant_id),
            "tenant_name": tenant.nom_pharmacie,
            "tenant_code": tenant.tenant_code,
            "total_pharmacies": len(pharmacies),
            "statistics": {
                "active_subscriptions": active_count,
                "expired_subscriptions": expired_count,
                "no_subscription": no_subscription_count,
                "coverage_rate": round((active_count + expired_count) / max(len(pharmacies), 1) * 100, 2)
            },
            "pharmacies": pharmacy_subs,
            "requested_at": datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "fetch_failed", "message": "Erreur lors de la récupération"}
        )


# =============================================================================
# PROLONGATION D'ABONNEMENT
# =============================================================================

@router.post("/extend/{pharmacy_id}", response_model=Dict[str, Any])
async def extend_pharmacy_subscription(
    pharmacy_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    extra_days: int = Query(30, ge=1, le=365, description="Nombre de jours supplémentaires (1-365)")
):
    """
    Prolonge l'abonnement d'une pharmacie.
    """
    logger.info(f"Prolongation d'abonnement demandée par {current_user.email} pour pharmacie {pharmacy_id} (+{extra_days} jours)")
    
    try:
        pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
        if not pharmacy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "pharmacy_not_found", "message": "Pharmacie non trouvée"}
            )
        
        subscription = get_pharmacy_subscription(db, pharmacy.id)
        
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "no_subscription", "message": "Aucun abonnement trouvé pour cette pharmacie"}
            )
        
        # Prolonger l'abonnement
        old_end = subscription.end_date
        new_end = old_end + timedelta(days=extra_days)
        
        subscription.end_date = new_end
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.updated_at = datetime.utcnow()
        
        db.commit()
        
        logger.info(f"Prolongation réussie pour pharmacie {pharmacy.name}: {old_end} -> {new_end}")
        
        return {
            "message": f"Abonnement prolongé de {extra_days} jours",
            "success": True,
            "pharmacy_id": str(pharmacy.id),
            "pharmacy_name": pharmacy.name,
            "old_end_date": old_end.isoformat(),
            "new_end_date": new_end.isoformat(),
            "days_remaining": subscription.days_remaining(),
            "extended_by": current_user.email,
            "extended_at": datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur lors de la prolongation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "extension_failed", "message": "Erreur lors de la prolongation"}
        )


# =============================================================================
# STATISTIQUES AVANCÉES
# =============================================================================

@router.get("/statistics", response_model=Dict[str, Any])
async def get_subscription_statistics(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """
    Statistiques avancées sur les abonnements des pharmacies.
    """
    logger.info(f"Demande de statistiques avancées par {current_user.email}")
    
    try:
        # Statistiques globales
        total_pharmacies = db.query(Pharmacy).count()
        pharmacies_with_subscription = db.query(PharmacySubscription).distinct(PharmacySubscription.pharmacy_id).count()
        
        # Statistiques par statut
        status_stats = db.query(
            PharmacySubscription.status,
            func.count(PharmacySubscription.id).label("count")
        ).group_by(PharmacySubscription.status).all()
        
        # Statistiques par plan
        plan_stats = db.query(
            PharmacySubscription.plan,
            func.count(PharmacySubscription.id).label("count")
        ).group_by(PharmacySubscription.plan).all()
        
        # Abonnements expirant dans les 30 jours
        now = datetime.utcnow()
        expiring_soon = db.query(PharmacySubscription).filter(
            PharmacySubscription.status == SubscriptionStatus.ACTIVE,
            PharmacySubscription.end_date.between(now, now + timedelta(days=30))
        ).count()
        
        # Abonnements expirés
        expired = db.query(PharmacySubscription).filter(
            PharmacySubscription.status == SubscriptionStatus.EXPIRED
        ).count()
        
        # Calcul des revenus mensuels récurrents (MRR)
        active_subs = db.query(PharmacySubscription).filter(
            PharmacySubscription.status == SubscriptionStatus.ACTIVE
        ).all()
        
        monthly_revenue = sum([float(sub.price) for sub in active_subs if sub.billing_cycle == "monthly"])
        yearly_revenue = sum([float(sub.price) / 12 for sub in active_subs if sub.billing_cycle == "yearly"])
        total_mrr = round(monthly_revenue + yearly_revenue, 2)
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "overview": {
                "total_pharmacies": total_pharmacies,
                "pharmacies_with_subscription": pharmacies_with_subscription,
                "coverage_rate": round(pharmacies_with_subscription / max(total_pharmacies, 1) * 100, 2)
            },
            "by_status": {status.value: count for status, count in status_stats},
            "by_plan": {plan.value: count for plan, count in plan_stats},
            "alerts": {
                "expiring_soon_30d": expiring_soon,
                "expired": expired
            },
            "revenue": {
                "monthly_recurring_revenue": total_mrr,
                "annual_projected": round(total_mrr * 12, 2),
                "average_revenue_per_pharmacy": round(total_mrr / max(pharmacies_with_subscription, 1), 2)
            }
        }
        
    except Exception as e:
        logger.error(f"Erreur lors du calcul des statistiques: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "statistics_failed", "message": "Erreur lors du calcul des statistiques"}
        )


# =============================================================================
# RECHERCHE ET FILTRES
# =============================================================================

@router.get("/search", response_model=Dict[str, Any])
async def search_pharmacy_subscriptions(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    query: str = Query(..., min_length=2, description="Terme de recherche"),
    status: Optional[str] = Query(None, regex="^(active|expired|trial|cancelled)$", description="Statut de l'abonnement"),
    plan: Optional[str] = Query(None, regex="^(starter|professional|enterprise)$", description="Type de plan"),
    tenant_id: Optional[UUID] = Query(None, description="ID du tenant")
):
    """
    Recherche d'abonnements par nom de pharmacie, code ou tenant.
    """
    logger.info(f"Recherche d'abonnements: '{query}' par {current_user.email}")
    
    try:
        search_pattern = f"%{query.lower()}%"
        
        # Requête de base: pharmacies avec leurs abonnements
        pharmacy_query = db.query(Pharmacy, PharmacySubscription).outerjoin(
            PharmacySubscription, PharmacySubscription.pharmacy_id == Pharmacy.id
        )
        
        # Filtre de recherche
        pharmacy_query = pharmacy_query.filter(
            or_(
                Pharmacy.name.ilike(search_pattern),
                Pharmacy.pharmacy_code.ilike(search_pattern),
                Pharmacy.city.ilike(search_pattern)
            )
        )
        
        if tenant_id:
            pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
        
        results = []
        for pharmacy, subscription in pharmacy_query.limit(50).all():
            result_item = {
                "pharmacy_id": str(pharmacy.id),
                "pharmacy_name": pharmacy.name,
                "pharmacy_code": pharmacy.pharmacy_code,
                "city": pharmacy.city,
                "tenant_id": str(pharmacy.tenant_id) if pharmacy.tenant_id else None,
                "tenant_name": pharmacy.tenant.nom_pharmacie if pharmacy.tenant else None,
            }
            
            if subscription:
                # Filtrer par statut si spécifié
                if status and subscription.status.value != status:
                    continue
                # Filtrer par plan si spécifié
                if plan and subscription.plan.value != plan:
                    continue
                    
                result_item["subscription"] = {
                    "id": str(subscription.id),
                    "plan": subscription.plan.value,
                    "plan_name": subscription.plan_name,
                    "status": subscription.status.value,
                    "is_active": subscription.is_active(),
                    "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
                    "days_remaining": subscription.days_remaining(),
                    "price": float(subscription.price)
                }
            else:
                if status or plan:
                    continue  # Filtrer les pharmacies sans abonnement si on filtre par statut/plan
                result_item["subscription"] = None
            
            results.append(result_item)
        
        return {
            "query": query,
            "filters": {
                "status": status,
                "plan": plan,
                "tenant_id": str(tenant_id) if tenant_id else None
            },
            "total_results": len(results),
            "results": results,
            "requested_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de la recherche: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "search_failed", "message": "Erreur lors de la recherche"}
        )


# =============================================================================
# ENDPOINT DE SANTÉ
# =============================================================================

@router.get("/health")
async def health_check():
    """
    Vérifie que le service est opérationnel.
    """
    return {
        "status": "healthy",
        "service": "super-admin-pharmacy-subscriptions-api",
        "timestamp": datetime.utcnow().isoformat(),
        "model": "PharmacySubscription (abonnement par pharmacie/branche)",
        "plans_available": list(PLAN_CONFIG.keys())
    }
# app/api/v1/subscription_codes.py
"""
Gestion des codes d'abonnement pour les BRANCHES.
Un abonnement est lié à une branche, et tous ses utilisateurs en bénéficient.
"""

import logging
import random
import string
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_active_user, get_super_admin_user, get_current_branch
from app.config.plans import PLAN_CONFIG, get_plan_config
from app.models.subscription_code import SubscriptionCode, SubscriptionCodeStatus
from app.models.branch_subscription import BranchSubscription, SubscriptionPlan, SubscriptionStatus
from app.models.branch import Branch
from app.models.user import User
from app.schemas.subscription import (
    SubscriptionCodeCreate,
    ActivateSubscriptionCode,
    BranchSubscriptionResponse,
    BranchSubscriptionStatusResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscription-codes", tags=["Subscription Codes"])


def generate_unique_code(length: int = 8) -> str:
    """Génère un code unique alphanumérique"""
    chars = string.ascii_uppercase + string.digits
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '')
    return ''.join(random.choices(chars, k=length))


def format_code_with_dashes(code: str) -> str:
    """Formate le code avec des tirets tous les 4 caractères"""
    clean = code.replace('-', '').replace(' ', '').upper()
    if len(clean) >= 8:
        return f"{clean[:4]}-{clean[4:8]}"
    return code


# =============================================================================
# SUPER ADMIN - GESTION DES CODES
# =============================================================================

@router.post("/admin/generate", response_model=Dict[str, Any])
async def generate_subscription_code(
    data: SubscriptionCodeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """
    Génère un code d'abonnement pour une BRANCHE spécifique.
    Accessible uniquement aux super admins.
    """
    logger.info(f"Génération de code par {current_user.email} pour branche {data.branch_id} plan {data.plan_type}")
    
    # Vérifier que le plan existe
    if data.plan_type not in PLAN_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_plan", "message": f"Le plan {data.plan_type} n'existe pas."}
        )
    
    # ✅ Vérifier que la branche existe
    branch = db.query(Branch).filter(Branch.id == data.branch_id).first()
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "branch_not_found", "message": "La branche spécifiée n'existe pas."}
        )
    
    plan_config = get_plan_config(data.plan_type)
    
    # Générer un code unique
    attempts = 0
    max_attempts = 10
    generated_code = None
    
    while attempts < max_attempts:
        raw_code = generate_unique_code(8)
        formatted_code = format_code_with_dashes(raw_code)
        
        existing = db.query(SubscriptionCode).filter(
            SubscriptionCode.code == formatted_code
        ).first()
        
        if not existing:
            generated_code = formatted_code
            break
        attempts += 1
    
    if not generated_code:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "code_generation_failed", "message": "Impossible de générer un code unique."}
        )
    
    # Durée en jours
    duration_days = data.duration_days or 30
    
    # Prix (en cents)
    price = data.price
    if not price:
        price = plan_config.get("price_monthly", 0)
    
    # Créer le code
    code = SubscriptionCode(
        code=generated_code,
        branch_id=data.branch_id,  # ✅ Lié à la branche
        tenant_id=branch.tenant_id,
        pharmacy_id=branch.parent_pharmacy_id,
        plan_type=data.plan_type,
        plan_name=plan_config["name"],
        duration_days=duration_days,
        price=int(price * 100) if price else 0,
        currency=data.currency or "EUR",
        valid_from=datetime.utcnow(),
        valid_until=data.valid_until or (datetime.utcnow() + timedelta(days=data.expiry_days or 90)),
        notes=data.notes,
        created_by_user_id=current_user.id,
        status=SubscriptionCodeStatus.PENDING,
    )
    
    db.add(code)
    db.commit()
    db.refresh(code)
    
    logger.info(f"Code généré: {generated_code} pour branche {branch.name}")
    
    return {
        "success": True,
        "code": code.code,
        "plan_type": code.plan_type,
        "plan_name": code.plan_name,
        "price": price,
        "currency": code.currency,
        "duration_days": code.duration_days,
        "valid_until": code.valid_until.isoformat(),
        "created_at": code.created_at.isoformat(),
        "status": code.status.value,
        "branch_id": str(code.branch_id),
        "branch_name": branch.name,
        "pharmacy_id": str(branch.parent_pharmacy_id),
        "pharmacy_name": branch.parent_pharmacy.name if branch.parent_pharmacy else None
    }


@router.get("/admin/list", response_model=Dict[str, Any])
async def list_subscription_codes(
    status: Optional[str] = Query(None),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par branche"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """Liste tous les codes d'abonnement générés."""
    query = db.query(SubscriptionCode)
    
    if status:
        try:
            status_enum = SubscriptionCodeStatus(status)
            query = query.filter(SubscriptionCode.status == status_enum)
        except ValueError:
            pass
    
    if branch_id:
        query = query.filter(SubscriptionCode.branch_id == branch_id)
    
    total = query.count()
    codes = query.order_by(SubscriptionCode.created_at.desc())\
                 .offset((page - 1) * limit)\
                 .limit(limit)\
                 .all()
    
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "codes": [
            {
                "id": str(code.id),
                "code": code.code,
                "plan_type": code.plan_type,
                "plan_name": code.plan_name,
                "price": float(code.price / 100) if code.price else 0,
                "currency": code.currency,
                "duration_days": code.duration_days,
                "status": code.status.value,
                "valid_until": code.valid_until.isoformat() if code.valid_until else None,
                "created_at": code.created_at.isoformat(),
                "branch_id": str(code.branch_id) if code.branch_id else None,
                "branch_name": code.branch.name if code.branch else None,
                "created_by": code.created_by_user.email if code.created_by_user else None
            }
            for code in codes
        ]
    }


# =============================================================================
# ACTIVATION PAR L'UTILISATEUR
# =============================================================================

def activate_branch_subscription(
    db: Session,
    branch: Branch,
    code: SubscriptionCode,
    activated_by: User
) -> BranchSubscription:
    """
    Active un abonnement pour une branche avec un code.
    """
    plan_config = get_plan_config(code.plan_type)
    now = datetime.utcnow()
    end_date = now + timedelta(days=code.duration_days)
    
    # Vérifier si la branche a déjà un abonnement
    existing_sub = db.query(BranchSubscription).filter(
        BranchSubscription.branch_id == branch.id
    ).first()
    
    price_value = float(code.price / 100) if code.price else 0.0
    
    if existing_sub:
        # Mettre à jour l'abonnement existant
        existing_sub.plan = SubscriptionPlan(code.plan_type)
        existing_sub.plan_name = plan_config["name"]
        existing_sub.start_date = now
        existing_sub.end_date = end_date
        existing_sub.status = SubscriptionStatus.ACTIVE
        existing_sub.billing_cycle = "yearly" if code.duration_days >= 365 else "monthly"
        existing_sub.price = price_value
        existing_sub.currency = code.currency or "EUR"
        existing_sub.max_products = plan_config.get("max_products", 100)
        existing_sub.max_users = plan_config.get("max_users", 5)
        existing_sub.updated_at = now
        subscription = existing_sub
    else:
        # Créer un nouvel abonnement
        subscription = BranchSubscription(
            branch_id=branch.id,
            tenant_id=branch.tenant_id,
            pharmacy_id=branch.parent_pharmacy_id,
            plan=SubscriptionPlan(code.plan_type),
            plan_name=plan_config["name"],
            start_date=now,
            end_date=end_date,
            status=SubscriptionStatus.ACTIVE,
            billing_cycle="yearly" if code.duration_days >= 365 else "monthly",
            price=price_value,
            currency=code.currency or "EUR",
            max_products=plan_config.get("max_products", 100),
            max_users=plan_config.get("max_users", 5),
            max_storage_mb=plan_config.get("max_storage_mb", 100)
        )
        db.add(subscription)
    
    db.flush()
    return subscription


@router.post("/activate", response_model=Dict[str, Any])
async def activate_code_for_branch(
    data: ActivateSubscriptionCode,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Any:
    """
    Active un abonnement pour la BRANCHE active de l'utilisateur.
    Le code est lié à la branche.
    Tous les utilisateurs de la branche bénéficieront de cet abonnement.
    """
    logger.info(f"Activation de code par {current_user.email}")
    
    # ✅ Vérifier que l'utilisateur a une branche active
    if not current_user.active_branch_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "no_active_branch",
                "message": "Aucune branche active sélectionnée. Veuillez d'abord sélectionner une branche."
            }
        )
    
    branch = db.query(Branch).filter(Branch.id == current_user.active_branch_id).first()
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "branch_not_found", "message": "Branche non trouvée."}
        )
    
    # Chercher le code
    clean_code = data.code.strip().upper().replace('-', '').replace(' ', '')
    search_variations = [
        data.code.strip().upper(),
        format_code_with_dashes(clean_code),
        clean_code
    ]
    
    code = None
    for search_code in search_variations:
        code = db.query(SubscriptionCode).filter(
            SubscriptionCode.code == search_code,
            SubscriptionCode.status == SubscriptionCodeStatus.PENDING
        ).first()
        if code:
            break
    
    if not code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "invalid_code", "message": "Code invalide ou déjà utilisé."}
        )
    
    # ✅ Vérifier que le code correspond à cette branche
    if code.branch_id and code.branch_id != branch.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "wrong_branch",
                "message": f"Ce code est réservé à une autre branche. Contactez votre administrateur."
            }
        )
    
    if not code.is_valid():
        status_text = "expiré" if code.valid_until and datetime.utcnow() > code.valid_until else "non disponible"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "code_expired", "message": f"Ce code est {status_text}."}
        )
    
    try:
        # Activer l'abonnement pour la branche
        subscription = activate_branch_subscription(
            db=db,
            branch=branch,
            code=code,
            activated_by=current_user
        )
        
        # Marquer le code comme utilisé
        code.status = SubscriptionCodeStatus.ACTIVATED
        code.activated_by_user_id = current_user.id
        code.activated_at = datetime.utcnow()
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Abonnement activé pour la branche {branch.name}",
            "subscription": {
                "id": str(subscription.id),
                "branch_id": str(branch.id),
                "branch_name": branch.name,
                "plan": subscription.plan.value,
                "plan_name": subscription.plan_name,
                "start_date": subscription.start_date.isoformat(),
                "end_date": subscription.end_date.isoformat(),
                "days_remaining": subscription.days_remaining(),
                "max_users": subscription.max_users,
                "max_products": subscription.max_products
            },
            "code": {
                "code": code.code,
                "plan": code.plan_type,
                "duration_days": code.duration_days
            }
        }
        
    except Exception as exc:
        db.rollback()
        logger.error(f"Erreur activation: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "activation_failed", "message": "Erreur lors de l'activation."}
        )


# =============================================================================
# STATUT DE L'ABONNEMENT D'UNE BRANCHE
# =============================================================================

@router.get("/branch/{branch_id}/status", response_model=BranchSubscriptionStatusResponse)
async def get_branch_subscription_status(
    branch_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Any:
    """
    Récupère le statut de l'abonnement d'une branche.
    L'utilisateur doit appartenir à cette branche.
    """
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branche non trouvée")
    
    # Vérifier que l'utilisateur appartient à cette branche
    if current_user.branch_id != branch_id and current_user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    subscription = db.query(BranchSubscription).filter(
        BranchSubscription.branch_id == branch_id
    ).first()
    
    # Compter les utilisateurs de la branche
    from app.models.user import User
    current_users = db.query(User).filter(User.branch_id == branch_id, User.is_active == True).count()
    
    # Compter les produits de la branche
    from app.models.product import Product
    current_products = db.query(Product).filter(Product.branch_id == branch_id, Product.is_active == True).count()
    
    if not subscription or not subscription.is_active():
        return BranchSubscriptionStatusResponse(
            branch_id=branch.id,
            branch_name=branch.name,
            has_active_subscription=False,
            plan=None,
            plan_name=None,
            status=None,
            days_remaining=0,
            is_trial=False,
            trial_days_remaining=0,
            max_users=0,
            current_users=current_users,
            max_products=0,
            current_products=current_products,
            can_add_users=False,
            can_add_products=False
        )
    
    return BranchSubscriptionStatusResponse(
        branch_id=branch.id,
        branch_name=branch.name,
        has_active_subscription=True,
        plan=subscription.plan.value,
        plan_name=subscription.plan_name,
        status=subscription.status.value,
        days_remaining=subscription.days_remaining(),
        is_trial=subscription.is_trial(),
        trial_days_remaining=subscription.trial_days_remaining(),
        max_users=subscription.max_users,
        current_users=current_users,
        max_products=subscription.max_products,
        current_products=current_products,
        can_add_users=current_users < subscription.max_users,
        can_add_products=subscription.max_products == 0 or current_products < subscription.max_products
    )


# =============================================================================
# VALIDATION DU CODE
# =============================================================================

@router.get("/validate", response_model=Dict[str, Any])
@router.post("/validate", response_model=Dict[str, Any])
async def validate_code(
    code: str = Query(None, description="Code à valider"),
    db: Session = Depends(get_db),
    data: Optional[ActivateSubscriptionCode] = None,
) -> Any:
    """Valide un code sans l'activer."""
    code_to_validate = data.code if data else code
    
    if not code_to_validate:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "missing_code", "message": "Code d'abonnement requis."}
        )
    
    clean_code = code_to_validate.strip().upper().replace('-', '').replace(' ', '')
    search_variations = [
        code_to_validate.strip().upper(),
        format_code_with_dashes(clean_code),
        clean_code,
    ]
    
    code_obj = None
    for search_code in search_variations:
        code_obj = db.query(SubscriptionCode).filter(
            SubscriptionCode.code == search_code
        ).first()
        if code_obj:
            break
    
    if not code_obj:
        return {"valid": False, "message": "Code invalide."}
    
    if not code_obj.is_valid():
        status_text = "expiré" if code_obj.valid_until and datetime.utcnow() > code_obj.valid_until else "déjà utilisé"
        return {
            "valid": False,
            "message": f"Code {status_text}.",
            "status": code_obj.status.value,
            "valid_until": code_obj.valid_until.isoformat() if code_obj.valid_until else None
        }
    
    return {
        "valid": True,
        "message": "Code valide.",
        "plan": {
            "type": code_obj.plan_type,
            "name": code_obj.plan_name,
            "duration_days": code_obj.duration_days
        },
        "price": float(code_obj.price / 100) if code_obj.price else 0,
        "currency": code_obj.currency or "EUR",
        "valid_until": code_obj.valid_until.isoformat() if code_obj.valid_until else None,
        "code": code_obj.code,
        "branch_id": str(code_obj.branch_id) if code_obj.branch_id else None
    }
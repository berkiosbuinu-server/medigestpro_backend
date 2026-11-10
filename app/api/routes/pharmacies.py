from fastapi import APIRouter, Depends, HTTPException, status, Path, Query
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import json
from uuid import UUID
import pytz
from sqlalchemy import func
import os
import shutil
from fastapi import UploadFile, File

from app.api.deps import get_current_user, get_db, get_current_tenant, get_current_active_pharmacy
from app.models.user import User
from app.models.tenant import Tenant
from app.models.pharmacy import Pharmacy
from app.models.branch import Branch
from app.schemas.pharmacy import (
    PharmacyCreate, 
    PharmacyUpdate, 
    PharmacyResponse,
    PharmacyConfigUpdate,
    PharmacyConfigResponse,
    CurrencyConfig,
    WorkingHoursConfig,
    AutomaticPricingConfig,
    BranchConfig,
    ThemeConfig,
    SalesConfig
)
from app.schemas.branch import BranchCreate, BranchUpdate, BranchResponse, BranchListResponse, BranchServiceStatus, BranchStatistics, BranchConfigUpdate, BranchWorkingHoursConfig
from app.utils.pharmacy_utils import PharmacyValidator
from app.schemas.branch import BranchWorkingHoursConfig

router = APIRouter(prefix="", tags=["pharmacies"])

class PharmacyLimits:
    """Définit les limites de pharmacies selon le plan d'abonnement"""
    
    @staticmethod
    def get_limits_for_plan(plan: str, subscription=None) -> dict:
        """Utilise l'abonnement pour déterminer les limites"""
        # Si on a un abonnement, l'utiliser
        if subscription:
            max_branches = subscription.max_branches if hasattr(subscription, 'max_branches') else 0
            return {
                "max_pharmacies": 1 if subscription.plan == "trial" else 10,
                "max_branches_per_pharmacy": max_branches,
                "description": subscription.plan_name
            }
        
        # Fallback sur les anciennes limites
        limits = {
            "essentiel": {"max_pharmacies": 1, "max_branches_per_pharmacy": 0},
            "starter": {"max_pharmacies": 1, "max_branches_per_pharmacy": 0},
            "basic": {"max_pharmacies": 1, "max_branches_per_pharmacy": 0},
            "professionnel": {"max_pharmacies": 2, "max_branches_per_pharmacy": 0},
            "professional": {"max_pharmacies": 2, "max_branches_per_pharmacy": 0},
            "entreprise": {"max_pharmacies": 10, "max_branches_per_pharmacy": 0},  # 0 = illimité
            "enterprise": {"max_pharmacies": 10, "max_branches_per_pharmacy": 0},
            "premium": {"max_pharmacies": 10, "max_branches_per_pharmacy": 0},
            "trial": {"max_pharmacies": 1, "max_branches_per_pharmacy": 0}
        }
        
        plan_lower = plan.lower() if plan else "essentiel"
        for key, value in limits.items():
            if key in plan_lower:
                return value
        
        return {"max_pharmacies": 1, "max_branches_per_pharmacy": 0}
    
    @staticmethod
    def can_create_pharmacy(
        db: Session, 
        tenant: Tenant, 
        check_active_only: bool = True
    ) -> dict:
        """Vérifie si le tenant peut créer une nouvelle pharmacie"""
        query = db.query(Pharmacy).filter(Pharmacy.tenant_id == tenant.id)
        
        if check_active_only:
            query = query.filter(Pharmacy.is_active == True)
        
        current_count = query.count()
        
        plan = tenant.current_plan or "essentiel"
        limits = PharmacyLimits.get_limits_for_plan(plan)
        max_allowed = limits["max_pharmacies"]
        
        can_create = current_count < max_allowed
        
        result = {
            "can_create": can_create,
            "reason": "" if can_create else f"Limite de {max_allowed} pharmacies atteinte pour le plan {plan}",
            "current_count": current_count,
            "max_allowed": max_allowed,
            "remaining": max(0, max_allowed - current_count),
            "plan": plan,
            "plan_description": limits["description"],
            "max_branches_per_pharmacy": limits["max_branches_per_pharmacy"]
        }
        
        return result
    
    @staticmethod
    def can_create_branch(pharmacy: Pharmacy, tenant: Tenant, db: Session = None) -> dict:
        """Vérifie si une pharmacie peut créer une succursale"""
        current_branches = db.query(Branch).filter(
            Branch.parent_pharmacy_id == pharmacy.id,
            Branch.is_active == True
        ).count()
        
        # Vérifier d'abord l'abonnement
        if pharmacy.subscription:
            max_branches = pharmacy.subscription.max_branches
            # 0 = illimité
            if max_branches == 0:
                return {
                    "can_create": True,
                    "current_branches": current_branches,
                    "max_branches_allowed": float('inf'),
                    "remaining": float('inf'),
                    "reason": ""
                }
            can_create = current_branches < max_branches
            return {
                "can_create": can_create,
                "current_branches": current_branches,
                "max_branches_allowed": max_branches,
                "remaining": max(0, max_branches - current_branches),
                "reason": "" if can_create else f"Limite de {max_branches} succursales atteinte pour le plan {pharmacy.subscription.plan_name}"
            }
        
        # Fallback sur les anciennes limites
        limits = PharmacyLimits.get_limits_for_plan(tenant.current_plan or "essentiel")
        max_branches = limits["max_branches_per_pharmacy"]
        
        # 0 = illimité
        if max_branches == 0:
            return {
                "can_create": True,
                "current_branches": current_branches,
                "max_branches_allowed": float('inf'),
                "remaining": float('inf'),
                "reason": ""
            }
        
        can_create = current_branches < max_branches
        
        return {
            "can_create": can_create,
            "current_branches": current_branches,
            "max_branches_allowed": max_branches,
            "remaining": max(0, max_branches - current_branches),
            "reason": "" if can_create else f"Limite de {max_branches} succursales atteinte"
        }

# ==================== PHARMACIES CRUD ====================

@router.get("/", response_model=List[PharmacyResponse])
def get_pharmacies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
    skip: int = 0,
    limit: int = 100,
    active_only: bool = True
):
    """Récupère toutes les pharmacies du tenant"""
    query = db.query(Pharmacy).filter(Pharmacy.tenant_id == current_tenant.id)
    
    if active_only:
        query = query.filter(Pharmacy.is_active == True)
    
    pharmacies = query.offset(skip).limit(limit).all()
    
    result = []
    for pharmacy in pharmacies:
        pharmacy_dict = {
            "id": str(pharmacy.id),
            "tenant_id": str(pharmacy.tenant_id),
            "nom": pharmacy.name,
            "name": pharmacy.name,
            "license_number": pharmacy.license_number,
            "address": pharmacy.address,
            "city": pharmacy.city,
            "country": pharmacy.country,
            "phone": pharmacy.phone,
            "email": pharmacy.email,
            "is_active": pharmacy.is_active,
            "opening_hours": pharmacy.opening_hours,
            "pharmacist_in_charge": pharmacy.pharmacist_in_charge,
            "pharmacist_license": pharmacy.pharmacist_license,
            "config": pharmacy.config,
            "created_at": pharmacy.created_at,
            "updated_at": pharmacy.updated_at
        }
        result.append(pharmacy_dict)
    
    return result


@router.get("/limits", response_model=dict)
def get_pharmacy_limits(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Récupère les limites de pharmacies pour le tenant actuel"""
    limits_info = PharmacyLimits.can_create_pharmacy(db, current_tenant)
    
    return {
        "tenant_id": str(current_tenant.id),
        "tenant_name": current_tenant.nom_pharmacie,
        "current_plan": current_tenant.current_plan or "essentiel",
        "limits": PharmacyLimits.get_limits_for_plan(current_tenant.current_plan or "essentiel"),
        "current_pharmacies_count": limits_info["current_count"],
        "max_pharmacies_allowed": limits_info["max_allowed"],
        "remaining_pharmacies": limits_info["remaining"],
        "can_create_more": limits_info["can_create"],
        "max_branches_per_pharmacy": limits_info["max_branches_per_pharmacy"]
    }


@router.get("/active", response_model=dict)
def get_active_pharmacy(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Récupère la pharmacie active pour l'utilisateur connecté.
    Si l'utilisateur a une pharmacie active en session, la retourne.
    Sinon, retourne la première pharmacie active du tenant.
    """
    # Vérifier si l'utilisateur a une pharmacie active en session
    if current_user.active_pharmacy_id:
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == current_user.active_pharmacy_id,
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active == True
        ).first()
        
        if pharmacy:
            return {
                "id": str(pharmacy.id),
                "name": pharmacy.name,
                "license_number": pharmacy.license_number,
                "config": pharmacy.config,
                "branches_count": db.query(Branch).filter(
                    Branch.parent_pharmacy_id == pharmacy.id,
                    Branch.is_active == True
                ).count(),
                "is_active": pharmacy.is_active
            }
    
    # Sinon, récupérer la première pharmacie active
    first_pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active == True
    ).first()
    
    if first_pharmacy:
        # Mettre à jour l'utilisateur avec cette pharmacie
        current_user.active_pharmacy_id = first_pharmacy.id
        db.commit()
        
        return {
            "id": str(first_pharmacy.id),
            "name": first_pharmacy.name,
            "license_number": first_pharmacy.license_number,
            "config": first_pharmacy.config,
            "branches_count": db.query(Branch).filter(
                Branch.parent_pharmacy_id == first_pharmacy.id,
                Branch.is_active == True
            ).count(),
            "is_active": first_pharmacy.is_active
        }
    
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Aucune pharmacie active trouvée pour ce tenant"
    )


@router.post("/active/{pharmacy_id}", response_model=dict)
def set_active_pharmacy(
    pharmacy_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Définit la pharmacie active pour l'utilisateur connecté.
    """
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active == True
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée ou inactive"
        )
    
    # Mettre à jour l'utilisateur
    current_user.active_pharmacy_id = pharmacy.id
    db.commit()
    
    return {
        "id": str(pharmacy.id),
        "name": pharmacy.name,
        "message": "Pharmacie active mise à jour avec succès"
    }


@router.post("/", response_model=PharmacyResponse, status_code=status.HTTP_201_CREATED)
def create_pharmacy(
    pharmacy_in: PharmacyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Crée une nouvelle pharmacie avec configuration par défaut"""
    
    # Vérifier les limites selon le plan
    limits_check = PharmacyLimits.can_create_pharmacy(db, current_tenant)
    
    if not limits_check["can_create"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=limits_check["reason"]
        )
    
    # Valider le numéro de licence
    if not PharmacyValidator.validate_license_number(pharmacy_in.license_number, pharmacy_in.country):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Numéro de licence invalide"
        )
    
    # Vérifier l'unicité du numéro de licence
    existing = db.query(Pharmacy).filter(
        Pharmacy.license_number == pharmacy_in.license_number
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce numéro de licence est déjà utilisé"
        )
    
    # Configuration par défaut complète
    default_config = {
        "pharmacyInfo": {
            "name": pharmacy_in.nom or pharmacy_in.name,
            "address": pharmacy_in.address or "",
            "phone": pharmacy_in.phone or "",
            "email": pharmacy_in.email or "",
            "licenseNumber": pharmacy_in.license_number
        },
        "currencies": [
            {"code": "CDF", "symbol": "FC", "isActive": True, "exchangeRate": 2500},
            {"code": "USD", "symbol": "$", "isActive": True, "exchangeRate": 1}
        ],
        "primaryCurrency": "CDF",
        "taxRate": 16,
        "lowStockThreshold": 10,
        "expiryWarningDays": 90,
        "allowNegativeStock": False,
        "workingHours": {
            "enabled": True,
            "startTime": "08:00",
            "endTime": "20:00",
            "overtimeEndTime": "22:00",
            "daysOff": {
                "monday": True,
                "tuesday": True,
                "wednesday": True,
                "thursday": True,
                "friday": True,
                "saturday": True,
                "sunday": False
            },
            "timezone": "Africa/Kinshasa"
        },
        "productReturnDays": 30,
        "marginConfig": {
            "defaultMargin": 25,
            "minMargin": 10,
            "maxMargin": 50
        },
        "automaticPricing": {
            "enabled": False,
            "method": "percentage",
            "value": 25
        },
        "theme": "system",
        "initialCapital": 0,
        "branchConfig": {
            "maxBranches": limits_check["max_branches_per_pharmacy"],
            "currentBranches": 0,
            "branches": []
        },
        # Champs pour la configuration de vente
        "salesType": {
            "type": "both"
        },
        "calcul_auto_prix": True,
        "marge_par_defaut": 25,
        "taux_tva": 16,
        "lock_stock_modification": False,
        "createdAt": datetime.utcnow().isoformat(),
        "updatedAt": datetime.utcnow().isoformat()
    }
    
    # Créer la pharmacie
    pharmacy = Pharmacy(
        name=pharmacy_in.nom or pharmacy_in.name,
        license_number=pharmacy_in.license_number,
        address=pharmacy_in.address,
        city=pharmacy_in.city,
        country=pharmacy_in.country,
        phone=pharmacy_in.phone,
        email=pharmacy_in.email,
        is_active=pharmacy_in.is_active,
        opening_hours=pharmacy_in.opening_hours,
        pharmacist_in_charge=pharmacy_in.pharmacist_in_charge,
        pharmacist_license=pharmacy_in.pharmacist_license,
        config=default_config,
        tenant_id=current_tenant.id
    )
    
    db.add(pharmacy)
    db.commit()
    db.refresh(pharmacy)
    
    # Mettre à jour les statistiques du tenant
    if current_tenant.meta_data is None:
        current_tenant.meta_data = {}
    
    if "pharmacies_stats" not in current_tenant.meta_data:
        current_tenant.meta_data["pharmacies_stats"] = {}
    
    current_tenant.meta_data["pharmacies_stats"]["last_created"] = datetime.utcnow().isoformat()
    current_tenant.meta_data["pharmacies_stats"]["total_created"] = (
        current_tenant.meta_data["pharmacies_stats"].get("total_created", 0) + 1
    )
    
    db.commit()
    
    # Définir comme pharmacie active si c'est la première
    if limits_check["current_count"] == 0:
        current_user.active_pharmacy_id = pharmacy.id
        db.commit()
    
    pharmacy_dict = {
        "id": str(pharmacy.id),
        "tenant_id": str(pharmacy.tenant_id),
        "nom": pharmacy.name,
        "name": pharmacy.name,
        "license_number": pharmacy.license_number,
        "address": pharmacy.address,
        "city": pharmacy.city,
        "country": pharmacy.country,
        "phone": pharmacy.phone,
        "email": pharmacy.email,
        "is_active": pharmacy.is_active,
        "opening_hours": pharmacy.opening_hours,
        "pharmacist_in_charge": pharmacy.pharmacist_in_charge,
        "pharmacist_license": pharmacy.pharmacist_license,
        "config": pharmacy.config,
        "created_at": pharmacy.created_at,
        "updated_at": pharmacy.updated_at
    }
    
    return pharmacy_dict


@router.get("/{pharmacy_id}", response_model=PharmacyResponse)
def get_pharmacy(
    pharmacy_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Récupère une pharmacie spécifique"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    pharmacy_dict = {
        "id": str(pharmacy.id),
        "tenant_id": str(pharmacy.tenant_id),
        "nom": pharmacy.name,
        "name": pharmacy.name,
        "license_number": pharmacy.license_number,
        "address": pharmacy.address,
        "city": pharmacy.city,
        "country": pharmacy.country,
        "phone": pharmacy.phone,
        "email": pharmacy.email,
        "is_active": pharmacy.is_active,
        "opening_hours": pharmacy.opening_hours,
        "pharmacist_in_charge": pharmacy.pharmacist_in_charge,
        "pharmacist_license": pharmacy.pharmacist_license,
        "config": pharmacy.config,
        "created_at": pharmacy.created_at,
        "updated_at": pharmacy.updated_at
    }
    
    return pharmacy_dict


@router.put("/{pharmacy_id}", response_model=PharmacyResponse)
def update_pharmacy(
    pharmacy_id: str,
    pharmacy_in: PharmacyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour une pharmacie"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Si on tente de réactiver une pharmacie désactivée, vérifier les limites
    if pharmacy_in.is_active is True and pharmacy.is_active is False:
        limits_check = PharmacyLimits.can_create_pharmacy(db, current_tenant)
        if not limits_check["can_create"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Impossible de réactiver: {limits_check['reason']}"
            )
    
    update_data = pharmacy_in.dict(exclude_unset=True)
    
    for field, value in update_data.items():
        if field == 'name' or field == 'nom':
            setattr(pharmacy, 'name', value)
        else:
            setattr(pharmacy, field, value)
    
    pharmacy.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(pharmacy)
    
    pharmacy_dict = {
        "id": str(pharmacy.id),
        "tenant_id": str(pharmacy.tenant_id),
        "nom": pharmacy.name,
        "name": pharmacy.name,
        "license_number": pharmacy.license_number,
        "address": pharmacy.address,
        "city": pharmacy.city,
        "country": pharmacy.country,
        "phone": pharmacy.phone,
        "email": pharmacy.email,
        "is_active": pharmacy.is_active,
        "opening_hours": pharmacy.opening_hours,
        "pharmacist_in_charge": pharmacy.pharmacist_in_charge,
        "pharmacist_license": pharmacy.pharmacist_license,
        "config": pharmacy.config,
        "created_at": pharmacy.created_at,
        "updated_at": pharmacy.updated_at
    }
    
    return pharmacy_dict


@router.delete("/{pharmacy_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pharmacy(
    pharmacy_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Désactive une pharmacie (soft delete)"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    pharmacy.is_active = False
    pharmacy.updated_at = datetime.utcnow()
    
    # Si c'était la pharmacie active de l'utilisateur, la désactiver
    if current_user.active_pharmacy_id == pharmacy.id:
        current_user.active_pharmacy_id = None
    
    db.commit()


@router.post("/{pharmacy_id}/reactivate", response_model=PharmacyResponse)
def reactivate_pharmacy(
    pharmacy_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Réactive une pharmacie désactivée"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    limits_check = PharmacyLimits.can_create_pharmacy(db, current_tenant)
    if not limits_check["can_create"] and not pharmacy.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Impossible de réactiver: {limits_check['reason']}"
        )
    
    pharmacy.is_active = True
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(pharmacy)
    
    pharmacy_dict = {
        "id": str(pharmacy.id),
        "tenant_id": str(pharmacy.tenant_id),
        "nom": pharmacy.name,
        "name": pharmacy.name,
        "license_number": pharmacy.license_number,
        "address": pharmacy.address,
        "city": pharmacy.city,
        "country": pharmacy.country,
        "phone": pharmacy.phone,
        "email": pharmacy.email,
        "is_active": pharmacy.is_active,
        "opening_hours": pharmacy.opening_hours,
        "pharmacist_in_charge": pharmacy.pharmacist_in_charge,
        "pharmacist_license": pharmacy.pharmacist_license,
        "config": pharmacy.config,
        "created_at": pharmacy.created_at,
        "updated_at": pharmacy.updated_at
    }
    
    return pharmacy_dict

# ==================== PHARMACY CONFIGURATION ====================
@router.get("/{pharmacy_id}/service-status")
def check_pharmacy_service_status(
    pharmacy_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant)
):
    """
    Vérifie si la pharmacie est en service selon les heures configurées.
    """
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    # Vérifier l'accès à la pharmacie
    query = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id)
    
    if current_user.role not in ["super_admin", "superadmin"]:
        if not current_tenant:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé"
            )
        query = query.filter(Pharmacy.tenant_id == current_tenant.id)
    
    pharmacy = query.first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Récupérer la configuration
    config = pharmacy.config or {}
    working_hours = config.get("workingHours", {})
    
    if not working_hours.get("enabled", True):
        return {
            "pharmacy_id": str(pharmacy.id),
            "pharmacy_name": pharmacy.name,
            "in_service": True,
            "restrictions_enabled": False,
            "message": "Service toujours disponible (pas de restriction horaire)",
            "current_time_utc": datetime.now(pytz.UTC).isoformat(),
        }
    
    timezone_str = working_hours.get("timezone", "Africa/Kinshasa")
    
    try:
        tz = pytz.timezone(timezone_str)
        now_local = datetime.now(tz)
        now_utc = datetime.now(pytz.UTC)
    except Exception:
        tz = pytz.UTC
        now_local = datetime.now(pytz.UTC)
        now_utc = now_local
        timezone_str = "UTC"
    
    current_minutes = now_local.hour * 60 + now_local.minute
    current_day = now_local.strftime("%A").lower()
    
    days_off = working_hours.get("daysOff", {})
    
    if not days_off:
        days_off = {
            "monday": True,
            "tuesday": True,
            "wednesday": True,
            "thursday": True,
            "friday": True,
            "saturday": True,
            "sunday": False
        }
    
    is_working_day = days_off.get(current_day, False)
    
    start_time_str = working_hours.get("startTime", "08:00")
    end_time_str = working_hours.get("endTime", "20:00")
    
    try:
        start_parts = start_time_str.split(":")
        end_parts = end_time_str.split(":")
        start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
        end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])
    except (ValueError, IndexError):
        start_minutes = 8 * 60
        end_minutes = 20 * 60
        start_time_str = "08:00"
        end_time_str = "20:00"
    
    if end_minutes < start_minutes:
        is_within_hours = current_minutes >= start_minutes or current_minutes <= end_minutes
    else:
        is_within_hours = start_minutes <= current_minutes <= end_minutes
    
    in_service = is_working_day and is_within_hours
    
    # Calculer le prochain service
    next_service_info = calculate_next_service_time(
        current_day=current_day,
        current_minutes=current_minutes,
        working_hours=working_hours,
        is_working_day=is_working_day,
        start_minutes=start_minutes,
        end_minutes=end_minutes,
        days_off=days_off
    )
    
    return {
        "pharmacy_id": str(pharmacy.id),
        "pharmacy_name": pharmacy.name,
        "in_service": in_service,
        "restrictions_enabled": True,
        "current_time_utc": now_utc.isoformat(),
        "current_time_local": now_local.isoformat(),
        "timezone": timezone_str,
        "current_day": current_day,
        "is_working_day": is_working_day,
        "is_within_hours": is_within_hours,
        "working_hours": {
            "start": start_time_str,
            "end": end_time_str,
            "overtime": working_hours.get("overtimeEndTime")
        },
        "message": "Service disponible" if in_service else "Service indisponible - hors horaires",
        "next_service_time": next_service_info
    }


def calculate_next_service_time(
    current_day: str,
    current_minutes: int,
    working_hours: dict,
    is_working_day: bool,
    start_minutes: int,
    end_minutes: int,
    days_off: dict
) -> str | None:
    """Calcule le prochain moment où la pharmacie sera en service."""
    start_time_str = working_hours.get("startTime", "08:00")
    
    if is_working_day and current_minutes < start_minutes:
        return f"{start_time_str} (aujourd'hui)"
    
    if is_working_day and current_minutes > end_minutes:
        return find_next_open_day(current_day, start_time_str, days_off)
    
    if not is_working_day:
        return find_next_open_day(current_day, start_time_str, days_off)
    
    return None


def find_next_open_day(current_day: str, start_time: str, days_off: dict) -> str:
    """Trouve le prochain jour OUVERT dans la semaine."""
    days_order = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    
    day_names_fr = {
        "monday": "lundi", 
        "tuesday": "mardi", 
        "wednesday": "mercredi",
        "thursday": "jeudi", 
        "friday": "vendredi", 
        "saturday": "samedi",
        "sunday": "dimanche"
    }
    
    try:
        current_index = days_order.index(current_day)
    except ValueError:
        current_index = 0
    
    for i in range(1, 8):
        next_index = (current_index + i) % 7
        next_day = days_order[next_index]
        
        is_open = days_off.get(next_day, False)
        
        if is_open:
            day_name_fr = day_names_fr.get(next_day, next_day)
            if i == 1:
                return f"{start_time} demain"
            else:
                return f"{start_time} {day_name_fr}"
    
    return "aucun jour d'ouverture configuré"

@router.get("/{pharmacy_id}/branches/{branch_id}/service-status")
def check_branch_service_status(
    pharmacy_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant)
):
    """Version simplifiée pour les branches"""
    try:
        UUID(pharmacy_id)
        UUID(branch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID invalide"
        )
    
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.parent_pharmacy_id == pharmacy_id
    ).first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée"
        )
    
    return {
        "branch_id": str(branch.id),
        "branch_name": branch.name,
        "in_service": True,
        "restrictions_enabled": False,
        "message": "Service disponible",
        "current_time_utc": datetime.utcnow().isoformat()
    }

@router.get("/{pharmacy_id}/config", response_model=PharmacyConfigResponse)
def get_pharmacy_config(
    pharmacy_id: str = Path(..., description="ID de la pharmacie (UUID)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Récupère la configuration complète d'une pharmacie"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    return {
        "pharmacy_id": pharmacy_id,
        "config": pharmacy.config or {},
        "updated_at": pharmacy.updated_at
    }


@router.patch("/{pharmacy_id}/config", response_model=PharmacyConfigResponse)
def update_pharmacy_config(
    pharmacy_id: str,
    config_in: PharmacyConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour la configuration d'une pharmacie"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Récupérer la config actuelle
    current_config = pharmacy.config or {}
    
    # Fonction de conversion des datetime
    def convert_datetime_to_iso(obj):
        if isinstance(obj, dict):
            return {k: convert_datetime_to_iso(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_datetime_to_iso(item) for item in obj]
        elif isinstance(obj, datetime):
            return obj.isoformat()
        else:
            return obj
    
    # Convertir les datetime dans update_data
    update_data = config_in.dict(exclude_unset=True, exclude_none=True)
    update_data = convert_datetime_to_iso(update_data)
    
    # ✅ Gestion spéciale pour salesType (peut être un objet ou une chaîne)
    if "salesType" in update_data:
        sales_type_value = update_data["salesType"]
        if isinstance(sales_type_value, dict) and "type" in sales_type_value:
            # Si c'est un objet avec 'type', extraire la valeur
            update_data["salesType"] = sales_type_value["type"]
        elif isinstance(sales_type_value, str):
            # Déjà une chaîne, garder tel quel
            pass
    
    # Fonction de merge récursif
    def deep_merge(original, updates):
        for key, value in updates.items():
            if isinstance(value, dict) and key in original and isinstance(original[key], dict):
                deep_merge(original[key], value)
            else:
                original[key] = value
        return original
    
    updated_config = deep_merge(current_config.copy(), update_data)
    
    # Mettre à jour la date
    updated_config["updatedAt"] = datetime.utcnow().isoformat()
    
    # Nettoyer la config
    updated_config = convert_datetime_to_iso(updated_config)
    
    pharmacy.config = updated_config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(pharmacy)
    
    return {
        "pharmacy_id": pharmacy_id,
        "config": pharmacy.config,
        "updated_at": pharmacy.updated_at
    }

@router.patch("/{pharmacy_id}/config/sales", response_model=dict)
def update_sales_config(
    pharmacy_id: str,
    sales_config: SalesConfig,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour la configuration de vente (salesType, calcul_auto_prix, etc.)"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    config = pharmacy.config or {}
    
    # Mettre à jour les champs de configuration de vente
    config["salesType"] = sales_config.salesType
    config["calcul_auto_prix"] = sales_config.calcul_auto_prix
    config["marge_par_defaut"] = sales_config.marge_par_defaut
    config["taux_tva"] = sales_config.taux_tva
    config["lock_stock_modification"] = sales_config.lock_stock_modification
    config["updatedAt"] = datetime.utcnow().isoformat()
    
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {
        "message": "Configuration de vente mise à jour",
        "sales_config": {
            "salesType": config["salesType"],
            "calcul_auto_prix": config["calcul_auto_prix"],
            "marge_par_defaut": config["marge_par_defaut"],
            "taux_tva": config["taux_tva"],
            "lock_stock_modification": config["lock_stock_modification"]
        }
    }


@router.patch("/{pharmacy_id}/config/currencies")
def update_currencies_config(
    pharmacy_id: str,
    currencies: List[CurrencyConfig],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour la configuration des devises"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    for currency in currencies:
        if currency.exchangeRate <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Le taux de change pour {currency.code} doit être supérieur à 0"
            )
        
        if currency.code == "USD" and currency.exchangeRate != 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Le taux de change pour USD doit être 1 (devise de référence)"
            )
    
    if not any(c.isActive for c in currencies):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Au moins une devise doit être active"
        )
    
    config = pharmacy.config or {}
    config["currencies"] = [c.dict() for c in currencies]
    config["updatedAt"] = datetime.utcnow().isoformat()
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {"message": "Configuration des devises mise à jour", "currencies": currencies}


@router.patch("/{pharmacy_id}/config/working-hours")
def update_working_hours(
    pharmacy_id: str,
    working_hours: WorkingHoursConfig,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour les heures de service"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    config = pharmacy.config or {}
    config["workingHours"] = working_hours.dict()
    config["updatedAt"] = datetime.utcnow().isoformat()
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {"message": "Heures de service mises à jour", "working_hours": working_hours}


@router.patch("/{pharmacy_id}/config/pricing")
def update_pricing_config(
    pharmacy_id: str,
    pricing: AutomaticPricingConfig,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour la configuration des prix"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    config = pharmacy.config or {}
    config["automaticPricing"] = pricing.dict()
    config["updatedAt"] = datetime.utcnow().isoformat()
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {"message": "Configuration des prix mise à jour", "pricing": pricing}


@router.patch("/{pharmacy_id}/config/theme")
def update_theme(
    pharmacy_id: str,
    theme: ThemeConfig,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour le thème de l'application"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    config = pharmacy.config or {}
    config["theme"] = theme.theme
    config["updatedAt"] = datetime.utcnow().isoformat()
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {"message": "Thème mis à jour", "theme": theme.theme}


# ==================== BRANCHES MANAGEMENT ====================

@router.get("/{pharmacy_id}/branches", response_model=List[BranchResponse])
def get_branches(
    pharmacy_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
    active_only: bool = True
):
    """Récupère toutes les branches d'une pharmacie"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    query = db.query(Branch).filter(Branch.parent_pharmacy_id == pharmacy.id)
    
    if active_only:
        query = query.filter(Branch.is_active == True)
    
    branches = query.order_by(Branch.is_main_branch.desc(), Branch.created_at.asc()).all()
    
    return branches


@router.get("/{pharmacy_id}/branches/{branch_id}", response_model=BranchResponse)
def get_branch(
    pharmacy_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Récupère une branche spécifique"""
    try:
        UUID(pharmacy_id)
        UUID(branch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID invalide"
        )
    
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.parent_pharmacy_id == pharmacy_id,
        Branch.tenant_id == current_tenant.id
    ).first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée"
        )
    
    return branch


@router.post("/{pharmacy_id}/branches", response_model=BranchResponse, status_code=status.HTTP_201_CREATED)
def create_branch(
    pharmacy_id: str,
    branch_in: BranchCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Crée une nouvelle succursale pour une pharmacie"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Vérifier les limites
    limits_check = PharmacyLimits.can_create_branch(pharmacy, current_tenant, db)
    
    if not limits_check["can_create"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=limits_check["reason"]
        )
    
    # Vérifier si c'est la première branche (devient branche principale)
    existing_branches_count = db.query(Branch).filter(
        Branch.parent_pharmacy_id == pharmacy.id,
        Branch.is_active == True
    ).count()
    
    is_main_branch = existing_branches_count == 0
    
    # Générer un code unique
    code = branch_in.code or f"{pharmacy.name[:3].upper()}{existing_branches_count + 1:03d}"
    
    # Créer la branche
    branch = Branch(
        tenant_id=current_tenant.id,
        parent_pharmacy_id=pharmacy.id,
        name=branch_in.name,
        code=code,
        address=branch_in.address,
        city=branch_in.city,
        country=branch_in.country or pharmacy.country,
        phone=branch_in.phone or pharmacy.phone,
        email=branch_in.email or pharmacy.email,
        latitude=branch_in.latitude,
        longitude=branch_in.longitude,
        manager_id=branch_in.manager_id,
        manager_name=branch_in.manager_name,
        opening_hours=branch_in.opening_hours,
        config=branch_in.config or {},
        is_active=True,
        is_main_branch=is_main_branch,
        created_by=current_user.id
    )
    
    db.add(branch)
    
    # Mettre à jour la config de la pharmacie
    config = pharmacy.config or {}
    if "branchConfig" not in config:
        config["branchConfig"] = {"maxBranches": limits_check["max_branches_allowed"], "currentBranches": 0, "branches": []}
    
    config["branchConfig"]["currentBranches"] = existing_branches_count + 1
    config["branchConfig"]["maxBranches"] = limits_check["max_branches_allowed"]
    
    # Ajouter la branche dans la liste pour compatibilité
    if "branches" not in config["branchConfig"]:
        config["branchConfig"]["branches"] = []
    
    config["branchConfig"]["branches"].append({
        "id": str(branch.id),
        "name": branch.name,
        "code": branch.code,
        "address": branch.address,
        "city": branch.city,
        "phone": branch.phone,
        "email": branch.email,
        "is_main_branch": is_main_branch
    })
    
    config["updatedAt"] = datetime.utcnow().isoformat()
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(branch)
    
    return branch


@router.put("/{pharmacy_id}/branches/{branch_id}", response_model=BranchResponse)
def update_branch(
    pharmacy_id: str,
    branch_id: str,
    branch_in: BranchUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour une succursale"""
    try:
        UUID(pharmacy_id)
        UUID(branch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID invalide"
        )
    
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.parent_pharmacy_id == pharmacy_id,
        Branch.tenant_id == current_tenant.id
    ).first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée"
        )
    
    update_data = branch_in.dict(exclude_unset=True, exclude_none=True)
    
    for field, value in update_data.items():
        setattr(branch, field, value)
    
    branch.updated_at = datetime.utcnow()
    
    # Mettre à jour la config de la pharmacie
    pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
    if pharmacy and pharmacy.config:
        config = pharmacy.config
        if "branchConfig" in config and "branches" in config["branchConfig"]:
            for b in config["branchConfig"]["branches"]:
                if b.get("id") == branch_id:
                    b.update({
                        "name": branch.name,
                        "code": branch.code,
                        "address": branch.address,
                        "city": branch.city,
                        "phone": branch.phone,
                        "email": branch.email
                    })
                    break
            config["updatedAt"] = datetime.utcnow().isoformat()
            pharmacy.config = config
            pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(branch)
    
    return branch


@router.delete("/{pharmacy_id}/branches/{branch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_branch(
    pharmacy_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Désactive une succursale (soft delete)"""
    try:
        UUID(pharmacy_id)
        UUID(branch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID invalide"
        )
    
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.parent_pharmacy_id == pharmacy_id,
        Branch.tenant_id == current_tenant.id
    ).first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée"
        )
    
    # Ne pas supprimer la branche principale si c'est la seule
    if branch.is_main_branch:
        other_branches = db.query(Branch).filter(
            Branch.parent_pharmacy_id == pharmacy_id,
            Branch.id != branch_id,
            Branch.is_active == True
        ).count()
        
        if other_branches == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de supprimer la branche principale. Transférez d'abord les données vers une autre branche."
            )
    
    branch.is_active = False
    branch.updated_at = datetime.utcnow()
    
    # Mettre à jour la config de la pharmacie
    pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
    if pharmacy and pharmacy.config:
        config = pharmacy.config
        if "branchConfig" in config:
            if "currentBranches" in config["branchConfig"]:
                config["branchConfig"]["currentBranches"] = max(0, config["branchConfig"]["currentBranches"] - 1)
            if "branches" in config["branchConfig"]:
                config["branchConfig"]["branches"] = [b for b in config["branchConfig"]["branches"] if b.get("id") != branch_id]
            config["updatedAt"] = datetime.utcnow().isoformat()
            pharmacy.config = config
            pharmacy.updated_at = datetime.utcnow()
    
    db.commit()


# ==================== SERVICE STATUS ====================

def find_next_open_day(current_day: str, start_time: str, days_off: dict) -> str:
    """Trouve le prochain jour OUVERT dans la semaine."""
    days_order = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    
    day_names_fr = {
        "monday": "lundi", 
        "tuesday": "mardi", 
        "wednesday": "mercredi",
        "thursday": "jeudi", 
        "friday": "vendredi", 
        "saturday": "samedi",
        "sunday": "dimanche"
    }
    
    try:
        current_index = days_order.index(current_day)
    except ValueError:
        current_index = 0
    
    for i in range(1, 8):
        next_index = (current_index + i) % 7
        next_day = days_order[next_index]
        
        is_open = days_off.get(next_day, False)
        
        if is_open:
            day_name_fr = day_names_fr.get(next_day, next_day)
            
            if i == 1:
                return f"{start_time} demain"
            else:
                return f"{start_time} {day_name_fr}"
    
    return "aucun jour d'ouverture configuré"


# ==================== ONLINE USERS ====================

@router.get("/{pharmacy_id}/online-users")
def get_online_users(
    pharmacy_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Récupère la liste des utilisateurs en ligne pour une pharmacie"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_user.tenant_id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    threshold = datetime.utcnow() - timedelta(minutes=15)
    
    online_users = db.query(User).filter(
        User.tenant_id == current_user.tenant_id,
        User.pharmacy_id == pharmacy_id,
        User.actif == True,
        User.last_login >= threshold
    ).all()
    
    result = []
    for user in online_users:
        if user.last_login:
            login_duration = datetime.utcnow() - user.last_login
            duration_minutes = int(login_duration.total_seconds() / 60)
            duration_text = f"{duration_minutes} min"
        else:
            duration_text = "N/A"
        
        result.append({
            "id": str(user.id),
            "nom_complet": user.nom_complet,
            "email": user.email,
            "role": user.role,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "login_duration": duration_text,
            "status": "online"
        })
    
    return {
        "pharmacy_id": pharmacy_id,
        "pharmacy_name": pharmacy.name,
        "online_count": len(online_users),
        "users": result,
        "timestamp": datetime.utcnow().isoformat()
    }


# ==================== LOGO UPLOAD ====================

@router.post("/{pharmacy_id}/logo")
async def upload_logo(
    pharmacy_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Télécharge le logo de la pharmacie"""
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID invalide")
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(status_code=404, detail="Pharmacie non trouvée")
    
    upload_dir = f"uploads/pharmacies/{pharmacy_id}"
    os.makedirs(upload_dir, exist_ok=True)
    
    file_extension = file.filename.split(".")[-1]
    filename = f"logo.{file_extension}"
    file_path = f"{upload_dir}/{filename}"
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    config = pharmacy.config or {}
    config["pharmacyInfo"] = config.get("pharmacyInfo", {})
    config["pharmacyInfo"]["logoUrl"] = f"/uploads/pharmacies/{pharmacy_id}/{filename}"
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {"logo_url": config["pharmacyInfo"]["logoUrl"]}

# ==================== ADDITIONAL BRANCH ENDPOINTS ====================
# À ajouter après les endpoints CRUD existants des branches

@router.get("/{pharmacy_id}/branches/{branch_id}/statistics", response_model=BranchStatistics)
def get_branch_statistics(
    pharmacy_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
    period: str = Query("month", description="Période: day, week, month, year")
):
    """
    Récupère les statistiques d'une succursale
    """
    try:
        UUID(pharmacy_id)
        UUID(branch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID invalide"
        )
    
    # Vérifier que la pharmacie appartient au tenant
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.parent_pharmacy_id == pharmacy_id,
        Branch.tenant_id == current_tenant.id
    ).first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée"
        )
    
    # Calculer les dates en fonction de la période
    now = datetime.utcnow()
    if period == "day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_date = now - timedelta(days=now.weekday())
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:  # year
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Compter les produits
    from app.models.product import Product
    products_query = db.query(Product).filter(Product.branch_id == branch.id)
    
    products_total = products_query.count()
    products_low_stock = products_query.filter(Product.quantity <= Product.low_stock_threshold).count()
    products_out_of_stock = products_query.filter(Product.quantity == 0).count()
    
    # Produits expirant bientôt (dans 90 jours)
    expiry_date = now + timedelta(days=90)
    products_expiring_soon = products_query.filter(
        Product.expiry_date <= expiry_date,
        Product.expiry_date >= now
    ).count()
    
    # Compter les ventes
    from app.models.sale import Sale
    sales_today = db.query(Sale).filter(
        Sale.branch_id == branch.id,
        Sale.created_at >= start_date
    ).count()
    
    sales_today_amount = db.query(func.sum(Sale.total_amount)).filter(
        Sale.branch_id == branch.id,
        Sale.created_at >= start_date
    ).scalar() or 0.0
    
    # Ventes de la semaine
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    sales_this_week = db.query(Sale).filter(
        Sale.branch_id == branch.id,
        Sale.created_at >= week_start
    ).count()
    
    sales_this_week_amount = db.query(func.sum(Sale.total_amount)).filter(
        Sale.branch_id == branch.id,
        Sale.created_at >= week_start
    ).scalar() or 0.0
    
    # Ventes du mois
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    sales_this_month = db.query(Sale).filter(
        Sale.branch_id == branch.id,
        Sale.created_at >= month_start
    ).count()
    
    sales_this_month_amount = db.query(func.sum(Sale.total_amount)).filter(
        Sale.branch_id == branch.id,
        Sale.created_at >= month_start
    ).scalar() or 0.0
    
    # Compter les clients
    from app.models.customer import Customer
    customers_total = db.query(Customer).filter(
        Customer.branch_id == branch.id
    ).count()
    
    customers_active = db.query(Customer).filter(
        Customer.branch_id == branch.id,
        Customer.is_active == True
    ).count()
    
    # Compter les employés
    employees_count = db.query(User).filter(
        User.branch_id == branch.id,
        User.actif == True
    ).count()
    
    # Dernière vente
    last_sale = db.query(Sale).filter(
        Sale.branch_id == branch.id
    ).order_by(Sale.created_at.desc()).first()
    
    return BranchStatistics(
        branch_id=branch.id,
        branch_name=branch.name,
        products_total=products_total,
        products_low_stock=products_low_stock,
        products_expiring_soon=products_expiring_soon,
        products_out_of_stock=products_out_of_stock,
        sales_today=sales_today,
        sales_today_amount=float(sales_today_amount),
        sales_this_week=sales_this_week,
        sales_this_week_amount=float(sales_this_week_amount),
        sales_this_month=sales_this_month,
        sales_this_month_amount=float(sales_this_month_amount),
        customers_total=customers_total,
        customers_active=customers_active,
        employees_count=employees_count,
        last_sale_at=last_sale.created_at if last_sale else None
    )


@router.get("/{pharmacy_id}/branches/{branch_id}/service-status", response_model=BranchServiceStatus)
def check_branch_service_status(
    pharmacy_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant)
):
    """
    Vérifie si la branche est en service selon ses heures configurées.
    """
    try:
        UUID(pharmacy_id)
        UUID(branch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID invalide"
        )
    
    # Vérifier la pharmacie
    pharmacy_query = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id)
    if current_user.role not in ["super_admin", "superadmin"]:
        if not current_tenant:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé"
            )
        pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == current_tenant.id)
    
    pharmacy = pharmacy_query.first()
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Récupérer la branche
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.parent_pharmacy_id == pharmacy_id,
        Branch.is_active == True
    ).first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée"
        )
    
    # Récupérer la configuration de la branche
    config = branch.config or {}
    working_hours = config.get("workingHours", {})
    
    # Si pas de config spécifique, utiliser celle de la pharmacie
    if not working_hours:
        pharmacy_config = pharmacy.config or {}
        working_hours = pharmacy_config.get("workingHours", {})
    
    if not working_hours.get("enabled", True):
        return BranchServiceStatus(
            branch_id=branch.id,
            branch_name=branch.name,
            in_service=True,
            restrictions_enabled=False,
            current_time_local=datetime.now(pytz.UTC).isoformat(),
            timezone="UTC",
            current_day=datetime.now(pytz.UTC).strftime("%A").lower(),
            is_working_day=True,
            is_within_hours=True,
            working_hours={"start": "00:00", "end": "23:59"},
            message="Service toujours disponible (pas de restriction horaire)"
        )
    
    timezone_str = working_hours.get("timezone", "Africa/Kinshasa")
    
    try:
        tz = pytz.timezone(timezone_str)
        now_local = datetime.now(tz)
    except Exception:
        tz = pytz.UTC
        now_local = datetime.now(pytz.UTC)
        timezone_str = "UTC"
    
    current_minutes = now_local.hour * 60 + now_local.minute
    current_day = now_local.strftime("%A").lower()
    
    days_off = working_hours.get("daysOff", {})
    if not days_off:
        days_off = {
            "monday": True,
            "tuesday": True,
            "wednesday": True,
            "thursday": True,
            "friday": True,
            "saturday": True,
            "sunday": False
        }
    
    is_working_day = days_off.get(current_day, False)
    
    start_time_str = working_hours.get("startTime", "08:00")
    end_time_str = working_hours.get("endTime", "20:00")
    
    try:
        start_parts = start_time_str.split(":")
        end_parts = end_time_str.split(":")
        start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
        end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])
    except (ValueError, IndexError):
        start_minutes = 8 * 60
        end_minutes = 20 * 60
        start_time_str = "08:00"
        end_time_str = "20:00"
    
    if end_minutes < start_minutes:
        is_within_hours = current_minutes >= start_minutes or current_minutes <= end_minutes
    else:
        is_within_hours = start_minutes <= current_minutes <= end_minutes
    
    in_service = is_working_day and is_within_hours
    
    # Calculer le prochain service
    next_service_info = calculate_branch_next_service(
        current_day=current_day,
        current_minutes=current_minutes,
        working_hours=working_hours,
        is_working_day=is_working_day,
        start_minutes=start_minutes,
        end_minutes=end_minutes,
        days_off=days_off
    )
    
    return BranchServiceStatus(
        branch_id=branch.id,
        branch_name=branch.name,
        in_service=in_service,
        restrictions_enabled=True,
        current_time_local=now_local.isoformat(),
        timezone=timezone_str,
        current_day=current_day,
        is_working_day=is_working_day,
        is_within_hours=is_within_hours,
        working_hours={
            "start": start_time_str,
            "end": end_time_str,
            "overtime": working_hours.get("overtimeEndTime")
        },
        message="✅ En service" if in_service else "❌ Hors service",
        next_service_time=next_service_info
    )


def calculate_branch_next_service(
    current_day: str,
    current_minutes: int,
    working_hours: dict,
    is_working_day: bool,
    start_minutes: int,
    end_minutes: int,
    days_off: dict
) -> Optional[str]:
    """Calcule le prochain moment où la branche sera en service."""
    start_time_str = working_hours.get("startTime", "08:00")
    
    if is_working_day and current_minutes < start_minutes:
        return f"{start_time_str} (aujourd'hui)"
    
    if is_working_day and current_minutes > end_minutes:
        return find_next_open_branch_day(current_day, start_time_str, days_off)
    
    if not is_working_day:
        return find_next_open_branch_day(current_day, start_time_str, days_off)
    
    return None


def find_next_open_branch_day(current_day: str, start_time: str, days_off: dict) -> str:
    """Trouve le prochain jour OUVERT dans la semaine pour une branche."""
    days_order = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    
    day_names_fr = {
        "monday": "lundi", 
        "tuesday": "mardi", 
        "wednesday": "mercredi",
        "thursday": "jeudi", 
        "friday": "vendredi", 
        "saturday": "samedi",
        "sunday": "dimanche"
    }
    
    try:
        current_index = days_order.index(current_day)
    except ValueError:
        current_index = 0
    
    for i in range(1, 8):
        next_index = (current_index + i) % 7
        next_day = days_order[next_index]
        
        is_open = days_off.get(next_day, False)
        
        if is_open:
            day_name_fr = day_names_fr.get(next_day, next_day)
            if i == 1:
                return f"{start_time} demain"
            else:
                return f"{start_time} {day_name_fr}"
    
    return "aucun jour d'ouverture configuré"


@router.patch("/{pharmacy_id}/branches/{branch_id}/config", response_model=dict)
def update_branch_config(
    pharmacy_id: str,
    branch_id: str,
    config_update: BranchConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Met à jour la configuration spécifique d'une branche
    """
    try:
        UUID(pharmacy_id)
        UUID(branch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID invalide"
        )
    
    # Vérifier la pharmacie
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Vérifier la branche
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.parent_pharmacy_id == pharmacy_id,
        Branch.tenant_id == current_tenant.id
    ).first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée"
        )
    
    # Récupérer la config actuelle
    current_config = branch.config or {}
    
    # Mettre à jour les champs
    update_data = config_update.dict(exclude_unset=True, exclude_none=True)
    
    for key, value in update_data.items():
        if key == "workingHours" and value:
            current_config["workingHours"] = {**current_config.get("workingHours", {}), **value.dict()}
        else:
            current_config[key] = value
    
    current_config["updatedAt"] = datetime.utcnow().isoformat()
    
    # Sauvegarder
    branch.config = current_config
    branch.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {
        "message": "Configuration de la branche mise à jour",
        "branch_id": str(branch.id),
        "config": branch.config
    }


@router.get("/{pharmacy_id}/branches/{branch_id}/config", response_model=dict)
def get_branch_config(
    pharmacy_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Récupère la configuration spécifique d'une branche
    """
    try:
        UUID(pharmacy_id)
        UUID(branch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID invalide"
        )
    
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.parent_pharmacy_id == pharmacy_id,
        Branch.tenant_id == current_tenant.id
    ).first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée"
        )
    
    return {
        "branch_id": str(branch.id),
        "branch_name": branch.name,
        "config": branch.config or {},
        "updated_at": branch.updated_at
    }


@router.post("/{pharmacy_id}/branches/{branch_id}/set-main", response_model=BranchResponse)
def set_main_branch(
    pharmacy_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Définit une branche comme branche principale
    """
    try:
        UUID(pharmacy_id)
        UUID(branch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID invalide"
        )
    
    # Vérifier la pharmacie
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Vérifier la branche
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.parent_pharmacy_id == pharmacy_id,
        Branch.tenant_id == current_tenant.id
    ).first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée"
        )
    
    if not branch.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Impossible de définir une branche inactive comme principale"
        )
    
    # Retirer le statut de principale des autres branches
    db.query(Branch).filter(
        Branch.parent_pharmacy_id == pharmacy_id,
        Branch.tenant_id == current_tenant.id,
        Branch.is_main_branch == True
    ).update({"is_main_branch": False})
    
    # Définir cette branche comme principale
    branch.is_main_branch = True
    branch.updated_at = datetime.utcnow()
    
    # Mettre à jour la config de la pharmacie
    config = pharmacy.config or {}
    if "branchConfig" not in config:
        config["branchConfig"] = {}
    
    config["branchConfig"]["main_branch_id"] = str(branch.id)
    config["branchConfig"]["main_branch_name"] = branch.name
    config["updatedAt"] = datetime.utcnow().isoformat()
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(branch)
    
    return branch


@router.get("/{pharmacy_id}/branches/filter", response_model=BranchListResponse)
def filter_branches(
    pharmacy_id: str,
    search: Optional[str] = Query(None, description="Recherche par nom, code, ville"),
    city: Optional[str] = Query(None, description="Filtrer par ville"),
    country: Optional[str] = Query(None, description="Filtrer par pays"),
    is_active: Optional[bool] = Query(None, description="Filtrer par statut"),
    is_main_branch: Optional[bool] = Query(None, description="Filtrer par branche principale"),
    has_manager: Optional[bool] = Query(None, description="Avec responsable assigné"),
    page: int = Query(1, ge=1, description="Page"),
    size: int = Query(20, ge=1, le=100, description="Taille de page"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Filtre les branches avec pagination
    """
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    # Vérifier la pharmacie
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Construire la requête
    query = db.query(Branch).filter(
        Branch.parent_pharmacy_id == pharmacy.id,
        Branch.tenant_id == current_tenant.id
    )
    
    # Appliquer les filtres
    if search:
        query = query.filter(
            Branch.name.ilike(f"%{search}%") |
            Branch.code.ilike(f"%{search}%") |
            Branch.city.ilike(f"%{search}%") |
            Branch.address.ilike(f"%{search}%")
        )
    
    if city:
        query = query.filter(Branch.city == city)
    
    if country:
        query = query.filter(Branch.country == country)
    
    if is_active is not None:
        query = query.filter(Branch.is_active == is_active)
    
    if is_main_branch is not None:
        query = query.filter(Branch.is_main_branch == is_main_branch)
    
    if has_manager is not None:
        if has_manager:
            query = query.filter(Branch.manager_id.isnot(None))
        else:
            query = query.filter(Branch.manager_id.is_(None))
    
    # Compter le total
    total = query.count()
    
    # Pagination
    offset = (page - 1) * size
    branches = query.order_by(
        Branch.is_main_branch.desc(),
        Branch.created_at.desc()
    ).offset(offset).limit(size).all()
    
    # Calculer le nombre de pages
    pages = (total + size - 1) // size
    
    return BranchListResponse(
        items=branches,
        total=total,
        page=page,
        size=size,
        pages=pages
    )


@router.get("/{pharmacy_id}/branches/export")
def export_branches(
    pharmacy_id: str,
    format: str = Query("csv", description="Format d'export: csv, json"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Exporte la liste des branches au format CSV ou JSON
    """
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    branches = db.query(Branch).filter(
        Branch.parent_pharmacy_id == pharmacy.id,
        Branch.tenant_id == current_tenant.id
    ).all()
    
    # Convertir en dictionnaires
    data = []
    for branch in branches:
        data.append({
            "id": str(branch.id),
            "name": branch.name,
            "code": branch.code,
            "address": branch.address,
            "city": branch.city,
            "country": branch.country,
            "phone": branch.phone,
            "email": branch.email,
            "manager_name": branch.manager_name,
            "is_active": branch.is_active,
            "is_main_branch": branch.is_main_branch,
            "created_at": branch.created_at.isoformat() if branch.created_at else None
        })
    
    if format == "json":
        return data
    
    # Export CSV
    import csv
    from fastapi.responses import StreamingResponse
    import io
    
    output = io.StringIO()
    if data:
        writer = csv.DictWriter(output, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    else:
        writer = csv.writer(output)
        writer.writerow(["Aucune donnée"])
    
    response = StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv"
    )
    response.headers["Content-Disposition"] = f"attachment; filename=branches_{pharmacy.name}.csv"
    
    return response


@router.post("/{pharmacy_id}/branches/{branch_id}/working-hours/override")
def override_branch_working_hours(
    pharmacy_id: str,
    branch_id: str,
    working_hours: BranchWorkingHoursConfig,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Remplace les horaires d'ouverture de la branche (override ceux de la pharmacie)
    """
    try:
        UUID(pharmacy_id)
        UUID(branch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID invalide"
        )
    
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.parent_pharmacy_id == pharmacy_id,
        Branch.tenant_id == current_tenant.id
    ).first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée"
        )
    
    config = branch.config or {}
    config["workingHours"] = working_hours.dict()
    config["workingHoursOverridden"] = True
    config["updatedAt"] = datetime.utcnow().isoformat()
    
    branch.config = config
    branch.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {
        "message": "Horaires de la branche mis à jour",
        "branch_id": str(branch.id),
        "working_hours": working_hours.dict()
    }


@router.delete("/{pharmacy_id}/branches/{branch_id}/working-hours/override")
def remove_branch_working_hours_override(
    pharmacy_id: str,
    branch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Supprime l'override des horaires pour revenir aux horaires de la pharmacie
    """
    try:
        UUID(pharmacy_id)
        UUID(branch_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID invalide"
        )
    
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.parent_pharmacy_id == pharmacy_id,
        Branch.tenant_id == current_tenant.id
    ).first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée"
        )
    
    config = branch.config or {}
    
    # Supprimer les horaires spécifiques
    if "workingHours" in config:
        del config["workingHours"]
    if "workingHoursOverridden" in config:
        del config["workingHoursOverridden"]
    
    config["updatedAt"] = datetime.utcnow().isoformat()
    
    branch.config = config
    branch.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {
        "message": "Override supprimé, la branche utilise maintenant les horaires de la pharmacie",
        "branch_id": str(branch.id)
    }


@router.get("/{pharmacy_id}/branches/statistics/summary")
def get_branches_summary(
    pharmacy_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Récupère un résumé statistique de toutes les branches
    """
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    branches = db.query(Branch).filter(
        Branch.parent_pharmacy_id == pharmacy.id,
        Branch.tenant_id == current_tenant.id,
        Branch.is_active == True
    ).all()
    
    from app.models.product import Product
    from app.models.sale import Sale
    from app.models.customer import Customer
    from app.models.user import User
    
    total_products = 0
    total_sales_today = 0
    total_sales_today_amount = 0.0
    total_customers = 0
    total_employees = 0
    
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    for branch in branches:
        total_products += db.query(Product).filter(Product.branch_id == branch.id).count()
        total_customers += db.query(Customer).filter(Customer.branch_id == branch.id).count()
        total_employees += db.query(User).filter(User.branch_id == branch.id, User.actif == True).count()
        
        sales_today = db.query(func.sum(Sale.total_amount)).filter(
            Sale.branch_id == branch.id,
            Sale.created_at >= today_start
        ).scalar() or 0.0
        total_sales_today_amount += float(sales_today)
        if sales_today > 0:
            total_sales_today += 1
    
    return {
        "pharmacy_id": str(pharmacy.id),
        "pharmacy_name": pharmacy.name,
        "branches_count": len(branches),
        "total_products": total_products,
        "total_customers": total_customers,
        "total_employees": total_employees,
        "sales_today_count": total_sales_today,
        "sales_today_amount": total_sales_today_amount,
        "branches": [
            {
                "id": str(b.id),
                "name": b.name,
                "city": b.city,
                "is_main_branch": b.is_main_branch
            }
            for b in branches
        ]
    }

@router.get("/me/active-pharmacy")
def get_my_active_pharmacy(
    active_pharmacy: Pharmacy = Depends(get_current_active_pharmacy),
):
    """Récupère la pharmacie active de l'utilisateur"""
    return {"pharmacy_id": active_pharmacy.id, "name": active_pharmacy.name}
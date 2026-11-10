# app/api/v1/superadmin.py
"""
API de gestion pour les super administrateurs de la plateforme SaaS
"""
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from uuid import UUID
import logging
import os
import random
import re
import secrets
import string
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, Body
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, text
from pydantic import BaseModel, EmailStr, Field, validator

from app.db.session import get_db
from app.models.user import User
from app.models.tenant import Tenant
from app.models.pharmacy import Pharmacy
from app.models.audit_log import AuditLog
from app.models.user_pharmacy import UserPharmacy
from app.api.deps import get_current_active_user
from app.core.security import hash_password, create_access_token
from app.services.audit_service import log_action
from app.services.notification_service import send_email, send_sms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/super-admin", tags=["Super Admin"])

# =========================
# DEPENDANCES SUPER ADMIN
# =========================

# app/api/v1/superadmin.py
def verify_super_admin(
    current_user: User = Depends(get_current_active_user)
):
    """Vérifie que l'utilisateur est un super administrateur"""
    
    logger.info(f"🔍 Vérification super admin - Rôle utilisateur: '{current_user.role}'")
    logger.info(f"   User ID: {current_user.id}")
    logger.info(f"   User email: {current_user.email}")
    logger.info(f"   User actif: {current_user.actif}")
    
    # Normaliser le rôle pour la comparaison
    user_role = current_user.role.lower().strip() if current_user.role else ""
    
    # Liste des rôles acceptés pour super admin
    allowed_roles = ["super_admin", "superadmin", "super-admin", "admin_super"]
    
    # Log détaillé pour debug
    logger.info(f"🔍 Rôle normalisé: '{user_role}'")
    logger.info(f"🔍 Rôles acceptés: {allowed_roles}")
    
    if user_role not in allowed_roles:
        logger.error(f"❌ Accès refusé - Rôle '{user_role}' non autorisé")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Accès refusé. Rôle super admin requis. Rôle actuel: {current_user.role}"
        )
    
    logger.info(f"✅ Super admin vérifié: {current_user.email}")
    return current_user
# =========================
# SCHEMAS
# =========================

class TenantCreateSchema(BaseModel):
    """Schéma pour la création manuelle d'un tenant"""
    nom_pharmacie: str = Field(..., min_length=2, max_length=100)
    email_admin: EmailStr
    password_admin: str = Field(..., min_length=8)
    nom_commercial: Optional[str] = None
    ville: str = Field(..., min_length=2, max_length=50)
    pays: str = "RDC"
    telephone: str
    adresse: Optional[str] = None
    type_pharmacie: Optional[str] = None
    plan: str = Field("professional", pattern="^(starter|professional|enterprise)$")
    trial_days: int = Field(14, ge=1, le=365)

class TenantUpdateSchema(BaseModel):
    """Schéma pour la mise à jour d'un tenant"""
    nom_pharmacie: Optional[str] = Field(None, min_length=2, max_length=100)
    email_admin: Optional[EmailStr] = None
    nom_commercial: Optional[str] = None
    ville: Optional[str] = None
    pays: Optional[str] = None
    telephone: Optional[str] = None
    adresse: Optional[str] = None
    type_pharmacie: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(active|trial|suspended|inactive)$")
    current_plan: Optional[str] = Field(None, pattern="^(starter|professional|enterprise)$")
    max_users: Optional[int] = Field(None, ge=0)
    max_products: Optional[int] = Field(None, ge=0)
    max_pharmacies: Optional[int] = Field(None, ge=0)
    trial_end_date: Optional[datetime] = None

class SuperUserCreateSchema(BaseModel):
    """Schéma pour créer un super administrateur"""
    nom_complet: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8)
    telephone: Optional[str] = None

class UserImpersonateSchema(BaseModel):
    """Schéma pour l'impersonation d'un utilisateur"""
    user_id: UUID
    tenant_id: Optional[UUID] = None

class SystemSettingsSchema(BaseModel):
    """Schéma pour les paramètres système"""
    maintenance_mode: Optional[bool] = False
    maintenance_message: Optional[str] = "Maintenance en cours"
    allow_new_registrations: Optional[bool] = True
    max_trial_days: Optional[int] = Field(30, ge=1, le=365)
    sms_enabled: Optional[bool] = True
    email_enabled: Optional[bool] = True
    default_plan: Optional[str] = Field("professional", pattern="^(starter|professional|enterprise)$")

class BulkActionSchema(BaseModel):
    """Schéma pour les actions en masse"""
    tenant_ids: List[UUID]
    action: str = Field(..., pattern="^(activate|suspend|extend_trial|change_plan)$")
    value: Optional[Any] = None

# =========================
# ENDPOINTS DASHBOARD
# =========================

@router.get("/dashboard/overview", status_code=status.HTTP_200_OK)
async def get_dashboard_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Dashboard d'aperçu global de la plateforme"""
    
    # Statistiques de base
    total_tenants = db.query(Tenant).count()
    active_tenants = db.query(Tenant).filter(Tenant.status == "active").count()
    trial_tenants = db.query(Tenant).filter(Tenant.status == "trial").count()
    suspended_tenants = db.query(Tenant).filter(Tenant.status == "suspended").count()
    
    # Statistiques d'utilisateurs
    total_users = db.query(User).count()
    super_admins = db.query(User).filter(User.role == "super_admin").count()
    admin_users = db.query(User).filter(User.role == "admin").count()
    
    # Statistiques temporelles
    today = datetime.utcnow().date()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    
    new_tenants_today = db.query(Tenant).filter(
        func.date(Tenant.created_at) == today
    ).count()
    
    new_tenants_week = db.query(Tenant).filter(
        Tenant.created_at >= week_ago
    ).count()
    
    new_tenants_month = db.query(Tenant).filter(
        Tenant.created_at >= month_ago
    ).count()
    
    # Distribution par plan
    plan_distribution = db.query(
        Tenant.current_plan, 
        func.count(Tenant.id).label("count")
    ).group_by(Tenant.current_plan).all()
    
    # Tenants récents
    recent_tenants = db.query(Tenant).order_by(
        Tenant.created_at.desc()
    ).limit(10).all()
    
    # Activités récentes
    recent_activities = db.query(AuditLog).order_by(
        AuditLog.created_at.desc()
    ).limit(20).all()
    
    return {
        "platform": {
            "total_tenants": total_tenants,
            "active_tenants": active_tenants,
            "trial_tenants": trial_tenants,
            "suspended_tenants": suspended_tenants,
            "total_users": total_users,
            "super_admins": super_admins,
            "admin_users": admin_users
        },
        "growth": {
            "new_today": new_tenants_today,
            "new_week": new_tenants_week,
            "new_month": new_tenants_month,
            "growth_rate": round((new_tenants_week / max(total_tenants, 1)) * 100, 2)
        },
        "distribution": {
            "by_plan": {plan: count for plan, count in plan_distribution},
            "by_status": {
                "active": active_tenants,
                "trial": trial_tenants,
                "suspended": suspended_tenants,
                "inactive": total_tenants - (active_tenants + trial_tenants + suspended_tenants)
            }
        },
        "recent_activity": {
            "tenants": [
                {
                    "id": str(t.id),
                    "nom_pharmacie": t.nom_pharmacie,
                    "email_admin": t.email_admin,
                    "status": t.status,
                    "plan": t.current_plan,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "trial_end_date": t.trial_end_date.isoformat() if t.trial_end_date else None
                } for t in recent_tenants
            ],
            "audit_logs": [
                {
                    "id": str(al.id),
                    "user_id": str(al.user_id) if al.user_id else None,
                    "tenant_id": str(al.tenant_id) if al.tenant_id else None,
                    "action": al.action,
                    "description": al.description,
                    "created_at": al.created_at.isoformat() if al.created_at else None
                } for al in recent_activities
            ]
        },
        "timestamp": datetime.utcnow().isoformat()
    }

@router.get("/dashboard/metrics", status_code=status.HTTP_200_OK)
async def get_platform_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    period: str = Query("month", pattern="^(day|week|month|year)$")
):
    """Métriques temporelles de la plateforme"""
    
    end_date = datetime.utcnow()
    
    if period == "day":
        start_date = end_date - timedelta(days=1)
        interval = "hour"
    elif period == "week":
        start_date = end_date - timedelta(days=7)
        interval = "day"
    elif period == "month":
        start_date = end_date - timedelta(days=30)
        interval = "day"
    else:  # year
        start_date = end_date - timedelta(days=365)
        interval = "month"
    
    # Requête pour les nouveaux tenants par intervalle
    # Note: Cette requête dépend de votre base de données (PostgreSQL/MySQL)
    # Adaptez la fonction d'extraction de date selon votre DB
    
    new_tenants_data = []  # À implémenter selon votre DB
    
    # Tenants expirant bientôt
    expiring_soon = db.query(Tenant).filter(
        Tenant.status == "trial",
        Tenant.trial_end_date.between(
            datetime.utcnow(),
            datetime.utcnow() + timedelta(days=3)
        )
    ).count()
    
    # Tenants avec peu d'utilisateurs
    tenants_with_few_users = db.query(Tenant).filter(
        Tenant.status == "active",
        Tenant.max_users > 0
    ).all()
    
    low_usage_tenants = []
    for tenant in tenants_with_few_users:
        user_count = db.query(User).filter(
            User.tenant_id == tenant.id,
            User.actif == True
        ).count()
        if user_count < 2:  # Moins de 2 utilisateurs actifs
            low_usage_tenants.append({
                "id": str(tenant.id),
                "nom_pharmacie": tenant.nom_pharmacie,
                "user_count": user_count,
                "max_users": tenant.max_users
            })
    
    return {
        "period": period,
        "new_tenants": new_tenants_data,
        "alerts": {
            "expiring_trials_soon": expiring_soon,
            "low_usage_tenants": len(low_usage_tenants),
            "suspended_tenants": db.query(Tenant).filter(
                Tenant.status == "suspended"
            ).count()
        },
        "low_usage_tenants": low_usage_tenants[:10],  # Limiter à 10
        "timestamp": datetime.utcnow().isoformat()
    }

# =========================
# ENDPOINTS GESTION TENANTS
# =========================

@router.get("/tenants", status_code=status.HTTP_200_OK)
async def list_all_tenants(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
    plan_filter: Optional[str] = Query(None),
    sort_by: str = Query("created_at", pattern="^(created_at|nom_pharmacie|status|plan)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$")
):
    """Liste tous les tenants avec filtres et pagination"""
    
    query = db.query(Tenant)
    
    # Appliquer les filtres
    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(
            or_(
                Tenant.nom_pharmacie.ilike(search_term),
                Tenant.email_admin.ilike(search_term),
                Tenant.ville.ilike(search_term),
                Tenant.tenant_code.ilike(search_term)
            )
        )
    
    if status_filter:
        query = query.filter(Tenant.status == status_filter)
    
    if plan_filter:
        query = query.filter(Tenant.current_plan == plan_filter)
    
    # Appliquer le tri
    order_column = getattr(Tenant, sort_by, Tenant.created_at)
    if sort_order == "desc":
        query = query.order_by(order_column.desc())
    else:
        query = query.order_by(order_column.asc())
    
    # Pagination
    total = query.count()
    offset = (page - 1) * limit
    tenants = query.offset(offset).limit(limit).all()
    
    # Récupérer les comptes d'utilisateurs pour chaque tenant
    tenants_with_stats = []
    for tenant in tenants:
        user_count = db.query(User).filter(
            User.tenant_id == tenant.id,
            User.actif == True
        ).count()
        
        pharmacy_count = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == tenant.id,
            Pharmacy.is_active == True
        ).count()
        
        tenants_with_stats.append({
            **tenant.to_dict(),
            "user_count": user_count,
            "pharmacy_count": pharmacy_count,
            "trial_days_remaining": (
                (tenant.trial_end_date - datetime.utcnow()).days
                if tenant.trial_end_date and tenant.status == "trial"
                else None
            )
        })
    
    return {
        "tenants": tenants_with_stats,
        "pagination": {
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit,
            "has_next": offset + limit < total,
            "has_prev": page > 1
        },
        "filters": {
            "search": search,
            "status": status_filter,
            "plan": plan_filter
        }
    }

@router.post("/tenants", status_code=status.HTTP_201_CREATED)
async def create_tenant_manual(
    tenant_data: TenantCreateSchema,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Création manuelle d'un tenant par le super admin"""
    
    # Vérifier si l'email admin existe déjà
    existing_user = db.query(User).filter(
        User.email == tenant_data.email_admin.lower()
    ).first()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cet email est déjà utilisé"
        )
    
    try:
        # Générer les identifiants
        def generate_tenant_code(nom_pharmacie: str) -> str:
            prefix = nom_pharmacie[:3].upper().replace(' ', '')
            if len(prefix) < 3:
                prefix = prefix + 'PH'
            random_suffix = str(random.randint(100, 999))
            return f"{prefix}{random_suffix}"
        
        def generate_slug(nom_pharmacie: str) -> str:
            slug = nom_pharmacie.lower()
            slug = re.sub(r'[^a-z0-9\s-]', '', slug)
            slug = re.sub(r'\s+', '-', slug)
            slug = re.sub(r'-+', '-', slug)
            return slug.strip('-')
        
        tenant_code = generate_tenant_code(tenant_data.nom_pharmacie)
        slug = generate_slug(tenant_data.nom_pharmacie)
        
        # Définir les limites selon le plan
        plan_limits = {
            "starter": {"max_users": 2, "max_products": 500, "max_pharmacies": 1},
            "professional": {"max_users": 10, "max_products": 0, "max_pharmacies": 3},
            "enterprise": {"max_users": 0, "max_products": 0, "max_pharmacies": 0}
        }
        
        limits = plan_limits.get(tenant_data.plan, plan_limits["professional"])
        
        # Créer le tenant
        tenant = Tenant(
            tenant_code=tenant_code,
            slug=slug,
            nom_pharmacie=tenant_data.nom_pharmacie,
            nom_commercial=tenant_data.nom_commercial or tenant_data.nom_pharmacie,
            ville=tenant_data.ville,
            pays=tenant_data.pays,
            telephone_principal=tenant_data.telephone,
            email_admin=tenant_data.email_admin.lower(),
            nom_proprietaire=tenant_data.nom_pharmacie,
            adresse=tenant_data.adresse,
            type_pharmacie=tenant_data.type_pharmacie,
            status="trial",
            current_plan=tenant_data.plan,
            max_users=limits["max_users"],
            max_products=limits["max_products"],
            max_pharmacies=limits["max_pharmacies"],
            trial_start_date=datetime.utcnow(),
            trial_end_date=datetime.utcnow() + timedelta(days=tenant_data.trial_days),
            config={"created_by_super_admin": True}
        )
        
        db.add(tenant)
        db.flush()
        
        # Créer l'utilisateur admin
        admin_user = User(
            tenant_id=tenant.id,
            nom_complet=tenant_data.nom_pharmacie,
            email=tenant_data.email_admin.lower(),
            password_hash=hash_password(tenant_data.password_admin),
            role="admin",
            actif=True,
            telephone=tenant_data.telephone,
            adresse=tenant_data.adresse,
            activated_at=datetime.utcnow(),
            permissions={
                "gestion_utilisateurs": True,
                "gestion_stock": True,
                "gestion_ventes": True,
                "gestion_clients": True,
                "rapports": True,
                "configuration": True,
                "gestion_caisse": True,
                "gestion_fournisseurs": True
            }
        )
        
        db.add(admin_user)
        db.flush()
        
        # Créer la pharmacie principale
        pharmacy = Pharmacy(
            tenant_id=tenant.id,
            name=tenant_data.nom_pharmacie,
            address=tenant_data.adresse or tenant_data.ville,
            city=tenant_data.ville,
            phone=tenant_data.telephone,
            email=tenant_data.email_admin.lower(),
            is_active=True,
            is_main=True,
            pharmacy_code=f"{tenant_code}001"
        )
        
        db.add(pharmacy)
        db.flush()
        
        # Associer l'admin à la pharmacie
        association = UserPharmacy(
            user_id=admin_user.id,
            pharmacy_id=pharmacy.id,
            is_primary=True,
            role_in_pharmacy="admin"
        )
        
        db.add(association)
        db.commit()
        
        # Log d'audit
        log_action(
            db=db,
            tenant_id=tenant.id,
            user_id=current_user.id,
            action="SUPER_ADMIN_CREATE_TENANT",
            cible="tenant",
            description=f"Tenant créé manuellement: {tenant_data.nom_pharmacie}",
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent")
        )
        
        # Envoyer un email de bienvenue
        try:
            send_email(
                to_email=tenant_data.email_admin,
                subject="Votre compte pharmacie a été créé",
                template="tenant_welcome.html",
                context={
                    "nom_pharmacie": tenant_data.nom_pharmacie,
                    "email": tenant_data.email_admin,
                    "login_url": f"https://www.medigestpro.net/auth/login",
                    "plan": tenant_data.plan,
                    "trial_days": tenant_data.trial_days
                }
            )
        except Exception as e:
            logger.error(f"Erreur envoi email: {e}")
        
        return {
            "message": "Tenant créé avec succès",
            "tenant": {
                "id": str(tenant.id),
                "tenant_code": tenant.tenant_code,
                "nom_pharmacie": tenant.nom_pharmacie,
                "email_admin": tenant.email_admin,
                "plan": tenant.current_plan,
                "status": tenant.status,
                "trial_end_date": tenant.trial_end_date.isoformat()
            },
            "admin_user": {
                "email": admin_user.email,
                "password": tenant_data.password_admin
            },
            "instructions_sent": True
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création tenant: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la création: {str(e)}"
        )

@router.get("/tenants/{tenant_id}", status_code=status.HTTP_200_OK)
async def get_tenant_details(
    tenant_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Détails complets d'un tenant"""
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant non trouvé"
        )
    
    # Utilisateurs du tenant
    users = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.actif == True
    ).order_by(User.date_creation.desc()).all()
    
    # Pharmacies du tenant
    pharmacies = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant_id
    ).order_by(Pharmacy.is_main.desc()).all()
    
    # Logs d'audit récents du tenant
    audit_logs = db.query(AuditLog).filter(
        AuditLog.tenant_id == tenant_id
    ).order_by(AuditLog.created_at.desc()).limit(50).all()
    
    # Statistiques d'utilisation
    user_count = len(users)
    pharmacy_count = len(pharmacies)
    
    # Rôles des utilisateurs
    roles_distribution = {}
    for user in users:
        roles_distribution[user.role] = roles_distribution.get(user.role, 0) + 1
    
    # Calculer l'utilisation des limites
    usage_percentage = {
        "users": min(100, (user_count / max(tenant.max_users, 1)) * 100) if tenant.max_users > 0 else 0,
        "pharmacies": min(100, (pharmacy_count / max(tenant.max_pharmacies, 1)) * 100) if tenant.max_pharmacies > 0 else 0
    }
    
    return {
        "tenant": tenant.to_dict(),
        "statistics": {
            "users": {
                "total": user_count,
                "limit": tenant.max_users,
                "usage_percentage": round(usage_percentage["users"], 1)
            },
            "pharmacies": {
                "total": pharmacy_count,
                "limit": tenant.max_pharmacies,
                "usage_percentage": round(usage_percentage["pharmacies"], 1)
            },
            "roles_distribution": roles_distribution,
            "created": tenant.created_at.isoformat() if tenant.created_at else None,
            "last_updated": tenant.updated_at.isoformat() if tenant.updated_at else None
        },
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "nom_complet": u.nom_complet,
                "role": u.role,
                "telephone": u.telephone,
                "actif": u.actif,
                "last_login": u.last_login.isoformat() if u.last_login else None,
                "created_at": u.date_creation.isoformat() if u.date_creation else None
            } for u in users
        ],
        "pharmacies": [
            {
                "id": str(p.id),
                "name": p.name,
                "address": p.address,
                "city": p.city,
                "phone": p.phone,
                "is_main": p.is_main,
                "is_active": p.is_active,
                "created_at": p.created_at.isoformat() if p.created_at else None
            } for p in pharmacies
        ],
        "recent_activity": [
            {
                "id": str(log.id),
                "action": log.action,
                "description": log.description,
                "user_id": str(log.user_id) if log.user_id else None,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "ip_address": log.ip_address
            } for log in audit_logs
        ]
    }

@router.put("/tenants/{tenant_id}", status_code=status.HTTP_200_OK)
async def update_tenant(
    tenant_id: UUID,
    tenant_data: TenantUpdateSchema,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Mise à jour complète d'un tenant"""
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant non trouvé"
        )
    
    try:
        changes = []
        
        # Mettre à jour les champs fournis
        for field, value in tenant_data.dict(exclude_unset=True).items():
            if hasattr(tenant, field):
                old_value = getattr(tenant, field)
                if old_value != value:
                    setattr(tenant, field, value)
                    changes.append(f"{field}: {old_value} -> {value}")
        
        # Si l'email admin change, mettre à jour l'utilisateur admin
        if tenant_data.email_admin and tenant_data.email_admin != tenant.email_admin:
            admin_user = db.query(User).filter(
                User.tenant_id == tenant_id,
                User.role == "admin"
            ).first()
            
            if admin_user:
                admin_user.email = tenant_data.email_admin.lower()
                changes.append(f"admin_email: {tenant.email_admin} -> {tenant_data.email_admin}")
        
        db.commit()
        
        # Log d'audit
        if changes:
            log_action(
                db=db,
                tenant_id=tenant_id,
                user_id=current_user.id,
                action="SUPER_ADMIN_UPDATE_TENANT",
                cible="tenant",
                description=f"Mise à jour tenant {tenant.nom_pharmacie}: {', '.join(changes)}",
                ip=request.client.host if request.client else None
            )
        
        return {
            "message": "Tenant mis à jour avec succès",
            "tenant": tenant.to_dict(),
            "changes": changes
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur mise à jour tenant {tenant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la mise à jour: {str(e)}"
        )

@router.post("/tenants/{tenant_id}/actions", status_code=status.HTTP_200_OK)
async def perform_tenant_action(
    tenant_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    action: str = Query(..., pattern="^(suspend|activate|extend_trial|upgrade_plan|downgrade_plan|reset_password)$"),
    value: Optional[str] = Query(None)
):
    """Actions spécifiques sur un tenant"""
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant non trouvé"
        )
    
    try:
        result = {}
        
        if action == "suspend":
            old_status = tenant.status
            tenant.status = "suspended"
            result = {"action": "suspended", "old_status": old_status, "new_status": "suspended"}
            
        elif action == "activate":
            old_status = tenant.status
            tenant.status = "active"
            result = {"action": "activated", "old_status": old_status, "new_status": "active"}
            
        elif action == "extend_trial":
            if not value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Nombre de jours requis pour l'extension"
                )
            
            days = int(value)
            if tenant.trial_end_date:
                new_end_date = tenant.trial_end_date + timedelta(days=days)
            else:
                new_end_date = datetime.utcnow() + timedelta(days=days)
            
            old_date = tenant.trial_end_date
            tenant.trial_end_date = new_end_date
            result = {
                "action": "trial_extended",
                "days_added": days,
                "old_end_date": old_date.isoformat() if old_date else None,
                "new_end_date": new_end_date.isoformat()
            }
            
        elif action in ["upgrade_plan", "downgrade_plan"]:
            if not value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Nom du plan requis"
                )
            
            if value not in ["starter", "professional", "enterprise"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Plan invalide"
                )
            
            old_plan = tenant.current_plan
            tenant.current_plan = value
            
            # Mettre à jour les limites selon le plan
            plan_limits = {
                "starter": {"max_users": 2, "max_products": 500, "max_pharmacies": 1},
                "professional": {"max_users": 10, "max_products": 0, "max_pharmacies": 3},
                "enterprise": {"max_users": 0, "max_products": 0, "max_pharmacies": 0}
            }
            
            limits = plan_limits.get(value, plan_limits["professional"])
            tenant.max_users = limits["max_users"]
            tenant.max_products = limits["max_products"]
            tenant.max_pharmacies = limits["max_pharmacies"]
            
            result = {
                "action": "plan_changed",
                "old_plan": old_plan,
                "new_plan": value,
                "new_limits": limits
            }
            
        elif action == "reset_password":
            # Réinitialiser le mot de passe de l'admin
            admin_user = db.query(User).filter(
                User.tenant_id == tenant_id,
                User.role == "admin"
            ).first()
            
            if not admin_user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Utilisateur admin non trouvé"
                )
            
            alphabet = string.ascii_letters + string.digits
            new_password = ''.join(secrets.choice(alphabet) for _ in range(12))
            
            admin_user.password_hash = hash_password(new_password)
            
            result = {
                "action": "password_reset",
                "admin_email": admin_user.email,
                "new_password": new_password
            }
            
            # Envoyer le nouveau mot de passe par email
            try:
                send_email(
                    to_email=admin_user.email,
                    subject="Réinitialisation de votre mot de passe",
                    template="password_reset_admin.html",
                    context={
                        "nom_pharmacie": tenant.nom_pharmacie,
                        "email": admin_user.email,
                        "new_password": new_password,
                        "login_url": "https://votresite.com/auth/login"
                    }
                )
            except Exception as e:
                logger.error(f"Erreur envoi email: {e}")
        
        db.commit()
        
        # Log d'audit
        log_action(
            db=db,
            tenant_id=tenant_id,
            user_id=current_user.id,
            action=f"SUPER_ADMIN_{action.upper()}_TENANT",
            cible="tenant",
            description=f"Action {action} sur tenant {tenant.nom_pharmacie}",
            ip=request.client.host if request.client else None,
            details=result
        )
        
        return {
            "message": f"Action {action} effectuée avec succès",
            "tenant_id": str(tenant_id),
            "result": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur action {action} sur tenant {tenant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de l'action: {str(e)}"
        )

@router.delete("/tenants/{tenant_id}", status_code=status.HTTP_200_OK)
async def delete_tenant(
    tenant_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    confirm: bool = Query(False, description="Confirmation requise pour suppression")
):
    """Suppression d'un tenant (logique seulement)"""
    
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation requise. Ajoutez ?confirm=true à l'URL."
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant non trouvé"
        )
    
    try:
        # Sauvegarder les infos avant suppression
        tenant_info = {
            "id": str(tenant.id),
            "nom_pharmacie": tenant.nom_pharmacie,
            "email_admin": tenant.email_admin,
            "tenant_code": tenant.tenant_code,
            "deleted_by": str(current_user.id),
            "deleted_at": datetime.utcnow().isoformat()
        }
        
        # Marquer comme supprimé (soft delete)
        tenant.status = "deleted"
        tenant.deleted_at = datetime.utcnow()
        tenant.email_admin = f"deleted_{tenant.email_admin}"
        tenant.nom_pharmacie = f"DELETED_{tenant.nom_pharmacie}"
        
        # Désactiver tous les utilisateurs
        users = db.query(User).filter(User.tenant_id == tenant_id).all()
        for user in users:
            user.actif = False
            user.email = f"deleted_{user.email}"
        
        # Désactiver toutes les pharmacies
        pharmacies = db.query(Pharmacy).filter(Pharmacy.tenant_id == tenant_id).all()
        for pharmacy in pharmacies:
            pharmacy.is_active = False
        
        db.commit()
        
        # Log d'audit
        log_action(
            db=db,
            tenant_id=tenant_id,
            user_id=current_user.id,
            action="SUPER_ADMIN_DELETE_TENANT",
            cible="tenant",
            description=f"Tenant supprimé: {tenant_info['nom_pharmacie']}",
            ip=request.client.host if request.client else None,
            details=tenant_info
        )
        
        return {
            "message": "Tenant supprimé avec succès",
            "details": "Le tenant a été marqué comme supprimé. Tous les utilisateurs et pharmacies ont été désactivés.",
            "tenant_info": tenant_info
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur suppression tenant {tenant_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la suppression: {str(e)}"
        )

@router.post("/tenants/bulk-actions", status_code=status.HTTP_200_OK)
async def bulk_tenant_actions(
    bulk_data: BulkActionSchema,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Actions en masse sur plusieurs tenants"""
    
    if not bulk_data.tenant_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucun tenant spécifié"
        )
    
    results = []
    errors = []
    
    for tenant_id in bulk_data.tenant_ids:
        try:
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if not tenant:
                errors.append({"tenant_id": str(tenant_id), "error": "Non trouvé"})
                continue
            
            if bulk_data.action == "activate":
                tenant.status = "active"
                results.append({"tenant_id": str(tenant_id), "action": "activated", "name": tenant.nom_pharmacie})
                
            elif bulk_data.action == "suspend":
                tenant.status = "suspended"
                results.append({"tenant_id": str(tenant_id), "action": "suspended", "name": tenant.nom_pharmacie})
                
            elif bulk_data.action == "extend_trial":
                days = bulk_data.value if bulk_data.value else 7
                if tenant.trial_end_date:
                    tenant.trial_end_date = tenant.trial_end_date + timedelta(days=int(days))
                else:
                    tenant.trial_end_date = datetime.utcnow() + timedelta(days=int(days))
                results.append({"tenant_id": str(tenant_id), "action": "trial_extended", "days": days})
                
            elif bulk_data.action == "change_plan":
                if bulk_data.value in ["starter", "professional", "enterprise"]:
                    tenant.current_plan = bulk_data.value
                    results.append({"tenant_id": str(tenant_id), "action": "plan_changed", "new_plan": bulk_data.value})
                else:
                    errors.append({"tenant_id": str(tenant_id), "error": "Plan invalide"})
            
        except Exception as e:
            errors.append({"tenant_id": str(tenant_id), "error": str(e)})
    
    db.commit()
    
    # Log d'audit
    log_action(
        db=db,
        tenant_id=None,
        user_id=current_user.id,
        action=f"SUPER_ADMIN_BULK_{bulk_data.action.upper()}",
        cible="tenants",
        description=f"Action en masse sur {len(results)} tenants: {bulk_data.action}",
        ip=request.client.host if request.client else None,
        details={"results": results, "errors": errors}
    )
    
    return {
        "message": f"Action {bulk_data.action} exécutée",
        "summary": {
            "total_selected": len(bulk_data.tenant_ids),
            "successful": len(results),
            "failed": len(errors)
        },
        "results": results,
        "errors": errors
    }

# =========================
# ENDPOINTS GESTION UTILISATEURS
# =========================

@router.get("/users", status_code=status.HTTP_200_OK)
async def list_all_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    role_filter: Optional[str] = Query(None),
    tenant_id: Optional[UUID] = Query(None),
    actif: Optional[bool] = Query(None)
):
    """Liste tous les utilisateurs de la plateforme"""
    
    query = db.query(User)
    
    # Joindre avec Tenant pour avoir les infos
    query = query.join(Tenant, Tenant.id == User.tenant_id, isouter=True)
    
    # Appliquer les filtres
    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(
            or_(
                User.nom_complet.ilike(search_term),
                User.email.ilike(search_term),
                Tenant.nom_pharmacie.ilike(search_term)
            )
        )
    
    if role_filter:
        query = query.filter(User.role == role_filter)
    
    if tenant_id:
        query = query.filter(User.tenant_id == tenant_id)
    
    if actif is not None:
        query = query.filter(User.actif == actif)
    
    # Pagination
    total = query.count()
    offset = (page - 1) * limit
    users = query.order_by(User.created_at.desc()).offset(offset).limit(limit).all()
    
    # Format des résultats
    users_with_tenant = []
    for user in users:
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        users_with_tenant.append({
            "id": str(user.id),
            "email": user.email,
            "nom_complet": user.nom_complet,
            "role": user.role,
            "actif": user.actif,
            "telephone": user.telephone,
            "tenant": {
                "id": str(tenant.id) if tenant else None,
                "nom_pharmacie": tenant.nom_pharmacie if tenant else None,
                "tenant_code": tenant.tenant_code if tenant else None
            } if tenant else None,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "created_at": user.created_at.isoformat() if user.created_at else None
        })
    
    return {
        "users": users_with_tenant,
        "pagination": {
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit
        }
    }

@router.post("/users/super-admins", status_code=status.HTTP_201_CREATED)
async def create_super_admin_user(
    user_data: SuperUserCreateSchema,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Créer un nouveau super administrateur"""
    
    # Vérifier si l'email existe déjà
    existing_user = db.query(User).filter(
        User.email == user_data.email.lower()
    ).first()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cet email est déjà utilisé"
        )
    
    try:
        # Créer le super admin
        super_admin = User(
            tenant_id=None,  # Les super admins n'ont pas de tenant
            nom_complet=user_data.nom_complet,
            email=user_data.email.lower(),
            password_hash=hash_password(user_data.password),
            role="super_admin",
            actif=True,
            telephone=user_data.telephone,
            activated_at=datetime.utcnow(),
            permissions={
                "platform_management": True,
                "tenant_management": True,
                "user_management": True,
                "system_configuration": True
            }
        )
        
        db.add(super_admin)
        db.commit()
        
        # Log d'audit
        log_action(
            db=db,
            tenant_id=None,
            user_id=current_user.id,
            action="CREATE_SUPER_ADMIN",
            cible="user",
            description=f"Nouveau super admin créé: {user_data.email}",
            ip=request.client.host if request.client else None
        )
        
        return {
            "message": "Super administrateur créé avec succès",
            "user": {
                "id": str(super_admin.id),
                "email": super_admin.email,
                "nom_complet": super_admin.nom_complet,
                "role": super_admin.role,
                "created_at": super_admin.date_creation.isoformat() if super_admin.date_creation else None
            },
            "credentials": {
                "email": user_data.email,
                "password": user_data.password
            }
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création super admin: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la création: {str(e)}"
        )

@router.get("/users/{user_id}", status_code=status.HTTP_200_OK)
async def get_user_details(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Détails complets d'un utilisateur"""
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    tenant = None
    if user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    
    # Récupérer les logs de l'utilisateur
    user_logs = db.query(AuditLog).filter(
        AuditLog.user_id == user_id
    ).order_by(AuditLog.created_at.desc()).limit(50).all()
    
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "nom_complet": user.nom_complet,
            "role": user.role,
            "actif": user.actif,
            "telephone": user.telephone,
            "adresse": user.adresse,
            "permissions": user.permissions,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "login_attempts": user.login_attempts,
            "locked_until": user.locked_until.isoformat() if user.locked_until else None
        },
        "tenant": {
            "id": str(tenant.id) if tenant else None,
            "nom_pharmacie": tenant.nom_pharmacie if tenant else None,
            "tenant_code": tenant.tenant_code if tenant else None,
            "status": tenant.status if tenant else None,
            "plan": tenant.current_plan if tenant else None
        } if tenant else None,
        "activity_logs": [
            {
                "id": str(log.id),
                "action": log.action,
                "description": log.description,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "ip_address": log.ip_address
            } for log in user_logs
        ]
    }

@router.post("/users/impersonate", status_code=status.HTTP_200_OK)
async def impersonate_user(
    impersonate_data: UserImpersonateSchema,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Permet au super admin de se connecter en tant qu'un autre utilisateur"""
    
    # Récupérer l'utilisateur cible
    target_user = db.query(User).filter(User.id == impersonate_data.user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur cible non trouvé"
        )
    
    # Si un tenant_id est spécifié, vérifier que l'utilisateur appartient à ce tenant
    if impersonate_data.tenant_id and target_user.tenant_id != impersonate_data.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'utilisateur n'appartient pas au tenant spécifié"
        )
    
    # Vérifier que l'utilisateur cible est actif
    if not target_user.actif:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'utilisateur cible est désactivé"
        )
    
    # Créer un token d'impersonation
    impersonation_token = create_access_token({
        "sub": str(target_user.id),
        "tenant_id": str(target_user.tenant_id) if target_user.tenant_id else None,
        "role": target_user.role,
        "email": target_user.email,
        "impersonated_by": str(current_user.id),
        "is_impersonation": True
    }, expires_delta=timedelta(hours=1))  # Token limité à 1 heure
    
    # Log d'audit
    log_action(
        db=db,
        tenant_id=target_user.tenant_id,
        user_id=current_user.id,
        action="IMPERSONATE_USER",
        cible="user",
        description=f"Impersonation de {target_user.email}",
        ip=request.client.host if request.client else None,
        details={
            "target_user_id": str(target_user.id),
            "target_email": target_user.email,
            "target_tenant": str(target_user.tenant_id) if target_user.tenant_id else None
        }
    )
    
    return {
        "message": "Token d'impersonation créé",
        "impersonation_token": impersonation_token,
        "token_type": "bearer",
        "expires_in": 3600,  # 1 heure
        "user": {
            "id": str(target_user.id),
            "email": target_user.email,
            "nom_complet": target_user.nom_complet,
            "role": target_user.role,
            "tenant_id": str(target_user.tenant_id) if target_user.tenant_id else None
        },
        "impersonated_by": {
            "id": str(current_user.id),
            "email": current_user.email,
            "nom_complet": current_user.nom_complet
        },
        "instructions": "Utilisez ce token comme un token d'authentification normal. L'impersonation expire dans 1 heure."
    }

@router.delete("/users/{user_id}", status_code=status.HTTP_200_OK)
async def delete_user(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Suppression d'un utilisateur"""
    
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas supprimer votre propre compte"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    try:
        # Sauvegarder les informations
        user_info = {
            "id": str(user.id),
            "email": user.email,
            "nom_complet": user.nom_complet,
            "role": user.role,
            "tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "deleted_by": str(current_user.id),
            "deleted_at": datetime.utcnow().isoformat()
        }
        
        # Soft delete
        user.actif = False
        user.email = f"deleted_{user.email}"
        user.nom_complet = f"DELETED_{user.nom_complet}"
        
        db.commit()
        
        # Log d'audit
        log_action(
            db=db,
            tenant_id=user.tenant_id,
            user_id=current_user.id,
            action="DELETE_USER",
            cible="user",
            description=f"Utilisateur supprimé: {user_info['email']}",
            ip=request.client.host if request.client else None,
            details=user_info
        )
        
        return {
            "message": "Utilisateur supprimé avec succès",
            "user_info": user_info
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur suppression utilisateur {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la suppression: {str(e)}"
        )

# =========================
# ENDPOINTS SYSTÈME ET CONFIGURATION
# =========================

@router.get("/system/health", status_code=status.HTTP_200_OK)
async def system_health_check(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Vérification de santé complète du système"""
    
    checks = {
        "database": {
            "status": "healthy",
            "details": {}
        },
        "services": {
            "status": "healthy",
            "details": {}
        },
        "performance": {
            "status": "healthy",
            "details": {}
        }
    }
    
    # Vérification base de données
    try:
        db.execute("SELECT 1")
        checks["database"]["details"]["connection"] = "OK"
        
        # Vérifier les tables principales
        tables = ["tenants", "users", "pharmacies", "audit_logs"]
        for table in tables:
            try:
                db.execute(f"SELECT COUNT(*) FROM {table}")
                checks["database"]["details"][table] = "OK"
            except:
                checks["database"]["details"][table] = "ERROR"
                checks["database"]["status"] = "degraded"
                
    except Exception as e:
        checks["database"]["status"] = "unhealthy"
        checks["database"]["details"]["connection"] = f"ERROR: {str(e)}"
    
    # Vérifier les compteurs
    try:
        checks["performance"]["details"]["tenants_count"] = db.query(Tenant).count()
        checks["performance"]["details"]["users_count"] = db.query(User).count()
        checks["performance"]["details"]["active_tenants"] = db.query(Tenant).filter(
            Tenant.status == "active"
        ).count()
        
        # Vérifier la charge de la base
        result = db.execute(text("SHOW max_connections")).fetchone()
        if result:
            checks["performance"]["details"]["max_connections"] = result[0]
        
    except Exception as e:
        checks["performance"]["status"] = "degraded"
        checks["performance"]["details"]["error"] = str(e)
    
    # Vérifier les services externes (exemple)
    try:
        # Simuler une vérification SMS
        checks["services"]["details"]["sms_service"] = "OK"
    except:
        checks["services"]["details"]["sms_service"] = "ERROR"
        checks["services"]["status"] = "degraded"
    
    # Déterminer le statut global
    statuses = [check["status"] for check in checks.values()]
    if "unhealthy" in statuses:
        overall_status = "unhealthy"
    elif "degraded" in statuses:
        overall_status = "degraded"
    else:
        overall_status = "healthy"
    
    return {
        "status": overall_status,
        "timestamp": datetime.utcnow().isoformat(),
        "checks": checks,
        "recommendations": [
            "Vérifier les logs d'erreurs",
            "Surveiller l'utilisation de la mémoire",
            "Sauvegarder la base de données"
        ] if overall_status != "healthy" else ["Tous les systèmes fonctionnent normalement"]
    }

@router.get("/system/logs", status_code=status.HTTP_200_OK)
async def get_system_logs(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    level: Optional[str] = Query(None, pattern="^(ERROR|WARNING|INFO|DEBUG)$"),
    tenant_id: Optional[UUID] = Query(None),
    user_id: Optional[UUID] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    action: Optional[str] = Query(None)
):
    """Récupère les logs d'audit système"""
    
    query = db.query(AuditLog)
    
    # Appliquer les filtres
    if level:
        query = query.filter(AuditLog.action_level == level)
    
    if tenant_id:
        query = query.filter(AuditLog.tenant_id == tenant_id)
    
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    
    if start_date:
        query = query.filter(AuditLog.created_at >= start_date)
    
    if end_date:
        query = query.filter(AuditLog.created_at <= end_date)
    
    if action:
        query = query.filter(AuditLog.action.like(f"%{action}%"))
    
    # Pagination
    total = query.count()
    offset = (page - 1) * limit
    logs = query.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit).all()
    
    # Statistiques des logs
    logs_stats = {
        "total": total,
        "by_level": db.query(
            AuditLog.action_level, func.count(AuditLog.id)
        ).group_by(AuditLog.action_level).all(),
        "by_action": db.query(
            AuditLog.action, func.count(AuditLog.id)
        ).group_by(AuditLog.action).limit(10).all()
    }
    
    return {
        "logs": [
            {
                "id": str(log.id),
                "action": log.action,
                "action_level": log.action_level,
                "description": log.description,
                "user_id": str(log.user_id) if log.user_id else None,
                "tenant_id": str(log.tenant_id) if log.tenant_id else None,
                "ip_address": log.ip_address,
                "user_agent": log.user_agent,
                "details": log.details,
                "created_at": log.created_at.isoformat() if log.created_at else None
            } for log in logs
        ],
        "statistics": {
            "total": logs_stats["total"],
            "by_level": {level: count for level, count in logs_stats["by_level"]},
            "top_actions": {action: count for action, count in logs_stats["by_action"]}
        },
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": (total + limit - 1) // limit
        },
        "filters": {
            "level": level,
            "tenant_id": str(tenant_id) if tenant_id else None,
            "user_id": str(user_id) if user_id else None,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "action": action
        }
    }

@router.get("/system/settings", status_code=status.HTTP_200_OK)
async def get_system_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Récupère les paramètres système"""
    
    # Récupérer les paramètres depuis la base ou un fichier de configuration
    # Pour cet exemple, nous utilisons des valeurs par défaut
    
    settings = {
        "general": {
            "platform_name": "MEDIGEST PRO",
            "support_email": "support@votresaas.com",
            "default_language": "fr",
            "timezone": "Africa/Kinshasa"
        },
        "registration": {
            "allow_new_registrations": True,
            "require_email_verification": True,
            "require_sms_verification": True,
            "default_trial_days": 14,
            "default_plan": "professional"
        },
        "security": {
            "max_login_attempts": 5,
            "account_lock_duration_minutes": 15,
            "session_timeout_minutes": 120,
            "password_min_length": 8,
            "require_password_complexity": True
        },
        "notifications": {
            "sms_enabled": True,
            "email_enabled": True,
            "whatsapp_enabled": False,
            "send_welcome_email": True
        },
        "billing": {
            "currency": "USD",
            "starter_price": 49.99,
            "professional_price": 99.99,
            "enterprise_price": 199.99,
            "trial_conversion_rate": 15  # en pourcentage
        },
        "maintenance": {
            "mode": False,
            "message": "Maintenance planifiée",
            "scheduled_start": None,
            "scheduled_end": None
        }
    }
    
    # Récupérer les statistiques réelles pour le tableau de bord
    settings["statistics"] = {
        "active_tenants": db.query(Tenant).filter(Tenant.status == "active").count(),
        "trial_tenants": db.query(Tenant).filter(Tenant.status == "trial").count(),
        "total_users": db.query(User).count(),
        "storage_used_mb": 0,  # À implémenter selon votre système de stockage
        "api_requests_today": 0  # À implémenter avec un système de monitoring
    }
    
    return {
        "settings": settings,
        "last_updated": datetime.utcnow().isoformat(),
        "config_source": "database"  # ou "file" selon votre implémentation
    }

@router.put("/system/settings", status_code=status.HTTP_200_OK)
async def update_system_settings(
    settings_data: Dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Met à jour les paramètres système"""
    
    try:
        # Ici, vous devriez sauvegarder les paramètres dans votre base de données
        # Pour cet exemple, nous simulons la sauvegarde
        
        changes = []
        for section, values in settings_data.items():
            if isinstance(values, dict):
                for key, value in values.items():
                    changes.append(f"{section}.{key}: {value}")
        
        # Log d'audit
        log_action(
            db=db,
            tenant_id=None,
            user_id=current_user.id,
            action="UPDATE_SYSTEM_SETTINGS",
            cible="system",
            description="Mise à jour des paramètres système",
            ip=request.client.host if request.client else None,
            details={"changes": changes}
        )
        
        return {
            "message": "Paramètres système mis à jour",
            "changes": changes,
            "timestamp": datetime.utcnow().isoformat(),
            "requires_restart": False  # Indiquer si un redémarrage est nécessaire
        }
        
    except Exception as e:
        logger.error(f"Erreur mise à jour paramètres système: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la mise à jour: {str(e)}"
        )

@router.post("/system/maintenance", status_code=status.HTTP_200_OK)
async def toggle_maintenance_mode(
    request: Request,  # ✅ Paramètre sans valeur par défaut en premier
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    enabled: bool = Query(...),
    message: Optional[str] = "Maintenance en cours"
):
    """Active ou désactive le mode maintenance"""
    
    try:
        # Ici, vous devriez mettre à jour un flag en base de données
        # ou dans un cache partagé
        
        action = "activé" if enabled else "désactivé"
        
        # Log d'audit
        log_action(
            db=db,
            tenant_id=None,
            user_id=current_user.id,
            action="TOGGLE_MAINTENANCE_MODE",
            cible="system",
            description=f"Mode maintenance {action}",
            ip=request.client.host if request.client else None,
            details={"enabled": enabled, "message": message}
        )
        
        # Si le mode maintenance est activé, notifier les administrateurs
        if enabled:
            # Récupérer tous les super admins
            super_admins = db.query(User).filter(
                User.role == "super_admin",
                User.actif == True
            ).all()
            
            for admin in super_admins:
                try:
                    send_email(
                        to_email=admin.email,
                        subject="Mode maintenance activé",
                        template="maintenance_activated.html",
                        context={
                            "admin_name": admin.nom_complet,
                            "message": message,
                            "activated_by": current_user.nom_complet
                        }
                    )
                except Exception as e:
                    logger.error(f"Erreur notification admin {admin.email}: {e}")
        
        return {
            "message": f"Mode maintenance {action}",
            "maintenance_mode": enabled,
            "maintenance_message": message if enabled else None,
            "timestamp": datetime.utcnow().isoformat(),
            "notifications_sent": enabled  # Indiquer si des notifications ont été envoyées
        }
        
    except Exception as e:
        logger.error(f"Erreur mode maintenance: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la modification du mode maintenance: {str(e)}"
        )
# =========================
# ENDPOINTS RAPPORTS ET ANALYTIQUES
# =========================

@router.get("/analytics/overview", status_code=status.HTTP_200_OK)
async def get_analytics_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    period: str = Query("month", pattern="^(day|week|month|quarter|year)$")
):
    """Aperçu analytique de la plateforme"""
    
    end_date = datetime.utcnow()
    
    if period == "day":
        start_date = end_date - timedelta(days=1)
    elif period == "week":
        start_date = end_date - timedelta(days=7)
    elif period == "month":
        start_date = end_date - timedelta(days=30)
    elif period == "quarter":
        start_date = end_date - timedelta(days=90)
    else:  # year
        start_date = end_date - timedelta(days=365)
    
    # Nouveaux tenants
    new_tenants = db.query(Tenant).filter(
        Tenant.created_at.between(start_date, end_date)
    ).count()
    
    # Tenants activés (passés du trial à actif)
    activated_tenants = db.query(Tenant).filter(
        Tenant.status == "active",
        Tenant.activated_at.between(start_date, end_date)
    ).count()
    
    # Tenants ayant annulé/churn
    churned_tenants = db.query(Tenant).filter(
        Tenant.status.in_(["suspended", "inactive"]),
        Tenant.updated_at.between(start_date, end_date)
    ).count()
    
    # Nouveaux utilisateurs
    new_users = db.query(User).filter(
        User.created_at.between(start_date, end_date)
    ).count()
    
    # Activité des utilisateurs (connexions)
    active_users = db.query(User).filter(
        User.last_login.between(start_date, end_date)
    ).count()
    
    # Distribution par plan pour la période
    plans_distribution = db.query(
        Tenant.current_plan,
        func.count(Tenant.id).label("count")
    ).filter(
        Tenant.created_at.between(start_date, end_date)
    ).group_by(Tenant.current_plan).all()
    
    # Taux de rétention
    total_tenants_at_start = db.query(Tenant).filter(
        Tenant.created_at <= start_date,
        Tenant.status.in_(["active", "trial"])
    ).count()
    
    retained_tenants = db.query(Tenant).filter(
        Tenant.created_at <= start_date,
        Tenant.status.in_(["active", "trial"]),
        Tenant.updated_at >= start_date  # Ont eu de l'activité récente
    ).count()
    
    retention_rate = 0
    if total_tenants_at_start > 0:
        retention_rate = (retained_tenants / total_tenants_at_start) * 100
    
    return {
        "period": {
            "name": period,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat()
        },
        "growth_metrics": {
            "new_tenants": new_tenants,
            "activated_tenants": activated_tenants,
            "churned_tenants": churned_tenants,
            "new_users": new_users,
            "active_users": active_users,
            "net_growth": new_tenants - churned_tenants
        },
        "conversion_metrics": {
            "trial_conversion_rate": round((activated_tenants / max(new_tenants, 1)) * 100, 2),
            "retention_rate": round(retention_rate, 2),
            "user_activation_rate": round((active_users / max(new_users, 1)) * 100, 2)
        },
        "distribution": {
            "by_plan": {plan: count for plan, count in plans_distribution},
            "top_tenants_by_users": []  # À implémenter avec une requête plus complexe
        },
        "timeline": {
            "daily_signups": []  # À implémenter avec une requête groupée par jour
        }
    }

@router.get("/analytics/revenue", status_code=status.HTTP_200_OK)
async def get_revenue_analytics(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None)
):
    """Analytique des revenus (si vous avez un système de facturation)"""
    
    if not start_date:
        start_date = datetime.utcnow() - timedelta(days=30)
    if not end_date:
        end_date = datetime.utcnow()
    
    # Prix des plans (à adapter selon votre modèle de prix)
    plan_prices = {
        "starter": 49.99,
        "professional": 99.99,
        "enterprise": 199.99
    }
    
    # Récupérer les tenants payants pour la période
    paying_tenants = db.query(Tenant).filter(
        Tenant.status == "active",
        Tenant.current_plan.in_(list(plan_prices.keys())),
        Tenant.activated_at.between(start_date, end_date)
    ).all()
    
    # Calculer les revenus
    revenue_by_plan = {}
    total_mrr = 0  # Monthly Recurring Revenue
    
    for tenant in paying_tenants:
        price = plan_prices.get(tenant.current_plan, 0)
        revenue_by_plan[tenant.current_plan] = revenue_by_plan.get(tenant.current_plan, 0) + price
        total_mrr += price
    
    # Projeter les revenus annuels
    annual_revenue_projection = total_mrr * 12
    
    # Calculer le LTV (Lifetime Value) moyen
    # Cette formule est simplifiée, ajustez selon votre business model
    avg_subscription_duration_months = 6  # Valeur par défaut, à calculer réellement
    avg_ltv = total_mrr * avg_subscription_duration_months
    
    return {
        "period": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat()
        },
        "revenue": {
            "total_mrr": round(total_mrr, 2),
            "by_plan": {plan: round(revenue, 2) for plan, revenue in revenue_by_plan.items()},
            "annual_projection": round(annual_revenue_projection, 2)
        },
        "metrics": {
            "paying_tenants": len(paying_tenants),
            "avg_revenue_per_tenant": round(total_mrr / max(len(paying_tenants), 1), 2),
            "estimated_ltv": round(avg_ltv, 2),
            "conversion_rate": 0  # À calculer avec les données de trial
        },
        "forecast": {
            "next_month": round(total_mrr * 1.1, 2),  # Exemple: +10%
            "next_quarter": round(total_mrr * 3 * 1.15, 2)  # Exemple: +15%
        }
    }

@router.get("/analytics/export", status_code=status.HTTP_200_OK)
async def export_analytics_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    data_type: str = Query(..., pattern="^(tenants|users|activity|revenue)$"),
    format: str = Query("json", pattern="^(json|csv)$"),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None)
):
    """Export des données analytiques"""
    
    if not start_date:
        start_date = datetime.utcnow() - timedelta(days=30)
    if not end_date:
        end_date = datetime.utcnow()
    
    data = []
    
    if data_type == "tenants":
        tenants = db.query(Tenant).filter(
            Tenant.created_at.between(start_date, end_date)
        ).all()
        
        for tenant in tenants:
            user_count = db.query(User).filter(
                User.tenant_id == tenant.id,
                User.actif == True
            ).count()
            
            data.append({
                "id": str(tenant.id),
                "tenant_code": tenant.tenant_code,
                "nom_pharmacie": tenant.nom_pharmacie,
                "email_admin": tenant.email_admin,
                "ville": tenant.ville,
                "pays": tenant.pays,
                "plan": tenant.current_plan,
                "status": tenant.status,
                "user_count": user_count,
                "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
                "trial_end_date": tenant.trial_end_date.isoformat() if tenant.trial_end_date else None
            })
    
    elif data_type == "users":
        users = db.query(User).filter(
            User.created_at.between(start_date, end_date)
        ).all()
        
        for user in users:
            tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
            data.append({
                "id": str(user.id),
                "email": user.email,
                "nom_complet": user.nom_complet,
                "role": user.role,
                "actif": user.actif,
                "tenant": tenant.nom_pharmacie if tenant else None,
                "tenant_id": str(user.tenant_id) if user.tenant_id else None,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "last_login": user.last_login.isoformat() if user.last_login else None
            })
    
    elif data_type == "activity":
        logs = db.query(AuditLog).filter(
            AuditLog.created_at.between(start_date, end_date)
        ).order_by(AuditLog.created_at).all()
        
        for log in logs:
            data.append({
                "id": str(log.id),
                "timestamp": log.created_at.isoformat() if log.created_at else None,
                "action": log.action,
                "description": log.description,
                "user_id": str(log.user_id) if log.user_id else None,
                "tenant_id": str(log.tenant_id) if log.tenant_id else None,
                "ip_address": log.ip_address,
                "details": log.details
            })
    
    elif data_type == "revenue":
        # Implémentez l'export des données de revenus selon votre système
        data = [{"message": "Export revenue à implémenter"}]
    
    if format == "csv":
        # Convertir en CSV
        if not data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Aucune donnée à exporter"
            )
        
        # Créer le CSV en mémoire
        output = io.StringIO()
        if data:
            writer = csv.DictWriter(output, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        
        csv_content = output.getvalue()
        output.close()
        
        return JSONResponse(
            content={"csv": csv_content},
            media_type="application/json"
        )
    
    return {
        "export": {
            "type": data_type,
            "format": format,
            "records": len(data),
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            }
        },
        "data": data
    }

# =========================
# ENDPOINTS UTILITAIRES
# =========================

@router.get("/search", status_code=status.HTTP_200_OK)
async def global_search(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    query: str = Query(..., min_length=2)
):
    """Recherche globale dans la plateforme"""
    
    search_term = f"%{query}%"
    
    # Recherche dans les tenants
    tenants = db.query(Tenant).filter(
        or_(
            Tenant.nom_pharmacie.ilike(search_term),
            Tenant.email_admin.ilike(search_term),
            Tenant.tenant_code.ilike(search_term),
            Tenant.ville.ilike(search_term)
        )
    ).limit(10).all()
    
    # Recherche dans les utilisateurs
    users = db.query(User).filter(
        or_(
            User.nom_complet.ilike(search_term),
            User.email.ilike(search_term),
            User.telephone.ilike(search_term)
        )
    ).limit(10).all()
    
    # Recherche dans les pharmacies
    pharmacies = db.query(Pharmacy).filter(
        or_(
            Pharmacy.name.ilike(search_term),
            Pharmacy.city.ilike(search_term),
            Pharmacy.pharmacy_code.ilike(search_term)
        )
    ).limit(10).all()
    
    return {
        "query": query,
        "results": {
            "tenants": [
                {
                    "id": str(t.id),
                    "type": "tenant",
                    "nom_pharmacie": t.nom_pharmacie,
                    "email_admin": t.email_admin,
                    "tenant_code": t.tenant_code,
                    "status": t.status,
                    "plan": t.current_plan
                } for t in tenants
            ],
            "users": [
                {
                    "id": str(u.id),
                    "type": "user",
                    "nom_complet": u.nom_complet,
                    "email": u.email,
                    "role": u.role,
                    "tenant_id": str(u.tenant_id) if u.tenant_id else None
                } for u in users
            ],
            "pharmacies": [
                {
                    "id": str(p.id),
                    "type": "pharmacy",
                    "name": p.name,
                    "city": p.city,
                    "pharmacy_code": p.pharmacy_code,
                    "tenant_id": str(p.tenant_id)
                } for p in pharmacies
            ]
        },
        "counts": {
            "tenants": len(tenants),
            "users": len(users),
            "pharmacies": len(pharmacies),
            "total": len(tenants) + len(users) + len(pharmacies)
        }
    }

@router.get("/notifications", status_code=status.HTTP_200_OK)
async def get_system_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Récupère les notifications système importantes"""
    
    notifications = []
    
    # Tenants en trial expirant bientôt (dans 3 jours)
    expiring_trials = db.query(Tenant).filter(
        Tenant.status == "trial",
        Tenant.trial_end_date.between(
            datetime.utcnow(),
            datetime.utcnow() + timedelta(days=3)
        )
    ).count()
    
    if expiring_trials > 0:
        notifications.append({
            "id": "expiring_trials",
            "type": "warning",
            "title": f"{expiring_trials} trial(s) expire bientôt",
            "message": f"{expiring_trials} tenant(s) verront leur période d'essai se terminer dans moins de 3 jours.",
            "action": "/super-admin/tenants?status_filter=trial",
            "priority": "high"
        })
    
    # Tenants suspendés
    suspended_tenants = db.query(Tenant).filter(
        Tenant.status == "suspended"
    ).count()
    
    if suspended_tenants > 0:
        notifications.append({
            "id": "suspended_tenants",
            "type": "error",
            "title": f"{suspended_tenants} tenant(s) suspendu(s)",
            "message": f"{suspended_tenants} tenant(s) sont actuellement suspendus.",
            "action": "/super-admin/tenants?status_filter=suspended",
            "priority": "medium"
        })
    
    # Erreurs système récentes
    recent_errors = db.query(AuditLog).filter(
        AuditLog.action_level == "ERROR",
        AuditLog.created_at >= datetime.utcnow() - timedelta(hours=24)
    ).count()
    
    if recent_errors > 0:
        notifications.append({
            "id": "system_errors",
            "type": "error",
            "title": f"{recent_errors} erreur(s) système récente(s)",
            "message": f"{recent_errors} erreur(s) ont été enregistrées dans les dernières 24 heures.",
            "action": "/super-admin/system/logs?level=ERROR",
            "priority": "high"
        })
    
    # Sauvegarde de la base (exemple)
    last_backup = None  # À implémenter avec votre système de sauvegarde
    if last_backup and (datetime.utcnow() - last_backup).days > 1:
        notifications.append({
            "id": "backup_overdue",
            "type": "warning",
            "title": "Sauvegarde en retard",
            "message": "La dernière sauvegarde complète remonte à plus de 24 heures.",
            "action": "/super-admin/system/health",
            "priority": "medium"
        })
    
    return {
        "notifications": notifications,
        "unread_count": len([n for n in notifications if n["priority"] in ["high", "medium"]]),
        "last_checked": datetime.utcnow().isoformat()
    }

@router.get("/help", status_code=status.HTTP_200_OK)
async def super_admin_help():
    """Documentation de l'API Super Admin"""
    
    return {
        "api": "Super Admin Management API",
        "version": "1.0.0",
        "description": "API de gestion complète de la plateforme SaaS pour les super administrateurs",
        "endpoints": {
            "dashboard": {
                "GET /dashboard/overview": "Aperçu global de la plateforme",
                "GET /dashboard/metrics": "Métriques temporelles détaillées"
            },
            "tenants": {
                "GET /tenants": "Lister tous les tenants avec filtres",
                "POST /tenants": "Créer un tenant manuellement",
                "GET /tenants/{id}": "Détails complets d'un tenant",
                "PUT /tenants/{id}": "Mettre à jour un tenant",
                "POST /tenants/{id}/actions": "Actions spécifiques sur un tenant",
                "DELETE /tenants/{id}": "Supprimer un tenant (soft delete)",
                "POST /tenants/bulk-actions": "Actions en masse sur plusieurs tenants"
            },
            "users": {
                "GET /users": "Lister tous les utilisateurs",
                "POST /users/super-admins": "Créer un super administrateur",
                "GET /users/{id}": "Détails d'un utilisateur",
                "POST /users/impersonate": "Impersonation d'un utilisateur",
                "DELETE /users/{id}": "Supprimer un utilisateur"
            },
            "system": {
                "GET /system/health": "Vérification de santé du système",
                "GET /system/logs": "Logs d'audit système",
                "GET /system/settings": "Paramètres système",
                "PUT /system/settings": "Mettre à jour les paramètres",
                "POST /system/maintenance": "Gérer le mode maintenance"
            },
            "analytics": {
                "GET /analytics/overview": "Analytique d'aperçu",
                "GET /analytics/revenue": "Analytique des revenus",
                "GET /analytics/export": "Export des données"
            },
            "utilities": {
                "GET /search": "Recherche globale",
                "GET /notifications": "Notifications système",
                "GET /help": "Cette documentation"
            }
        },
        "authentication": "Bearer token avec rôle 'super_admin' requis",
        "rate_limits": "100 requêtes par minute par IP",
        "support": {
            "email": "superadmin-support@votresaas.com",
            "documentation": "https://docs.votresaas.com/super-admin",
            "emergency": "Tél: +243 XXX XXX XXX"
        }
    }

# =========================
# INITIALISATION SUPER ADMIN
# =========================

@router.post("/initialize", status_code=status.HTTP_201_CREATED)
async def initialize_super_admin(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Endpoint d'initialisation pour créer le premier super administrateur.
    Ne doit être accessible qu'une seule fois en production.
    """
    
    # Vérifier si un super admin existe déjà
    existing_super_admin = db.query(User).filter(
        User.role == "super_admin"
    ).first()
    
    if existing_super_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Un super administrateur existe déjà"
        )
    
    # Vérifier la clé secrète d'initialisation (à définir dans les variables d'environnement)
    init_key = request.headers.get("X-Init-Key")
    expected_key = os.getenv("SUPER_ADMIN_INIT_KEY")
    
    if not expected_key or init_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé d'initialisation invalide"
        )
    
    try:
        # Créer le super admin initial
        # Générer un mot de passe sécurisé
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        password = ''.join(secrets.choice(alphabet) for _ in range(16))
        
        super_admin = User(
            tenant_id=None,
            nom_complet="Super Administrateur Initial",
            email="superadmin@votresaas.com",
            password_hash=hash_password(password),
            role="super_admin",
            actif=True,
            activated_at=datetime.utcnow(),
            permissions={
                "platform_management": True,
                "tenant_management": True,
                "user_management": True,
                "system_configuration": True,
                "audit_logs": True,
                "analytics": True,
                "billing": True
            }
        )
        
        db.add(super_admin)
        db.commit()
        
        # Créer un enregistrement d'audit spécial
        log_action(
            db=db,
            tenant_id=None,
            user_id=super_admin.id,
            action="SYSTEM_INITIALIZATION",
            cible="system",
            description="Initialisation du système - Premier super administrateur créé",
            ip=request.client.host if request.client else None
        )
        
        return {
            "message": "Super administrateur initial créé avec succès",
            "important": "CONSERVEZ CES INFORMATIONS DANS UN ENDROIT SÉCURISÉ",
            "credentials": {
                "email": "superadmin@votresaas.com",
                "password": password
            },
            "instructions": [
                "1. Connectez-vous avec ces identifiants",
                "2. Changez immédiatement le mot de passe",
                "3. Mettez à jour l'email si nécessaire",
                "4. Créez d'autres super administrateurs si besoin",
                "5. Configurez les paramètres système"
            ],
            "security_warning": "NE PARTAGEZ PAS CES INFORMATIONS ET SUPPRIMEZ CE MESSAGE APRÈS UTILISATION"
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur initialisation super admin: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de l'initialisation: {str(e)}"
        )

@router.get("/subscriptions/statistics", status_code=status.HTTP_200_OK)
async def get_subscription_statistics(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """Statistiques des abonnements pour le super admin"""
    
    # Statistiques par plan
    plan_stats = db.query(
        Tenant.current_plan,
        func.count(Tenant.id).label('count')
    ).group_by(Tenant.current_plan).all()
    
    # Statistiques par statut
    status_stats = db.query(
        Tenant.status,
        func.count(Tenant.id).label('count')
    ).group_by(Tenant.status).all()
    
    # Revenus estimés (si vous avez un système de paiement)
    monthly_revenue = 0  # À calculer selon votre système
    
    return {
        "plans": {plan: count for plan, count in plan_stats},
        "status": {status: count for status, count in status_stats},
        "total_tenants": sum(count for _, count in plan_stats),
        "monthly_revenue_estimate": monthly_revenue,
        "timestamp": datetime.utcnow().isoformat()
    }

@router.get("/debug/whoami", status_code=status.HTTP_200_OK)
async def debug_whoami(
    current_user: User = Depends(get_current_active_user)  # Note: pas verify_super_admin
):
    """Voir l'utilisateur actuel (sans vérification de rôle)"""
    return {
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role,
            "actif": current_user.actif
        },
        "is_super_admin": current_user.role in ["superadmin", "super_admin"]
    }

@router.get("/debug/test-auth", status_code=status.HTTP_200_OK)
async def debug_test_auth(
    current_user: User = Depends(verify_super_admin)  # Avec vérification
):
    """Teste la vérification super admin"""
    return {"message": "Succès", "user": current_user.email}


@router.get("/debug/token-payload", status_code=status.HTTP_200_OK)
async def debug_token_payload(
    current_user: User = Depends(get_current_active_user)
):
    """Debug: Affiche le payload du token et les infos utilisateur"""
    
    # Récupérer le token depuis la requête (via FastAPI)
    from fastapi import Request
    
    # Note: Cette fonction doit avoir accès au token
    return {
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role,
            "role_lower": current_user.role.lower() if current_user.role else None,
            "actif": current_user.actif,
            "tenant_id": str(current_user.tenant_id) if current_user.tenant_id else None,
        },
        "jwt_payload": getattr(current_user, 'jwt_payload', None),
        "is_impersonated": getattr(current_user, 'is_impersonated', False),
        "is_super_admin": current_user.role in ["super_admin", "superadmin", "super-admin"],
        "debug": {
            "raw_role": current_user.role,
            "normalized_role": current_user.role.lower().strip() if current_user.role else None,
            "allowed_roles": ["super_admin", "superadmin", "super-admin"]
        }
    }

@router.get("/debug/whoami", status_code=status.HTTP_200_OK)
async def debug_whoami(
    current_user: User = Depends(get_current_active_user)
):
    """Debug: Voir l'utilisateur actuel et son rôle"""
    return {
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role,
            "role_lower": current_user.role.lower() if current_user.role else None,
            "actif": current_user.actif,
            "tenant_id": str(current_user.tenant_id) if current_user.tenant_id else None,
        },
        "is_super_admin": current_user.role.lower() in ["super_admin", "superadmin", "super-admin"],
        "debug": {
            "raw_role": current_user.role,
            "normalized_role": current_user.role.lower().strip() if current_user.role else None,
            "allowed_roles": ["super_admin", "superadmin", "super-admin", "admin_super"]
        }
    }
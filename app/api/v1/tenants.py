# app/api/v1/tenants.py - Version complète corrigée
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime
import logging
from pydantic import BaseModel, EmailStr, Field, validator

from app.db.session import get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.user import UserResponse
from app.core.security import hash_password
from app.api.deps import get_current_user, subscription_required
from app.services.audit_service import log_action

# Logging setup
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenants", tags=["Tenants"])

# =========================
# SCHEMAS
# =========================

class TenantRegisterRequest(BaseModel):
    """Schéma pour l'enregistrement d'un nouveau tenant"""
    email_admin: EmailStr
    password_admin: str = Field(..., min_length=8)
    confirm_password_admin: str = Field(..., min_length=8)
    nom_pharmacie: str = Field(..., min_length=2, max_length=100)
    ville: str = Field(..., min_length=2, max_length=50)
    telephone: Optional[str] = Field(None, min_length=9, max_length=20, description="Téléphone principal")
    adresse: Optional[str] = None
    nom_proprietaire: Optional[str] = None
    numero_agrement: Optional[str] = None
    
    @validator('confirm_password_admin')
    def passwords_match(cls, v, values, **kwargs):
        if 'password_admin' in values and v != values['password_admin']:
            raise ValueError('Les mots de passe ne correspondent pas')
        return v
    
    @validator('telephone')
    def validate_phone(cls, v):
        if v:
            # Nettoyer le numéro (garder uniquement les chiffres)
            cleaned = ''.join(filter(str.isdigit, v))
            if len(cleaned) < 8:
                raise ValueError('Le numéro de téléphone doit contenir au moins 8 chiffres')
            return cleaned
        return v

class TenantUpdateRequest(BaseModel):
    """Schéma pour la mise à jour d'un tenant"""
    nom_pharmacie: Optional[str] = Field(None, min_length=2, max_length=100)
    ville: Optional[str] = Field(None, min_length=2, max_length=50)
    telephone: Optional[str] = Field(None, min_length=9, max_length=20, description="Téléphone principal")
    adresse: Optional[str] = None
    nom_proprietaire: Optional[str] = None
    numero_agrement: Optional[str] = None
    email_admin: Optional[EmailStr] = None

class TenantResponse(BaseModel):
    """Schéma de réponse pour un tenant"""
    id: UUID
    nom_pharmacie: str
    ville: str
    email_admin: EmailStr
    telephone: Optional[str] = None
    adresse: Optional[str] = None
    nom_proprietaire: Optional[str] = None
    numero_agrement: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

# =========================
# FONCTIONS UTILITAIRES
# =========================

def is_valid_uuid(uuid_string: str) -> bool:
    """Vérifie si une chaîne est un UUID valide"""
    try:
        UUID(uuid_string)
        return True
    except ValueError:
        return False

# =========================
# ENDPOINTS TENANTS
# =========================

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register_tenant(
    data: TenantRegisterRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Crée un tenant (pharmacie) et son admin principal.
    """
    try:
        # Vérifier si l'email admin existe déjà
        existing_user = db.query(User).filter(User.email == data.email_admin.lower()).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email admin déjà utilisé"
            )

        # 1️⃣ Créer le tenant
        tenant = Tenant(
            nom_pharmacie=data.nom_pharmacie,
            ville=data.ville,
            email_admin=data.email_admin.lower(),
            telephone_principal=data.telephone,
            adresse=data.adresse,
            nom_proprietaire=data.nom_proprietaire or data.nom_pharmacie,
            numero_agrement=data.numero_agrement,
            status="active"
        )
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        
        logger.info(f"Tenant créé: {data.nom_pharmacie} ({tenant.id})")

        # 2️⃣ Créer l'admin principal
        admin_user = User(
            tenant_id=tenant.id,
            nom_complet=data.nom_proprietaire or f"Admin {data.nom_pharmacie}",
            email=data.email_admin.lower(),
            password_hash=hash_password(data.password_admin),
            role="admin",
            actif=True,
            telephone=data.telephone,
            adresse=data.adresse,
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
        db.commit()
        db.refresh(admin_user)
        
        logger.info(f"Admin principal créé: {admin_user.email}")

        # Log audit
        log_action(
            db=db,
            tenant_id=tenant.id,
            user_id=admin_user.id,
            action="TENANT_REGISTER",
            cible="tenant",
            description=f"Tenant créé: {data.nom_pharmacie}, admin: {data.email_admin}",
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent")
        )

        return {
            "message": "Tenant et admin créés avec succès",
            "user": admin_user.to_dict(include_tenant=True),
            "tenant": {
                "id": str(tenant.id),
                "nom_pharmacie": tenant.nom_pharmacie,
                "ville": tenant.ville
            },
            "login_url": "/auth/login"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création tenant: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la création du tenant: {str(e)}"
        )

@router.get("/me", response_model=Dict[str, Any])
def my_tenant(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Retourne les infos du tenant de l'utilisateur connecté
    """
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant non trouvé"
        )

    return {
        "message": "Accès autorisé",
        "tenant": {
            "id": str(tenant.id),
            "nom_pharmacie": tenant.nom_pharmacie,
            "ville": tenant.ville,
            "email_admin": tenant.email_admin,
            "telephone": tenant.telephone_principal,
            "adresse": tenant.adresse,
            "nom_proprietaire": tenant.nom_proprietaire,
            "numero_agrement": tenant.numero_agrement,
            "status": tenant.status,
            "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
            "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None
        },
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role,
            "nom_complet": current_user.nom_complet
        }
    }

@router.get("/", response_model=List[TenantResponse])
def list_tenants(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None)
):
    """
    Liste tous les tenants (admin/super_admin seulement)
    """
    if current_user.role not in ["super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Seuls les super admins peuvent voir tous les tenants."
        )

    # Construire la requête
    query = db.query(Tenant)
    
    # Appliquer les filtres
    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(
            db.or_(
                Tenant.nom_pharmacie.ilike(search_term),
                Tenant.email_admin.ilike(search_term),
                Tenant.ville.ilike(search_term)
            )
        )
    
    if status_filter:
        query = query.filter(Tenant.status == status_filter)

    # Pagination
    total = query.count()
    offset = (page - 1) * limit
    tenants = query.order_by(Tenant.created_at.desc()).offset(offset).limit(limit).all()
    
    return tenants

@router.get("/{tenant_id}", response_model=TenantResponse)
def get_tenant(
    tenant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les informations d'un tenant spécifique
    """
    # Vérifier si c'est un UUID valide
    if not is_valid_uuid(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de tenant invalide"
        )
    
    # Les super admins peuvent voir tous les tenants, les autres seulement leur tenant
    if current_user.role not in ["super_admin"]:
        if str(current_user.tenant_id) != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous ne pouvez voir que votre propre tenant"
            )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant non trouvé"
        )
    
    return TenantResponse(
        id=tenant.id,
        nom_pharmacie=tenant.nom_pharmacie,
        ville=tenant.ville,
        email_admin=tenant.email_admin,
        telephone=tenant.telephone_principal,  # ✅ CORRECTION
        adresse=tenant.adresse,
        nom_proprietaire=tenant.nom_proprietaire,
        numero_agrement=tenant.numero_agrement,
        status=tenant.status,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at
    )

@router.put("/{tenant_id}", response_model=TenantResponse)
def update_tenant(
    tenant_id: str,
    tenant_data: TenantUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Met à jour les informations d'un tenant
    """
    # Vérifier si c'est un UUID valide
    if not is_valid_uuid(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de tenant invalide"
        )
    
    # Vérifier les permissions
    if current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    # Vérifier que l'utilisateur peut modifier ce tenant
    if current_user.role != "super_admin" and str(current_user.tenant_id) != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous ne pouvez modifier que votre propre tenant"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant non trouvé"
        )
    
    try:
        # Mise à jour des champs
        if tenant_data.nom_pharmacie is not None:
            tenant.nom_pharmacie = tenant_data.nom_pharmacie
            
        if tenant_data.ville is not None:
            tenant.ville = tenant_data.ville
            
        if tenant_data.telephone is not None:
            tenant.telephone_principal = tenant_data.telephone
            
        if tenant_data.adresse is not None:
            tenant.adresse = tenant_data.adresse
            
        if tenant_data.nom_proprietaire is not None:
            tenant.nom_proprietaire = tenant_data.nom_proprietaire
            
        if tenant_data.numero_agrement is not None:
            tenant.numero_agrement = tenant_data.numero_agrement
            
        if tenant_data.email_admin is not None and tenant_data.email_admin != tenant.email_admin:
            # Vérifier si le nouvel email admin est déjà utilisé
            existing_user = db.query(User).filter(
                User.email == tenant_data.email_admin.lower()
            ).first()
            if existing_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cet email admin est déjà utilisé"
                )
            tenant.email_admin = tenant_data.email_admin.lower()
        
        db.commit()
        db.refresh(tenant)
        
        # Log audit
        log_action(
            db=db,
            tenant_id=tenant.id,
            user_id=current_user.id,
            action="UPDATE_TENANT",
            cible="tenant",
            description=f"Mise à jour tenant: {tenant.nom_pharmacie}",
            ip=request.client.host if request.client else None
        )
        
        return tenant
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur mise à jour tenant {tenant_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la mise à jour: {str(e)}"
        )

@router.patch("/{tenant_id}/status", response_model=TenantResponse)
def update_tenant_status(
    tenant_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    status_data: Dict[str, str] = {"status": "active"}
):
    """
    Met à jour le statut d'un tenant (super_admin seulement)
    """
    if current_user.role not in ["super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Seuls les super admins peuvent modifier le statut d'un tenant."
        )
    
    # Vérifier si c'est un UUID valide
    if not is_valid_uuid(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de tenant invalide"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant non trouvé"
        )
    
    new_status = status_data.get("status")
    if new_status not in ["active", "suspended", "inactive"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Statut invalide. Valeurs autorisées: active, suspended, inactive"
        )
    
    try:
        old_status = tenant.status
        tenant.status = new_status
        db.commit()
        db.refresh(tenant)
        
        # Log audit
        log_action(
            db=db,
            tenant_id=tenant.id,
            user_id=current_user.id,
            action="UPDATE_TENANT_STATUS",
            cible="tenant",
            description=f"Statut tenant changé de {old_status} à {new_status}: {tenant.nom_pharmacie}",
            ip=request.client.host if request.client else None
        )
        
        return tenant
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur mise à jour statut tenant {tenant_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la mise à jour du statut: {str(e)}"
        )

@router.get("/{tenant_id}/user-count")
def get_tenant_user_count(
    tenant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Retourne le nombre d'utilisateurs actifs pour un tenant
    """
    # Vérifier les permissions
    if current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    # Vérifier que le tenant_id est un UUID valide
    if not is_valid_uuid(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de tenant invalide"
        )
    
    # Vérifier que l'utilisateur peut voir ce tenant
    if current_user.role != "super_admin" and str(current_user.tenant_id) != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous ne pouvez voir que votre propre tenant"
        )
    
    user_count = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.actif == True
    ).count()
    
    return {
        "tenant_id": tenant_id,
        "user_count": user_count,
        "timestamp": datetime.utcnow().isoformat()
    }

@router.get("/{tenant_id}/statistics")
def get_tenant_statistics(
    tenant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Retourne des statistiques détaillées pour un tenant
    """
    # Vérifier les permissions
    if current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    # Vérifier que le tenant_id est un UUID valide
    if not is_valid_uuid(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de tenant invalide"
        )
    
    # Vérifier que l'utilisateur peut voir ce tenant
    if current_user.role != "super_admin" and str(current_user.tenant_id) != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous ne pouvez voir que votre propre tenant"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant non trouvé"
        )
    
    # Compter les utilisateurs par rôle
    users_by_role = db.query(
        User.role, db.func.count(User.id).label("count")
    ).filter(
        User.tenant_id == tenant_id,
        User.actif == True
    ).group_by(User.role).all()
    
    role_distribution = {role: count for role, count in users_by_role}
    
    # Derniers utilisateurs créés
    recent_users = db.query(User).filter(
        User.tenant_id == tenant_id
    ).order_by(User.date_creation.desc()).limit(5).all()
    
    recent_users_data = [{
        "id": str(u.id),
        "nom_complet": u.nom_complet,
        "email": u.email,
        "role": u.role,
        "date_creation": u.date_creation.isoformat() if u.date_creation else None
    } for u in recent_users]
    
    # Nombre total d'utilisateurs
    total_users = sum(role_distribution.values())
    
    return {
        "tenant": {
            "id": str(tenant.id),
            "nom_pharmacie": tenant.nom_pharmacie,
            "status": tenant.status,
            "created_at": tenant.created_at.isoformat() if tenant.created_at else None
        },
        "statistics": {
            "total_users": total_users,
            "role_distribution": role_distribution,
            "recent_users": recent_users_data
        },
        "timestamp": datetime.utcnow().isoformat()
    }

@router.get("/secure")
def secure_route(user: User = Depends(subscription_required)):
    """Route sécurisée SaaS"""
    return {
        "message": "Accès SaaS autorisé",
        "user": {
            "email": user.email,
            "role": user.role,
            "tenant_id": str(user.tenant_id)
        }
    }

@router.get("/health")
def health_check():
    """
    Endpoint de vérification de santé de l'API tenants
    """
    return {
        "status": "healthy",
        "service": "tenants-api",
        "timestamp": datetime.utcnow().isoformat(),
        "endpoints": [
            "POST /tenants/register - Créer un tenant",
            "GET /tenants/me - Mon tenant",
            "GET /tenants/ - Lister les tenants (super_admin)",
            "GET /tenants/{id} - Voir un tenant",
            "PUT /tenants/{id} - Modifier un tenant",
            "PATCH /tenants/{id}/status - Modifier statut (super_admin)",
            "GET /tenants/{id}/user-count - Nombre d'utilisateurs",
            "GET /tenants/{id}/statistics - Statistiques",
            "GET /tenants/secure - Route sécurisée SaaS"
        ]
    }

@router.get("/{tenant_id}/subscription")
async def redirect_subscription(
    tenant_id: UUID,
    db: Session = Depends(get_db)
):
    """
    Redirige vers la nouvelle route d'abonnement.
    """
    raise HTTPException(
        status_code=307,  # Temporary Redirect
        detail="Route déplacée",
        headers={"Location": f"/api/v1/subscriptions/{tenant_id}/status"})
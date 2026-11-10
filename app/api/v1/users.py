# app/api/v1/users.py - Version complète corrigée

from fastapi import APIRouter, Depends, HTTPException, status, Request, Query, Body
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional, Dict, Any
import logging
import traceback
from sqlalchemy import or_
import sys
from datetime import datetime, timedelta
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.db.session import get_db
from app.models.user import User
from app.models.user_pharmacy import UserPharmacy
from app.models.pharmacy import Pharmacy
from app.models.branch import Branch
from app.models.user_branch import UserBranch
from app.models.tenant import Tenant
from app.core.constants import ROLE_PATTERN, VALID_ROLES
from app.schemas.user import UserCreate, UserUpdate, UserResponse, UserListSchema, AdminChangePasswordRequest
from app.core.security import hash_password, verify_password
from app.services.audit_service import log_action
from app.services.user_branch_service import (
    BranchAssignmentError,
    assign_user_to_branch,
    check_branch_user_quota,
    resolve_required_branch,
)
from app.api.deps import (
    get_current_tenant,
    get_current_user,
    get_current_active_user,
    get_current_admin_user,  # ← Importer la dépendance admin
    require_role,
    require_permission,
    get_current_pharmacy_entity,
    get_current_branch_entity,
    can_user_access_pharmacy
)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('app.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])

# =========================
# SCHEMAS ADDITIONNELS
# =========================

class SimpleUserProfile(BaseModel):
    """Schéma simplifié pour affichage utilisateur"""
    id: UUID
    email: EmailStr
    nom_complet: str
    role: str
    telephone: Optional[str] = None
    actif: bool
    date_creation: Optional[datetime] = None
    last_login: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class UserCreateRequest(BaseModel):
    """
    Schéma pour la création d'utilisateur - Compatible avec le frontend
    Le frontend envoie 'full_name' mais nous stockons 'nom_complet'
    """
    full_name: str = Field(..., min_length=2, max_length=100, alias="full_name")
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: str = Field(..., pattern="^(admin|pharmacien|vendeur|caissier|gestionnaire|comptable|preparateur|stockiste)$")
    telephone: Optional[str] = Field(None, max_length=20)
    adresse: Optional[str] = None
    pharmacy_id: Optional[str] = None
    # OBLIGATOIRE : chaque utilisateur est affecté à une branche (user branche)
    branch_id: str = Field(..., min_length=1, description="Branche d'affectation de l'utilisateur (obligatoire)")
    is_active: Optional[bool] = True
    permissions: Optional[Dict[str, bool]] = None
    
    class Config:
        populate_by_name = True
        from_attributes = True

    @field_validator('branch_id')
    @classmethod
    def validate_branch_id(cls, v: str) -> str:
        v = (v or '').strip()
        try:
            UUID(v)
        except (ValueError, AttributeError):
            raise ValueError("branch_id doit être un UUID de branche valide")
        return v
    
    @field_validator('full_name')
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        if not v or len(v.strip()) < 2:
            raise ValueError('Le nom complet doit contenir au moins 2 caractères')
        return v.strip()
    
    @field_validator('telephone')
    @classmethod
    def validate_telephone(cls, v: Optional[str]) -> Optional[str]:
        if v:
            cleaned = v.replace(' ', '').replace('-', '')
            if not cleaned.replace('+', '').isdigit():
                raise ValueError('Le numéro de téléphone doit contenir uniquement des chiffres et éventuellement +')
        return v


class UserUpdateRequest(BaseModel):
    """Schéma pour la mise à jour d'utilisateur"""
    nom_complet: Optional[str] = Field(None, min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=8)
    role: Optional[str] = Field(None, pattern=ROLE_PATTERN)
    telephone: Optional[str] = None
    adresse: Optional[str] = None
    actif: Optional[bool] = None
    active_pharmacy_id: Optional[str] = None
    active_branch_id: Optional[str] = None
    
    class Config:
        from_attributes = True


class ChangePasswordRequest(BaseModel):
    """Schéma pour le changement de mot de passe"""
    old_password: str
    new_password: str = Field(..., min_length=8)


# =========================
# FONCTIONS UTILITAIRES
# =========================

def is_valid_uuid(uuid_string):
    """Vérifie si une chaîne est un UUID valide"""
    try:
        UUID(uuid_string)
        return True
    except ValueError:
        return False


def get_default_permissions(role: str) -> dict:
    """Retourne les permissions par défaut selon le rôle."""
    permissions = {
        "admin": {
            "gestion_utilisateurs": True,
            "gestion_stock": True,
            "gestion_ventes": True,
            "gestion_clients": True,
            "rapports": True,
            "configuration": True,
            "gestion_caisse": True,
            "gestion_fournisseurs": True
        },
        "pharmacien": {
            "gestion_utilisateurs": False,
            "gestion_stock": True,
            "gestion_ventes": True,
            "gestion_clients": True,
            "rapports": True,
            "configuration": False,
            "gestion_caisse": True,
            "gestion_fournisseurs": True
        },
        "vendeur": {
            "gestion_utilisateurs": False,
            "gestion_stock": False,
            "gestion_ventes": True,
            "gestion_clients": True,
            "rapports": False,
            "configuration": False,
            "gestion_caisse": True,
            "gestion_fournisseurs": False
        },
        "caissier": {
            "gestion_utilisateurs": False,
            "gestion_stock": False,
            "gestion_ventes": True,
            "gestion_clients": True,
            "rapports": False,
            "configuration": False,
            "gestion_caisse": True,
            "gestion_fournisseurs": False
        },
        "gestionnaire": {
            "gestion_utilisateurs": True,
            "gestion_stock": True,
            "gestion_ventes": True,
            "gestion_clients": True,
            "rapports": True,
            "configuration": True,
            "gestion_caisse": True,
            "gestion_fournisseurs": True
        },
        "comptable": {
            "gestion_utilisateurs": False,
            "gestion_stock": False,
            "gestion_ventes": True,
            "gestion_clients": True,
            "rapports": True,
            "configuration": False,
            "gestion_caisse": False,
            "gestion_fournisseurs": False
        },
        "preparateur": {
            "gestion_utilisateurs": False,
            "gestion_stock": True,
            "gestion_ventes": False,
            "gestion_clients": False,
            "rapports": False,
            "configuration": False,
            "gestion_caisse": False,
            "gestion_fournisseurs": True
        },
        "stockiste": {
            "gestion_utilisateurs": False,
            "gestion_stock": True,
            "gestion_ventes": False,
            "gestion_clients": False,
            "rapports": False,
            "configuration": False,
            "gestion_caisse": False,
            "gestion_fournisseurs": True
        }
    }
    return permissions.get(role, {})


# =========================
# ENDPOINTS SPÉCIAUX (DOIVENT ÊTRE AVANT LES ROUTES AVEC PARAMÈTRES)
# =========================

@router.get("/sessions/stats", status_code=status.HTTP_200_OK)
def get_session_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),  # ← Utiliser admin_user
    date_range: str = Query("month", description="Période: day, week, month, year"),
    pharmacy_id: Optional[str] = Query(None, description="Filtrer par pharmacie"),
    branch_id: Optional[str] = Query(None, description="Filtrer par branche")
):
    """Récupère les statistiques de sessions des utilisateurs"""
    # Calculer la date de début selon la période
    now = datetime.utcnow()
    if date_range == "day":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif date_range == "week":
        start_date = now - timedelta(days=now.weekday())
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    elif date_range == "month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif date_range == "year":
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start_date = now - timedelta(days=30)
    
    # Construire la requête de base
    query = db.query(User).filter(User.tenant_id == current_user.tenant_id)
    
    # Filtrer par pharmacie si spécifiée
    if pharmacy_id and is_valid_uuid(pharmacy_id):
        pharmacy_uuid = UUID(pharmacy_id)
        query = query.filter(User.pharmacy_associations.any(pharmacy_id=pharmacy_uuid))
    
    # Filtrer par branche si spécifiée
    if branch_id and is_valid_uuid(branch_id):
        branch_uuid = UUID(branch_id)
        query = query.filter(User.active_branch_id == branch_uuid)
    
    # Récupérer les utilisateurs
    users = query.filter(User.actif == True).all()
    
    # Compter les sessions actives (last_login dans les 15 dernières minutes)
    threshold = now - timedelta(minutes=15)
    active_sessions = query.filter(User.last_login >= threshold).count()
    
    # Connexions par jour (derniers 7 jours)
    from sqlalchemy import func, cast, Date
    
    daily_logins = db.query(
        cast(User.last_login, Date).label("login_date"),
        func.count(User.id).label("count")
    ).filter(
        User.tenant_id == current_user.tenant_id,
        User.last_login >= now - timedelta(days=7)
    ).group_by(cast(User.last_login, Date)).all()
    
    daily_logins_data = [
        {"date": str(row.login_date), "count": row.count}
        for row in daily_logins
    ]
    
    # Connexions par rôle
    role_stats = db.query(
        User.role,
        func.count(User.id).label("count")
    ).filter(
        User.tenant_id == current_user.tenant_id
    ).group_by(User.role).all()
    
    role_distribution = {role: count for role, count in role_stats}
    
    return {
        "total_users": len(users),
        "active_sessions": active_sessions,
        "inactive_sessions": len(users) - active_sessions,
        "date_range": date_range,
        "daily_logins": daily_logins_data,
        "role_distribution": role_distribution,
        "timestamp": now.isoformat()
    }


@router.get("/online-users", status_code=status.HTTP_200_OK)
def get_all_online_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),  # ← Utiliser admin_user
):
    """Récupère tous les utilisateurs en ligne du tenant"""
    # Calculer le seuil d'inactivité (15 minutes)
    threshold = datetime.utcnow() - timedelta(minutes=15)
    
    online_users = db.query(User).filter(
        User.tenant_id == current_user.tenant_id,
        User.actif == True,
        User.last_login >= threshold
    ).all()
    
    result = []
    for user in online_users:
        if user.last_login:
            login_duration = datetime.utcnow() - user.last_login
            duration_minutes = int(login_duration.total_seconds() / 60)
            
            if duration_minutes < 5:
                status = "online"
            elif duration_minutes < 15:
                status = "idle"
            else:
                status = "away"
        else:
            duration_minutes = 0
            status = "away"
        
        result.append({
            "id": str(user.id),
            "nom_complet": user.nom_complet,
            "email": user.email,
            "role": user.role,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "login_duration": f"{duration_minutes} min",
            "status": status,
            "active_pharmacy_id": str(user.active_pharmacy_id) if user.active_pharmacy_id else None,
            "active_branch_id": str(user.active_branch_id) if user.active_branch_id else None
        })
    
    return {
        "tenant_id": str(current_user.tenant_id),
        "online_count": len(online_users),
        "users": result,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/me/profile", status_code=status.HTTP_200_OK)
def get_my_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Récupère le profil de l'utilisateur connecté."""
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    
    profile = current_user.to_dict(include_tenant=False, include_pharmacies=True)
    if tenant:
        profile["pharmacie"] = {
            "id": str(tenant.id),
            "nom": getattr(tenant, 'nom_pharmacie', 'N/A'),
            "ville": getattr(tenant, 'ville', 'N/A'),
            "adresse": getattr(tenant, 'adresse', 'N/A')
        }
    else:
        profile["pharmacie"] = None
    
    return profile


@router.get("/me/profile/", status_code=status.HTTP_200_OK)
def get_my_profile_with_slash(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Alias pour GET /users/me/profile avec slash"""
    return get_my_profile(db, current_user)


@router.post("/me/change-password", status_code=status.HTTP_200_OK)
def change_my_password(
    request_data: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Permet à un utilisateur de changer son propre mot de passe."""
    # Vérifier l'ancien mot de passe
    if not verify_password(request_data.old_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ancien mot de passe incorrect"
        )
    
    # Vérifier que le nouveau mot de passe est différent
    if verify_password(request_data.new_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le nouveau mot de passe doit être différent de l'ancien"
        )
    
    # Vérifier la longueur du mot de passe
    if len(request_data.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le mot de passe doit contenir au moins 8 caractères"
        )
    
    try:
        current_user.password_hash = hash_password(request_data.new_password)
        db.commit()
        
        log_action(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action="CHANGE_PASSWORD",
            cible="user",
            description=f"Changement de mot de passe pour: {current_user.email}",
            ip=None
        )
        
        return {
            "message": "Mot de passe changé avec succès",
            "details": "Vous serez déconnecté à votre prochaine connexion"
        }
    
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur changement mot de passe: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors du changement de mot de passe: {str(e)}"
        )


# =========================
# ENDPOINTS GÉNÉRAUX
# =========================

@router.get("/", status_code=status.HTTP_200_OK)
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),  # ← Utiliser admin_user
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    actif: Optional[bool] = Query(None)
):
    """
    Liste les utilisateurs du tenant avec pagination et filtres.
    """
    # Construire la requête
    query = db.query(User).filter(User.tenant_id == current_user.tenant_id)
    
    # Appliquer les filtres
    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(
            (User.nom_complet.ilike(search_term)) |
            (User.email.ilike(search_term)) |
            (User.telephone.ilike(search_term))
        )
    
    if role:
        query = query.filter(User.role == role)
    
    if actif is not None:
        query = query.filter(User.actif == actif)

    # Pagination
    total = query.count()
    offset = (page - 1) * limit
    users = query.order_by(User.created_at.desc()).offset(offset).limit(limit).all()  # ← created_at
    
    # Format de réponse
    result = []
    for user in users:
        user_dict = user.to_dict(include_tenant=False, include_pharmacies=True)
        user_dict["can_edit"] = (
            current_user.id != user.id and
            current_user.role in ["admin", "super_admin"]
        )
        result.append(user_dict)

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "users": result
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_user(
    user_data: UserCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),  # ← Utiliser admin_user
):
    """Crée un utilisateur pour le tenant de l'admin connecté."""
    # Vérifier que l'admin est actif
    if not current_user.actif:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre compte est désactivé"
        )

    # Vérifier si le tenant existe
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant non trouvé"
        )
    
    # Vérifier l'unicité de l'email dans le tenant
    existing_user = db.query(User).filter(
        User.email == user_data.email.lower().strip(),
        User.tenant_id == current_user.tenant_id
    ).first()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cet email est déjà utilisé dans votre pharmacie"
        )

    # Branche d'affectation OBLIGATOIRE (existante, active, du même tenant) — aucun repli par défaut
    try:
        target_branch = resolve_required_branch(db, current_user.tenant_id, user_data.branch_id)
        check_branch_user_quota(db, target_branch)
    except BranchAssignmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.to_detail())

    # Vérifier les limites du plan
    user_count = db.query(User).filter(
        User.tenant_id == current_user.tenant_id,
        User.actif == True
    ).count()
    
    max_users = getattr(tenant, 'max_users', 10)
    
    if user_count >= max_users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Limite d'utilisateurs atteinte ({max_users} maximum). Veuillez mettre à jour votre plan."
        )

    # Vérifier les rôles autorisés
    allowed_roles = ["pharmacien", "vendeur", "caissier", "gestionnaire", "comptable", "preparateur", "stockiste"]
    if current_user.role != "super_admin":
        allowed_roles = [role for role in allowed_roles if role != "admin"]
    
    if user_data.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Rôle non autorisé. Rôles autorisés: {', '.join(allowed_roles)}"
        )

    try:
        # Création de l'utilisateur
        new_user = User(
            tenant_id=current_user.tenant_id,
            nom_complet=user_data.full_name,
            email=user_data.email.lower().strip(),
            password_hash=hash_password(user_data.password),
            role=user_data.role,
            actif=user_data.is_active if user_data.is_active is not None else True,
            telephone=user_data.telephone,
            adresse=user_data.adresse,
            permissions=user_data.permissions or get_default_permissions(user_data.role)
        )
        
        db.add(new_user)
        db.flush()
        
        # Affectation à la branche choisie (user branche) : branch_id, active_branch_id, user_branches
        assign_user_to_branch(
            db, new_user, target_branch, role=user_data.role, created_by=current_user.id
        )

        # La pharmacie de l'utilisateur est celle de sa branche
        pharmacy_id = target_branch.parent_pharmacy_id
        if user_data.pharmacy_id and is_valid_uuid(user_data.pharmacy_id) and pharmacy_id \
                and UUID(user_data.pharmacy_id) != pharmacy_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "branch_pharmacy_mismatch",
                    "message": "La branche choisie n'appartient pas à la pharmacie indiquée.",
                },
            )
        if pharmacy_id:
            pharmacy = db.query(Pharmacy).filter(
                Pharmacy.id == pharmacy_id,
                Pharmacy.tenant_id == current_user.tenant_id
            ).first()
            if pharmacy:
                db.add(UserPharmacy(
                    user_id=new_user.id,
                    pharmacy_id=pharmacy.id,
                    is_primary=(user_data.role in ["admin", "gestionnaire"]),
                    role_in_pharmacy=user_data.role,
                    can_manage=(user_data.role in ["admin", "gestionnaire"])
                ))
                new_user.active_pharmacy_id = pharmacy.id

        logger.info(f"Branche assignée à {new_user.email}: {target_branch.name}")

        db.commit()
        db.refresh(new_user)

        log_action(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action="CREATE_USER",
            cible="user",
            description=f"Création utilisateur: {new_user.email} (role={new_user.role})",
            ip=request.client.host if request.client else None
        )

        pharmacie_nom = getattr(tenant, 'nom_pharmacie', 'Votre pharmacie')
        
        branch_name = None
        if new_user.active_branch_id:
            branch = db.query(Branch).filter(Branch.id == new_user.active_branch_id).first()
            branch_name = branch.name if branch else None

        return {
            "message": "Utilisateur créé avec succès",
            "user": {
                "id": str(new_user.id),
                "email": new_user.email,
                "nom_complet": new_user.nom_complet,
                "role": new_user.role,
                "telephone": new_user.telephone,
                "actif": new_user.actif,
                "created_at": new_user.created_at.isoformat() if new_user.created_at else None,  # ← created_at
                "active_pharmacy_id": str(new_user.active_pharmacy_id) if new_user.active_pharmacy_id else None,
                "active_branch_id": str(new_user.active_branch_id) if new_user.active_branch_id else None,
                "branch_id": str(new_user.branch_id) if new_user.branch_id else None,
                "pharmacie": pharmacie_nom,
                "branche": branch_name
            },
            "instructions": f"L'utilisateur peut se connecter à votre pharmacie '{pharmacie_nom}' avec son email et mot de passe"
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création utilisateur: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la création de l'utilisateur: {str(e)}"
        )

@router.post("/{user_id}/change-password", status_code=status.HTTP_200_OK)
def admin_change_user_password(
    user_id: str,
    request_data: AdminChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),  # Seul un admin peut faire ça
):
    """
    Permet à un administrateur de changer le mot de passe d'un utilisateur.
    """
    # Vérifier que l'ID est valide
    if not is_valid_uuid(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID utilisateur invalide"
        )
    
    user_uuid = UUID(user_id)
    
    # Récupérer l'utilisateur
    user = db.query(User).filter(
        User.id == user_uuid,
        User.tenant_id == current_user.tenant_id
    ).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé dans votre tenant"
        )
    
    # Empêcher un admin de modifier son propre mot de passe via cet endpoint
    # (il devrait utiliser /me/change-password à la place)
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Utilisez l'endpoint /me/change-password pour modifier votre propre mot de passe"
        )
    
    try:
        # Changer le mot de passe
        user.password_hash = hash_password(request_data.new_password)
        db.commit()
        
        # Audit log
        log_action(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action="ADMIN_CHANGE_USER_PASSWORD",
            cible="user",
            description=f"Changement de mot de passe pour l'utilisateur: {user.email} (par admin)",
            ip=request.client.host if request.client else None
        )
        
        logger.info(f"✅ Mot de passe changé pour l'utilisateur {user.email} par admin {current_user.email}")
        
        return {
            "success": True,
            "message": "Mot de passe modifié avec succès",
            "user_id": str(user.id),
            "user_email": user.email
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors du changement de mot de passe: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors du changement de mot de passe: {str(e)}"
        )

@router.post("/{user_id}/reset-password", status_code=status.HTTP_200_OK)
def admin_reset_user_password(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Réinitialise le mot de passe d'un utilisateur avec un mot de passe temporaire.
    """
    import secrets
    import string
    
    if not is_valid_uuid(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID utilisateur invalide"
        )
    
    user_uuid = UUID(user_id)
    
    user = db.query(User).filter(
        User.id == user_uuid,
        User.tenant_id == current_user.tenant_id
    ).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Utilisez l'endpoint /me/change-password pour modifier votre propre mot de passe"
        )
    
    # Générer un mot de passe temporaire
    alphabet = string.ascii_letters + string.digits
    temp_password = ''.join(secrets.choice(alphabet) for _ in range(12))
    
    try:
        user.password_hash = hash_password(temp_password)
        db.commit()
        
        log_action(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action="RESET_USER_PASSWORD",
            cible="user",
            description=f"Réinitialisation mot de passe pour: {user.email}",
            ip=request.client.host if request.client else None
        )
        
        return {
            "success": True,
            "message": "Mot de passe réinitialisé avec succès",
            "temporary_password": temp_password,
            "user_id": str(user.id),
            "user_email": user.email,
            "instructions": "Communiquez ce mot de passe temporaire à l'utilisateur. Il devra le changer à sa première connexion."
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur réinitialisation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la réinitialisation: {str(e)}"
        )
# =========================
# ENDPOINTS PAR USER_ID
# =========================

@router.get("/{user_id}", status_code=status.HTTP_200_OK)
def get_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Récupère les détails d'un utilisateur spécifique du tenant."""
    if not is_valid_uuid(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID utilisateur invalide"
        )
    
    user_uuid = UUID(user_id)
    
    # Les admins peuvent voir tous les utilisateurs, les autres seulement leur propre profil
    if current_user.role not in ["admin", "super_admin"] and current_user.id != user_uuid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous ne pouvez voir que votre propre profil"
        )

    if current_user.role in ["admin", "super_admin"]:
        user = db.query(User).filter(
            User.id == user_uuid,
            User.tenant_id == current_user.tenant_id
        ).first()
    else:
        user = db.query(User).filter(User.id == user_uuid).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable"
        )

    return user.to_dict(include_tenant=False, include_pharmacies=True)


@router.put("/{user_id}", status_code=status.HTTP_200_OK)
def update_user(
    user_id: str,
    user_data: UserUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),  # ← Déjà bon, utilise get_current_admin_user
):
    """Met à jour un utilisateur du tenant courant."""
    
    # Vérifier que c'est un UUID valide
    if not is_valid_uuid(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID utilisateur invalide"
        )

    user_uuid = UUID(user_id)
    user = db.query(User).filter(
        User.id == user_uuid,
        User.tenant_id == current_user.tenant_id
    ).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable dans votre pharmacie"
        )

    # Vérifier qu'on ne modifie pas soi-même si on se désactive
    if user.id == current_user.id and user_data.actif == False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas désactiver votre propre compte"
        )

    # Vérifier l'unicité de l'email si modifié
    if user_data.email and user_data.email != user.email:
        existing_user = db.query(User).filter(
            User.email == user_data.email.lower().strip(),
            User.tenant_id == current_user.tenant_id,
            User.id != user_uuid
        ).first()
        
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cet email est déjà utilisé dans votre pharmacie"
            )

    # =================================================================
    # CORRECTION ICI : Permettre aux admins de modifier n'importe quel rôle
    # =================================================================
    
    # Si l'utilisateur courant est admin ou super_admin, il peut modifier n'importe quel rôle
    if current_user.role in ["admin", "super_admin", "superadmin"]:
        # L'admin a tous les droits, pas de restriction sur les rôles
        allowed_roles_for_update = ["admin", "pharmacien", "vendeur", "caissier", "gestionnaire", "comptable", "preparateur", "stockiste"]
        
        if user_data.role and user_data.role != user.role:
            if user_data.role not in allowed_roles_for_update:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Rôle non autorisé. Rôles autorisés pour les admins: {', '.join(allowed_roles_for_update)}"
                )
    else:
        # Pour les non-admins (cas normalement impossible car get_current_admin_user est utilisé)
        # mais on garde la logique par sécurité
        allowed_roles = ["pharmacien", "vendeur", "caissier", "gestionnaire", "comptable", "preparateur", "stockiste"]
        if current_user.role != "super_admin":
            allowed_roles = [role for role in allowed_roles if role != "admin"]
        
        if user_data.role and user_data.role != user.role:
            if user_data.role not in allowed_roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Rôle non autorisé. Rôles autorisés: {', '.join(allowed_roles)}"
                )

    try:
        # Mise à jour des champs
        if user_data.nom_complet is not None:
            user.nom_complet = user_data.nom_complet
        if user_data.email is not None:
            user.email = user_data.email.lower().strip()
        if user_data.telephone is not None:
            user.telephone = user_data.telephone
        if user_data.adresse is not None:
            user.adresse = user_data.adresse
        if user_data.role is not None:
            user.role = user_data.role
            # Mettre à jour les permissions si le rôle change
            user.permissions = get_default_permissions(user_data.role)
        if user_data.actif is not None:
            user.actif = user_data.actif
        if user_data.password is not None:
            user.password_hash = hash_password(user_data.password)
        if user_data.active_pharmacy_id is not None and is_valid_uuid(user_data.active_pharmacy_id):
            user.active_pharmacy_id = UUID(user_data.active_pharmacy_id)
        if user_data.active_branch_id is not None:
            # Un utilisateur reste toujours affecté à une branche valide de son tenant
            if str(user.role).lower() in ("admin", "tenant_admin") and user.branch_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": "admin_branchless",
                        "message": "Un admin n'est pas rattaché à une branche : il choisit la branche sur laquelle il travaille.",
                    },
                )
            try:
                new_branch = resolve_required_branch(db, current_user.tenant_id, user_data.active_branch_id)
                if user.branch_id != new_branch.id:
                    check_branch_user_quota(db, new_branch)
                assign_user_to_branch(db, user, new_branch, role=user.role, created_by=current_user.id)
            except BranchAssignmentError as exc:
                db.rollback()
                raise HTTPException(status_code=exc.status_code, detail=exc.to_detail())

        db.commit()
        db.refresh(user)

        log_action(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action="UPDATE_USER",
            cible="user",
            description=f"Mise à jour utilisateur: {user.email} (rôle: {user.role}) par admin",
            ip=request.client.host if request.client else None
        )

        return {
            "message": "Utilisateur mis à jour avec succès",
            "user": user.to_dict(include_tenant=False, include_pharmacies=True)
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur mise à jour utilisateur {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la mise à jour: {str(e)}"
        )

@router.patch("/{user_id}/toggle", status_code=status.HTTP_200_OK)
def toggle_user(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),  # ← Utiliser admin_user
):
    """Active ou désactive un utilisateur."""
    if not is_valid_uuid(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID utilisateur invalide"
        )

    user_uuid = UUID(user_id)
    user = db.query(User).filter(
        User.id == user_uuid,
        User.tenant_id == current_user.tenant_id
    ).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable dans votre pharmacie"
        )

    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas modifier votre propre statut"
        )

    try:
        user.actif = not user.actif
        db.commit()
        db.refresh(user)

        action = "ACTIVATE_USER" if user.actif else "DEACTIVATE_USER"
        status_text = "activé" if user.actif else "désactivé"
        
        log_action(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=action,
            cible="user",
            description=f"Utilisateur {status_text}: {user.email}",
            ip=request.client.host if request.client else None
        )

        return {
            "message": f"Utilisateur {status_text} avec succès",
            "user": user.to_dict(include_tenant=False, include_pharmacies=True)
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Erreur toggle utilisateur {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la modification du statut: {str(e)}"
        )


@router.post("/{user_id}/reset-password", status_code=status.HTTP_200_OK)
def reset_user_password(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),  # ← Utiliser admin_user
):
    """Réinitialise le mot de passe d'un utilisateur (admin seulement)."""
    if not is_valid_uuid(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID utilisateur invalide"
        )

    user_uuid = UUID(user_id)
    user = db.query(User).filter(
        User.id == user_uuid,
        User.tenant_id == current_user.tenant_id
    ).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable dans votre pharmacie"
        )

    import secrets
    import string
    
    alphabet = string.ascii_letters + string.digits
    temp_password = ''.join(secrets.choice(alphabet) for _ in range(10))
    
    try:
        user.password_hash = hash_password(temp_password)
        db.commit()

        log_action(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action="RESET_USER_PASSWORD",
            cible="user",
            description=f"Réinitialisation mot de passe pour: {user.email}",
            ip=request.client.host if request.client else None
        )

        return {
            "message": "Mot de passe réinitialisé avec succès",
            "temporary_password": temp_password,
            "instructions": "Communiquez ce mot de passe temporaire à l'utilisateur. Il devra le changer à sa première connexion.",
            "user": {
                "id": str(user.id),
                "email": user.email,
                "nom_complet": user.nom_complet
            }
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Erreur réinitialisation mot de passe {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la réinitialisation: {str(e)}"
        )


@router.get("/all", status_code=status.HTTP_200_OK)
def get_all_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),  # ← Utiliser admin_user
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100)
):
    """Liste TOUS les utilisateurs (super_admin seulement)"""
    if current_user.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux super administrateurs"
        )
    
    users = db.query(User).order_by(User.created_at.desc()).offset((page-1)*limit).limit(limit).all()
    return [user.to_dict(include_tenant=True, include_pharmacies=True) for user in users]


# =========================
# STATISTIQUES ET HEALTH
# =========================

@router.get("/statistics/overview", status_code=status.HTTP_200_OK)
def get_user_statistics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),  # ← Utiliser admin_user
):
    """Retourne des statistiques sur les utilisateurs du tenant."""
    from sqlalchemy import func
    
    total = db.query(User).filter(User.tenant_id == current_user.tenant_id).count()
    active = db.query(User).filter(
        User.tenant_id == current_user.tenant_id,
        User.actif == True
    ).count()
    inactive = total - active
    
    roles = db.query(User.role, func.count(User.id)).filter(
        User.tenant_id == current_user.tenant_id
    ).group_by(User.role).all()
    
    role_distribution = {role: count for role, count in roles}
    
    recent_users = db.query(User).filter(
        User.tenant_id == current_user.tenant_id
    ).order_by(User.created_at.desc()).limit(5).all()  # ← created_at
    
    recent_users_data = [{
        "id": str(u.id),
        "nom_complet": u.nom_complet,
        "email": u.email,
        "role": u.role,
        "created_at": u.created_at.isoformat() if u.created_at else None  # ← created_at
    } for u in recent_users]
    
    return {
        "total_users": total,
        "active_users": active,
        "inactive_users": inactive,
        "role_distribution": role_distribution,
        "recent_users": recent_users_data,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/sellers")
async def get_sellers(
    branch_id: Optional[str] = Query(None, description="Filtrer par branche (UUID)"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """Récupère la liste des vendeurs/caissiers."""
    try:
        tenant_id = current_tenant.id if current_tenant else current_user.tenant_id
        
        branch_uuid = None
        if branch_id:
            if not is_valid_uuid(branch_id):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Format d'ID de branche invalide: {branch_id}"
                )
            branch_uuid = UUID(branch_id)
        
        allowed_roles = ["vendeur", "caissier", "gerant", "admin", "super_admin", "superadmin"]
        
        query = db.query(User).filter(
            User.actif == True,
            User.role.in_(allowed_roles)
        )
        
        if tenant_id:
            query = query.filter(User.tenant_id == tenant_id)
        
        if branch_uuid:
            query = query.filter(
                or_(
                    User.active_branch_id == branch_uuid,
                    User.id.in_(
                        db.query(UserBranch.user_id).filter(
                            UserBranch.branch_id == branch_uuid,
                            UserBranch.is_active == True
                        )
                    )
                )
            )
        
        users = query.order_by(User.nom_complet).all()
        
        return {
            "success": True,
            "users": [
                {
                    "id": str(u.id),
                    "email": u.email,
                    "name": u.nom_complet,
                    "role": u.role,
                    "is_active": u.actif
                }
                for u in users
            ],
            "total": len(users)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération vendeurs: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération vendeurs: {str(e)}"
        )


@router.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    """Endpoint de vérification de santé de l'API users."""
    return {
        "status": "healthy",
        "service": "users-api",
        "timestamp": datetime.utcnow().isoformat(),
        "endpoints": [
            "POST /users/ - Créer un utilisateur",
            "GET /users/ - Lister les utilisateurs",
            "GET /users/online-users - Utilisateurs en ligne",
            "GET /users/sessions/stats - Statistiques de sessions",
            "GET /users/statistics/overview - Statistiques",
            "GET /users/{id} - Voir un utilisateur",
            "PUT /users/{id} - Modifier un utilisateur",
            "PATCH /users/{id}/toggle - Activer/désactiver",
            "POST /users/{id}/reset-password - Réinitialiser mot de passe",
            "GET /users/me/profile - Mon profil",
            "POST /users/me/change-password - Changer mon mot de passe",
            "GET /users/sellers - Liste des vendeurs"
        ]
    }
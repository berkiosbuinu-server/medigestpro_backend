# app/api/v1/auth.py
from datetime import datetime, timedelta
import logging
import random
import re
import uuid
from typing import Optional
import os
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from sqlalchemy.orm import Session
from jose import jwt  
import string
import secrets

from app.api.deps import get_current_user, get_current_active_user 
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
    get_password_hash,
    create_token_pair as security_create_token_pair
)
from app.db.session import get_db
from app.models.pharmacy import Pharmacy
from app.models.branch import Branch
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_pharmacy import UserPharmacy
from app.models.payment import Payment
from app.services.notification_service import send_sms, send_whatsapp, send_sms_with_fallback
from app.services.subscription_service import check_subscription_status
from app.services.pharmacy_subscription_service import create_pharmacy_subscription
from prometheus_client import Counter, Histogram
from app.core.config import settings 


login_attempts = Counter('login_attempts_total', 'Total login attempts')
login_duration = Histogram('login_duration_seconds', 'Login duration')

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
logger = logging.getLogger(__name__)

# Constantes
RESET_EXPIRATION_MIN = 60 * 24 * 7
MAX_LOGIN_ATTEMPTS = 5
LOCK_MIN = 15

ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES  # 43200 minutes = 30 jours
REFRESH_TOKEN_EXPIRE_DAYS = settings.REFRESH_TOKEN_EXPIRE_DAYS  # 60 jours

# Cache pour rate limiting
_rate_limiter_cache = {}


# =========================
# MODÈLES DE DONNÉES (Pydantic Schemas)
# =========================
class TenantRegisterSchema(BaseModel):
    email: EmailStr
    password: str
    confirm_password: Optional[str] = None
    nom_complet: str
    nom_pharmacie: str
    ville: str
    telephone: str
    type_pharmacie: str = None
    pays: str = "RDC"
    plan: Optional[str] = None
    plan_name: Optional[str] = None

    @field_validator("password")
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Mot de passe trop court (8 caractères minimum)")
        if len(v.encode('utf-8')) > 72:
            raise ValueError("Mot de passe trop long (72 caractères maximum)")
        if not any(c.isupper() for c in v):
            raise ValueError("Au moins une majuscule requise")
        if not any(c.islower() for c in v):
            raise ValueError("Au moins une minuscule requise")
        if not any(c.isdigit() for c in v):
            raise ValueError("Au moins un chiffre requis")
        return v
    
    @field_validator("telephone")
    def validate_phone(cls, v: str) -> str:
        v = re.sub(r'\D', '', v)
        
        if len(v) < 9:
            raise ValueError("Numéro de téléphone invalide (minimum 9 chiffres)")
        
        if len(v) == 9:
            return v
        elif len(v) == 11 and v.startswith('243'):
            return v
        elif len(v) == 12 and v.startswith('243'):
            return v[1:] if v[0] == '0' else v
        else:
            return v

    @model_validator(mode="after")
    def check_passwords(cls, model):
        if model.confirm_password and model.password != model.confirm_password:
            raise ValueError("Les mots de passe ne correspondent pas")
        return model
    
    @field_validator("pays")
    def validate_country(cls, v: str) -> str:
        """Convertit les noms de pays en codes ISO si nécessaire"""
        if not v:
            return "CD"
        
        country_mapping = {
            "rdc": "CD",
            "congo": "CD",
            "république démocratique du congo": "CD",
            "republique democratique du congo": "CD",
            "congo kinshasa": "CD",
            "congo-kinshasa": "CD",
            "rd congo": "CD",
            "côte d'ivoire": "CI",
            "cote d'ivoire": "CI",
            "cameroon": "CM",
            "cameroun": "CM",
            "senegal": "SN",
            "sénégal": "SN",
            "france": "FR",
            "belgique": "BE",
            "belgium": "BE",
        }
        
        if len(v) == 2 and v.isalpha():
            return v.upper()
        
        normalized = v.lower().strip()
        if normalized in country_mapping:
            return country_mapping[normalized]
        
        if "congo" in normalized or "kinshasa" in normalized:
            return "CD"
        
        raise ValueError(
            f"Pays '{v}' non reconnu. Utilisez le code ISO à 2 lettres "
            f"(ex: CD pour RDC, CI pour Côte d'Ivoire, FR pour France)"
        )


class LoginSchema(BaseModel):
    email: EmailStr
    password: str


class ResetRequestSchema(BaseModel):
    email: EmailStr


class ResetConfirmSchema(BaseModel):
    email: EmailStr
    code: str
    new_password: str


class ExistingPhoneVerificationRequest(BaseModel):
    phone: str
    email: Optional[str] = None


class ExistingPhoneVerificationConfirm(BaseModel):
    phone: str
    code: str
    action: str = "continue"


class SuperAdminSetup(BaseModel):
    email: EmailStr
    password: str
    nom_complet: str
    setup_key: str


class TokenRefreshSchema(BaseModel):
    refresh_token: str


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int


class ChangePlanSchema(BaseModel):
    new_plan: str
    plan_name: Optional[str] = None
    billing_period: Optional[str] = "mensuel"


class CreateSubscriptionPaymentSchema(BaseModel):
    plan: str
    billing_period: str = "monthly"
    payment_method: str
    amount: float
    reference: Optional[str] = None


# =========================
# FONCTIONS UTILITAIRES
# =========================
def format_phone_for_twilio(phone: str) -> str:
    """Formate un numéro de téléphone pour Twilio (format E.164)"""
    if not phone:
        return phone
    
    phone = re.sub(r'\D', '', phone)
    
    if not phone:
        return phone
    
    if phone.startswith('0'):
        phone = phone[1:]
    
    if len(phone) == 9:
        return f"+243{phone}"
    elif len(phone) == 11 and phone.startswith('243'):
        return f"+{phone}"
    elif phone.startswith('+'):
        return phone
    else:
        return f"+{phone}"


def rate_limit_check(key: str, max_attempts: int = 5, window_seconds: int = 300) -> bool:
    """Vérifie si une clé a dépassé la limite de tentatives"""
    now = datetime.utcnow()
    window_start = now - timedelta(seconds=window_seconds)
    
    if key in _rate_limiter_cache:
        _rate_limiter_cache[key] = [
            timestamp for timestamp in _rate_limiter_cache[key]
            if timestamp > window_start
        ]
    
    attempts = _rate_limiter_cache.get(key, [])
    if len(attempts) >= max_attempts:
        logger.warning(f"Rate limit atteint pour {key}")
        return False
    
    attempts.append(now)
    _rate_limiter_cache[key] = attempts[-max_attempts:]
    return True


def generate_otp() -> str:
    """Génère un code OTP à 6 chiffres"""
    return str(random.randint(100000, 999999))


def generate_tenant_code(nom_pharmacie: str) -> str:
    """Génère un code unique pour un tenant"""
    prefix = nom_pharmacie[:3].upper().replace(' ', '')
    if len(prefix) < 3:
        prefix = prefix + 'PH'
    random_suffix = str(random.randint(100, 999))
    return f"{prefix}{random_suffix}"


def generate_slug(nom_pharmacie: str) -> str:
    """Génère un slug à partir du nom de la pharmacie"""
    slug = nom_pharmacie.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'\s+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')


def generate_unique_slug(nom_pharmacie: str, db: Session) -> str:
    """Génère un slug unique à partir du nom de la pharmacie"""
    base_slug = generate_slug(nom_pharmacie)
    slug = base_slug
    counter = 1
    
    while db.query(Tenant).filter(Tenant.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1
        if counter > 100:
            slug = f"{base_slug}-{str(uuid.uuid4())[:8]}"
            break
    
    return slug


def generate_unique_tenant_code(nom_pharmacie: str, db: Session) -> str:
    prefix = nom_pharmacie[:3].upper().replace(' ', '')
    if len(prefix) < 3:
        prefix = prefix + 'PH'
    
    counter = 0  # ✅ Définir counter
    while True:
        random_suffix = str(random.randint(100, 999))
        tenant_code = f"{prefix}{random_suffix}"
        
        if not db.query(Tenant).filter(Tenant.tenant_code == tenant_code).first():
            return tenant_code
        
        counter += 1
        if counter > 10:
            return f"PH{str(uuid.uuid4())[:8].upper()}"

from app.core.branch_scope import is_branchless_admin


def is_subscription_active(db: Session, branch_id: str) -> bool:
    """Vérifie si l'abonnement est actif pour une branche donnée"""
    logger.info(f"🔍 Vérification abonnement pour branch_id: {branch_id}")
    try:
        if not branch_id:
            logger.warning("is_subscription_active appelé avec branch_id None")
            return True
        
        from app.models.branch_subscription import BranchSubscription, SubscriptionStatus
        
        # Chercher l'abonnement de la branche
        subscription = db.query(BranchSubscription).filter(
            BranchSubscription.branch_id == branch_id
        ).first()
        
        if not subscription:
            logger.warning(f"⚠️ Aucun abonnement trouvé pour la branche {branch_id}")
            # Essayer de trouver la branche et créer un abonnement d'essai
            branch = db.query(Branch).filter(Branch.id == branch_id).first()
            if branch and branch.is_main_branch:
                # Créer un abonnement d'essai pour la branche principale
                from app.services.branch_subscription_service import create_trial_subscription
                subscription = create_trial_subscription(db, branch_id)
                if subscription:
                    logger.info(f"✅ Abonnement d'essai créé pour la branche {branch_id}")
                    return True
            return True  # Fallback pour ne pas bloquer
        
        # Vérifier si l'abonnement est actif
        is_active = subscription.is_active()
        
        logger.info(f"📊 Abonnement pour branche {branch_id}: plan={subscription.plan}, actif={is_active}, fin={subscription.end_date}")
        
        return is_active
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de la vérification de l'abonnement: {e}", exc_info=True)
        return True  # Fallback pour ne pas bloquer en cas d'erreur
    
@router.post("/reset-active-branch")
async def reset_active_branch(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Réinitialise la branche active de l'utilisateur vers la branche principale.
    Utile quand l'utilisateur a une branche invalide.
    """
    # Admin sans branche : on reste en mode global (branch none)
    if is_branchless_admin(current_user):
        old_branch_id = current_user.active_branch_id
        current_user.active_branch_id = None
        db.commit()
        return {
            "success": True,
            "message": "Admin global : aucune branche active (branch none)",
            "old_branch_id": str(old_branch_id) if old_branch_id else None,
            "new_branch_id": None,
            "new_branch_name": None
        }

    # Trouver la branche principale du tenant
    main_branch = db.query(Branch).filter(
        Branch.tenant_id == current_user.tenant_id,
        Branch.is_main_branch == True,
        Branch.is_active == True
    ).first()
    
    if not main_branch:
        # Si pas de branche principale, prendre la première branche active
        main_branch = db.query(Branch).filter(
            Branch.tenant_id == current_user.tenant_id,
            Branch.is_active == True
        ).first()
    
    if not main_branch:
        raise HTTPException(
            status_code=404,
            detail="Aucune branche trouvée pour ce compte"
        )
    
    # Mettre à jour l'utilisateur
    old_branch_id = current_user.active_branch_id
    current_user.active_branch_id = main_branch.id
    db.commit()
    
    return {
        "success": True,
        "message": "Branche active réinitialisée avec succès",
        "old_branch_id": str(old_branch_id) if old_branch_id else None,
        "new_branch_id": str(main_branch.id),
        "new_branch_name": main_branch.name
    }

@router.get("/subscription/readonly-status")
def get_readonly_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retourne le statut de l'abonnement de la branche active"""
    
    if not current_user.active_branch_id:
        return {
            "subscription_active": True,
            "read_only_mode": False,
            "subscription_expired": False,
            "message": "Compte sans abonnement requis",
            "allowed_operations": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
        }
    
    subscription_active = is_subscription_active(db, str(current_user.active_branch_id))
    
    branch = db.query(Branch).filter(Branch.id == current_user.active_branch_id).first()
    subscription = None
    
    if branch:
        from app.models.branch_subscription import BranchSubscription
        subscription = db.query(BranchSubscription).filter(
            BranchSubscription.branch_id == branch.id
        ).first()
    
    days_remaining = None
    if subscription:
        days_remaining = subscription.days_remaining()
    
    return {
        "subscription_active": subscription_active,
        "read_only_mode": not subscription_active,
        "subscription_expired": not subscription_active,
        "message": "Mode lecture seule - Renouvelez votre abonnement pour modifier vos données" if not subscription_active else "Abonnement actif",
        "expiry_date": subscription.end_date.isoformat() if subscription and subscription.end_date else None,
        "days_remaining": days_remaining,
        "current_plan": subscription.plan.value if subscription else None,
        "branch_name": branch.name if branch else None,
        "allowed_operations": ["GET", "HEAD", "OPTIONS"] if not subscription_active else ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        "renewal_url": "/api/v1/subscriptions/plans",
        "suggestions": [
            "Consultez les offres d'abonnement",
            "Contactez le support pour plus d'informations",
            "Vos données sont toujours accessibles en lecture seule"
        ] if not subscription_active else []
    }

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Crée un refresh token JWT."""
    to_encode = data.copy()
    
    # Utiliser la valeur de settings si non spécifiée
    if expires_delta is None:
        refresh_expire_days = getattr(settings, 'REFRESH_TOKEN_EXPIRE_DAYS', 60)
        expires_delta = timedelta(days=refresh_expire_days)
    
    expire = datetime.utcnow() + expires_delta
    to_encode.update({
        "exp": expire,
        "type": "refresh"
    })
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_token_pair(user: User, subscription_active: bool, branch_id: Optional[str] = None) -> dict:
    """Génère un couple access_token + refresh_token cohérent."""
    
    access_expire_minutes = getattr(settings, 'ACCESS_TOKEN_EXPIRE_MINUTES', 43200)
    refresh_expire_days = getattr(settings, 'REFRESH_TOKEN_EXPIRE_DAYS', 60)
    
    access_payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "role": user.role,
        "email": user.email,
        "subscription_active": subscription_active,
        "branch_id": branch_id,
        "type": "access"
    }

    # ✅ CORRECTION : Ajouter branch_id au refresh token aussi
    refresh_payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "role": user.role,
        "email": user.email,
        "branch_id": branch_id,  # ← AJOUTER CETTE LIGNE
        "type": "refresh"
    }

    access_token = create_access_token(
        access_payload,
        expires_delta=timedelta(minutes=access_expire_minutes)
    )

    refresh_token = create_refresh_token(
        refresh_payload,
        expires_delta=timedelta(days=refresh_expire_days)
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": access_expire_minutes * 60,
        "refresh_expires_in": refresh_expire_days * 24 * 60 * 60
    }

def decode_token_safely(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except Exception as e:
        logger.error(f"Erreur décodage token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré"
        )


# =========================
# ENDPOINTS D'INSCRIPTION
# =========================
@router.post("/tenants/register", status_code=201)
def register_tenant(data: TenantRegisterSchema, db: Session = Depends(get_db)):
    """Inscription d'un nouveau tenant (pharmacie) avec création automatique de l'abonnement d'essai pour la branche"""
    
    # =========================
    # 1. VÉRIFICATIONS PRÉLIMINAIRES
    # =========================
    
    existing_user = db.query(User).filter(User.email == data.email.lower()).first()
    if existing_user:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "email_already_used",
                "message": "Cet email est déjà utilisé",
                "suggestion": "Utilisez un autre email ou connectez-vous si c'est votre compte"
            }
        )
    
    existing_pharmacy_name = db.query(Tenant).filter(
        Tenant.nom_pharmacie.ilike(data.nom_pharmacie.strip())
    ).first()
    
    if existing_pharmacy_name:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "pharmacy_name_exists",
                "message": f"Le nom de pharmacie '{data.nom_pharmacie}' existe déjà",
                "suggestion": "Ajoutez votre localisation au nom (ex: 'Ma Pharmacie - Kinshasa')"
            }
        )
    
    existing_phone = db.query(Tenant).filter(
        Tenant.telephone_principal == data.telephone
    ).first()
    
    if existing_phone:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "phone_already_used",
                "message": "Ce numéro de téléphone est déjà utilisé",
                "suggestion": "Utilisez un autre numéro ou contactez le support"
            }
        )
    
    if len(data.password.encode('utf-8')) > 72:
        raise HTTPException(
            status_code=400,
            detail="Mot de passe trop long (maximum 72 caractères)"
        )

    # =========================
    # 2. GESTION DU PLAN D'ABONNEMENT
    # =========================
    
    if not data.plan:
        raise HTTPException(400, "Plan d'abonnement requis")

    plan = data.plan
    plan_name = data.plan_name if data.plan_name else plan.capitalize()

    plan_limits = {
        "trial": {"max_users": 5, "max_products": 2000, "max_pharmacies": 1},
        "starter": {"max_users": 5, "max_products": 3000, "max_pharmacies": 1},
        "professional": {"max_users": 20, "max_products": 4000, "max_pharmacies": 3},
        "enterprise": {"max_users": 20, "max_products": 15000, "max_pharmacies": 0},
        "infinite": {"max_users": 0, "max_products": 0, "max_pharmacies": 0}
    }

    if plan not in plan_limits:
        raise HTTPException(
            status_code=400,
            detail=f"Plan invalide. Options: {', '.join(plan_limits.keys())}"
        )

    limits = plan_limits.get(plan)

    # =========================
    # 3. GÉNÉRATION DES IDENTIFIANTS UNIQUES
    # =========================
    
    tenant_code = generate_unique_tenant_code(data.nom_pharmacie, db)
    slug = generate_unique_slug(data.nom_pharmacie, db)
    pharmacy_code = f"{tenant_code}001"

    # =========================
    # 4. CRÉATION DU TENANT
    # =========================
    
    try:
        tenant = Tenant(
            tenant_code=tenant_code,
            slug=slug,
            nom_pharmacie=data.nom_pharmacie,
            nom_commercial=data.nom_pharmacie,
            ville=data.ville,
            pays=data.pays,
            telephone_principal=data.telephone,
            email_admin=data.email.lower(),
            nom_proprietaire=data.nom_complet,
            type_pharmacie=data.type_pharmacie,
            status="active",
            max_users=limits["max_users"],
            max_products=limits["max_products"],
            current_plan=plan,
            max_pharmacies=limits["max_pharmacies"],
            trial_start_date=datetime.utcnow(),
            trial_end_date=datetime.utcnow() + timedelta(days=14),
            config={"plan_name": plan_name}
        )
        db.add(tenant)
        db.flush()

    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création tenant: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "tenant_creation_failed",
                "message": "Erreur lors de la création de votre pharmacie",
                "suggestion": "Réessayez dans quelques instants ou contactez le support"
            }
        )

    # =========================
    # 5. CRÉATION DE LA PHARMACIE PRINCIPALE
    # =========================

    pharmacy = None
    default_branch = None

    try:
        license_number = f"LIC-{tenant_code}-{datetime.utcnow().strftime('%Y%m')}"
        
        pharmacy = Pharmacy(
            tenant_id=tenant.id,
            name=data.nom_pharmacie,
            address=data.ville,
            city=data.ville,
            phone=data.telephone,
            email=data.email.lower(),
            is_active=True,
            is_main=True,
            pharmacy_code=pharmacy_code,
            license_number=license_number,  
            country=data.pays,
            config={
                "require_prescription": True,
                "enable_expiry_alerts": True,
                "low_stock_threshold": 10,
                "enable_barcode": True,
                "tax_rate": 18.0,
                "currency": "CDF",
                "language": "fr",
                "date_format": "dd/MM/yyyy",
                "decimal_precision": 2
            }
        )
        db.add(pharmacy)
        db.flush()
        
        # CRÉATION DE LA BRANCHE PAR DÉFAUT
        from app.models.branch import Branch
        
        branch_code = f"BR-{tenant_code}-001"
        
        default_branch = Branch(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            parent_pharmacy_id=pharmacy.id,
            name="Pharmacie Principale",
            code=branch_code,
            address=data.ville,
            city=data.ville,
            country=data.pays,
            phone=data.telephone,
            email=data.email.lower(),
            is_active=True,
            is_main_branch=True,
            manager_id=None,
            manager_name=data.nom_complet,
            created_by=None,
            config={
                "workingHours": {
                    "enabled": True,
                    "startTime": "08:00",
                    "endTime": "20:00",
                    "timezone": "Africa/Kinshasa",
                    "daysOff": {
                        "monday": True,
                        "tuesday": True,
                        "wednesday": True,
                        "thursday": True,
                        "friday": True,
                        "saturday": True,
                        "sunday": False
                    }
                },
                "marginConfig": {
                    "defaultMargin": 30,
                    "minMargin": 10,
                    "maxMargin": 50
                },
                "automaticPricing": {
                    "enabled": True,
                    "method": "percentage",
                    "value": 25
                },
                "inheritedFromParent": True
            }
        )
        db.add(default_branch)
        db.flush()
        
        logger.info(f"Branche principale créée: {default_branch.name}")

    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création pharmacie/branche: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "pharmacy_creation_failed",
                "message": "Erreur lors de la création de la pharmacie",
                "suggestion": "Contactez le support technique"
            }
        )

    # =========================
    # 6. CRÉATION DE L'UTILISATEUR ADMIN
    # =========================

    try:
        hashed_password = hash_password(data.password)
        
        # Créer l'utilisateur avec active_branch_id
        admin = User(
            tenant_id=tenant.id,
            nom_complet=data.nom_complet,
            email=data.email.lower(),
            password_hash=hashed_password,
            role="admin",
            actif=True,
            telephone=data.telephone,
            login_attempts=0,
            active_pharmacy_id=pharmacy.id,
            # Admin global : branch none (l'abonnement reste porté par la branche principale)
            branch_id=None,
            active_branch_id=None
        )
        db.add(admin)
        db.flush()
        
        # Créer l'association user_pharmacy
        association = UserPharmacy(
            user_id=admin.id,
            pharmacy_id=pharmacy.id,
            is_primary=True,
            role_in_pharmacy="admin",
            can_manage=True
        )
        db.add(association)
        db.flush()
        
        # Mettre à jour la branche avec le manager
        default_branch.manager_id = admin.id
        default_branch.created_by = admin.id
        db.add(default_branch)
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création admin/association: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "admin_creation_failed",
                "message": "Erreur lors de la création du compte administrateur",
                "suggestion": "Vérifiez vos informations et réessayez"
            }
        )

    # =========================
    # 7. CRÉATION DE L'ABONNEMENT DE LA BRANCHE
    # =========================

    from app.models.branch_subscription import BranchSubscription, SubscriptionPlan, SubscriptionStatus

    branch_subscription = None
    try:
        # Créer un abonnement d'essai pour la branche principale
        trial_end_date = datetime.utcnow() + timedelta(days=14)
        
        branch_subscription = BranchSubscription(
            id=uuid.uuid4(),
            branch_id=default_branch.id,
            tenant_id=tenant.id,
            pharmacy_id=pharmacy.id,
            plan=SubscriptionPlan.TRIAL.value,
            plan_name="Essai gratuit",
            start_date=datetime.utcnow(),
            end_date=trial_end_date,
            trial_end_date=trial_end_date,
            status=SubscriptionStatus.TRIAL.value,
            billing_cycle="monthly",
            price=0.0,
            currency="EUR",
            auto_renew=False,
            max_products=limits["max_products"],
            max_users=limits["max_users"],
            max_storage_mb=100
        )
        db.add(branch_subscription)
        db.flush()
        
        logger.info(f"✅ Abonnement d'essai créé pour la branche {default_branch.id}")
        
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Erreur création abonnement branche: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "subscription_creation_failed",
                "message": "Erreur lors de la création de l'abonnement d'essai",
                "suggestion": "Contactez le support technique"
            }
        )
    
    # =========================
    # 8. VALIDATION FINALE
    # =========================
    
    try:
        db.commit()
        logger.info(f"Tenant créé avec succès: {tenant_code} - Admin: {admin.email} - Branche: {default_branch.id}")
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur commit final: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "database_error",
                "message": "Erreur lors de l'enregistrement final",
                "suggestion": "Veuillez réessayer dans quelques instants"
            }
        )

    # =========================
    # 9. RÉPONSE AU CLIENT
    # =========================
    
    response = {
        "status": "success",
        "message": "Pharmacie créée avec succès. Vous pouvez maintenant vous connecter.",
        "data": {
            "tenant_id": str(tenant.id),
            "user_id": str(admin.id),
            "tenant_code": tenant_code,
            "pharmacy_id": str(pharmacy.id),
            "branch_id": str(default_branch.id),
            "plan": "trial",
            "plan_name": "Essai gratuit",
            "trial_end_date": branch_subscription.end_date.isoformat(),
            "trial_days": 14,
            "subscription": {
                "id": str(branch_subscription.id),
                "branch_id": str(default_branch.id),
                "status": branch_subscription.status,
                "days_remaining": branch_subscription.days_remaining(),
                "is_trial": branch_subscription.is_trial(),
                "mode": "FULL" if branch_subscription.is_active() else "READ_ONLY"
            },
            "limits": {
                "max_users": limits["max_users"],
                "max_products": limits["max_products"],
                "max_pharmacies": limits["max_pharmacies"]
            },
            "created_at": datetime.utcnow().isoformat()
        },
        "next_steps": {
            "login": {
                "message": "Connectez-vous pour accéder à votre compte",
                "action": "POST /api/v1/auth/login",
                "required_data": {
                    "email": data.email,
                    "password": "votre_mot_de_passe"
                }
            },
            "welcome_sms": {
                "message": "Un SMS de bienvenue sera envoyé lors de votre première connexion",
                "note": "Le SMS sera envoyé automatiquement après votre première connexion réussie"
            },
            "trial_info": f"Vous bénéficiez de 14 jours d'essai gratuit jusqu'au {branch_subscription.end_date.strftime('%d/%m/%Y')}",
            "dashboard_access": "Connectez-vous pour accéder à votre tableau de bord"
        },
        "recommendations": [
            "Sauvegardez vos identifiants dans un endroit sécurisé",
            "Connectez-vous pour accéder à votre tableau de bord",
            "Complétez le profil de votre pharmacie après connexion",
            "Explorez les fonctionnalités pendant votre période d'essai"
        ]
    }
    
    return response

@router.post("/verify-subscription")
def verify_subscription(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Vérifie si l'abonnement est actif (basé sur la branche active).
    """
    if not current_user.tenant_id:
        return {
            "subscription_active": True,
            "message": "Compte sans abonnement requis"
        }
    
    # ✅ CORRECTION : Utiliser active_branch_id au lieu de tenant_id
    branch_id = current_user.active_branch_id
    if not branch_id:
        # Fallback: trouver la branche principale
        main_branch = db.query(Branch).filter(
            Branch.tenant_id == current_user.tenant_id,
            Branch.is_main_branch == True
        ).first()
        if main_branch:
            branch_id = main_branch.id
    
    if not branch_id:
        return {
            "subscription_active": True,
            "message": "Aucune branche trouvée"
        }
    
    subscription_active = is_subscription_active(db, str(branch_id))
    
    if not subscription_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "subscription_expired",
                "message": "L'abonnement de votre succursale a expiré",
                "requires_relogin": True,
                "action": "Veuillez renouveler l'abonnement de votre succursale"
            }
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    
    return {
        "subscription_active": True,
        "current_plan": tenant.current_plan if tenant else None,
        "trial_end_date": tenant.trial_end_date.isoformat() if tenant and tenant.trial_end_date else None
    }
# =========================
# ENDPOINTS DE CONNEXION (SANS OTP)
# =========================
@router.post("/login")
def login(data: LoginSchema, db: Session = Depends(get_db)):
    """Connexion utilisateur avec génération access_token + refresh_token."""
    email = data.email.lower().strip()
    logger.info(f"Tentative de login pour: {email}")

    user = db.query(User).filter(User.email == email).first()

    if not user:
        logger.warning(f"Utilisateur non trouvé: {email}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants invalides"
        )

    # Vérification verrouillage
    if user.locked_until and user.locked_until > datetime.utcnow():
        remaining = int((user.locked_until - datetime.utcnow()).total_seconds() / 60)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Compte temporairement bloqué. Réessayez dans {remaining} minutes."
        )

    # Vérification mot de passe
    if not verify_password(data.password, user.password_hash):
        user.login_attempts = (user.login_attempts or 0) + 1

        if user.login_attempts >= MAX_LOGIN_ATTEMPTS:
            user.locked_until = datetime.utcnow() + timedelta(minutes=LOCK_MIN)
            user.login_attempts = 0
            logger.warning(f"Compte verrouillé après trop d'échecs: {email}")

        db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants invalides"
        )

    # Vérifier si c'est la première connexion
    is_first_login = user.last_login is None
    
    # Envoi SMS de bienvenue si première connexion
    if is_first_login and user.telephone:
        try:
            formatted_phone = format_phone_for_twilio(user.telephone)
            welcome_message = (
                f"Bienvenue sur MEDIGEST ! 🎉\n\n"
                f"Votre compte a été créé avec succès.\n"
                f"Vous pouvez maintenant accéder à votre tableau de bord.\n\n"
                f"Email: {user.email}\n"
                f"Si vous avez des questions, contactez notre support."
            )
            send_sms_with_fallback(formatted_phone, welcome_message)
            logger.info(f"SMS de bienvenue envoyé à {email}")
        except Exception as e:
            logger.error(f"Erreur envoi SMS de bienvenue à {email}: {e}")

    # ============================================================
    # RÉCUPÉRATION DU TENANT ET DES BRANCHES (MODIFIÉ)
    # ============================================================
    tenant = None
    tenant_data = None
    branches = []
    main_branch = None
    subscription_active = False
    
    # Variables pour la branche par défaut
    default_branch_id = None
    default_branch_name = None

    branchless_admin = is_branchless_admin(user)

    if user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()

        if tenant:
            # Récupérer TOUTES les branches du tenant (au lieu des pharmacies)
            branches = db.query(Branch).filter(
                Branch.tenant_id == tenant.id,
                Branch.is_active == True
            ).order_by(Branch.is_main_branch.desc(), Branch.name).all()

            # Trouver la branche principale
            main_branch = next(
                (branch for branch in branches if branch.is_main_branch),
                branches[0] if branches else None
            )
            
            if branchless_admin:
                # Admin global (branch none) : pas de branche par défaut, pas d'abonnement
                main_branch = None
                subscription_active = True
            else:
                # Récupérer la branche principale par défaut si l'utilisateur n'en a pas
                if main_branch and not user.active_branch_id:
                    default_branch_id = str(main_branch.id)
                    default_branch_name = main_branch.name

                # Vérifier l'abonnement pour la branche principale
                if main_branch:
                    subscription_active = is_subscription_active(db, str(main_branch.id))
                else:
                    subscription_active = False

            # Données du tenant (inchangées)
            tenant_data = {
                "id": str(tenant.id),
                "tenant_code": tenant.tenant_code,
                "nom_pharmacie": tenant.nom_pharmacie,
                "nom_commercial": tenant.nom_commercial or tenant.nom_pharmacie,
                "ville": tenant.ville,
                "pays": tenant.pays,
                "email_admin": tenant.email_admin,
                "status": tenant.status,
                "current_plan": tenant.current_plan,
                "plan_name": tenant.config.get("plan_name") if tenant.config else tenant.current_plan,
                "max_users": tenant.max_users,
                "max_products": tenant.max_products,
                "max_pharmacies": tenant.max_pharmacies,
                "trial_end_date": tenant.trial_end_date.isoformat() if tenant.trial_end_date else None
            }
        else:
            logger.warning(f"Tenant introuvable pour l'utilisateur {email}: {user.tenant_id}")
            subscription_active = False
    else:
        logger.info(f"Utilisateur sans tenant (rôle: {user.role})")
        subscription_active = True

    # Réinitialiser les tentatives de login
    user.login_attempts = 0
    user.locked_until = None
    user.last_login = datetime.utcnow()
    db.commit()

    # Générer les tokens avec branch_id au lieu de pharmacy_id
    token_pair = create_token_pair(
        user=user,
        subscription_active=subscription_active,
        branch_id=str(main_branch.id) if main_branch else None
    )

    # Déterminer l'ID de branche actif
    if branchless_admin:
        active_branch_id = None
    else:
        active_branch_id = str(user.active_branch_id) if user.active_branch_id else default_branch_id
    active_branch_name = None
    
    if active_branch_id:
        branch = db.query(Branch).filter(Branch.id == active_branch_id).first()
        active_branch_name = branch.name if branch else default_branch_name
    elif branchless_admin:
        active_branch_name = "Toutes les branches"
    else:
        active_branch_name = default_branch_name or "Succursale principale"

    # Récupérer la pharmacie associée à la branche principale (pour compatibilité frontend)
    main_pharmacy = None
    if main_branch:
        main_pharmacy = db.query(Pharmacy).filter(Pharmacy.id == main_branch.parent_pharmacy_id).first()
    
    # Vérifier si la branche active est valide
    if user.active_branch_id and not branchless_admin:
        branch_exists = db.query(Branch).filter(Branch.id == user.active_branch_id).first()
        if not branch_exists:
            logger.warning(f"Branche active invalide pour {email}: {user.active_branch_id}, réinitialisation...")
            # Réinitialiser vers la branche principale
            main_branch = db.query(Branch).filter(
                Branch.tenant_id == user.tenant_id,
                Branch.is_main_branch == True,
                Branch.is_active == True
            ).first()
            
            if main_branch:
                user.active_branch_id = main_branch.id
                db.commit()
                logger.info(f"Branche active réinitialisée pour {email} vers {main_branch.name}")
                active_branch_name = main_branch.name
                active_branch_id = str(main_branch.id)

    # Accès par branche (abonnement actif => écriture, sinon lecture seule).
    # Surtout utile à l'admin sans branche, qui choisit la branche sur laquelle il travaille.
    branch_access_map = {}
    if branches:
        from app.models.branch_subscription import BranchSubscription
        _subs = db.query(BranchSubscription).filter(
            BranchSubscription.branch_id.in_([b.id for b in branches])
        ).all()
        _subs_by_branch = {str(sub.branch_id): sub for sub in _subs}
        for _b in branches:
            _sub = _subs_by_branch.get(str(_b.id))
            _active = bool(_sub and _sub.is_active())
            branch_access_map[str(_b.id)] = {
                "subscription_active": _active,
                "has_subscription": _sub is not None,
                "access_mode": "full" if _active else "read_only",
                "subscription_end_date": _sub.end_date.isoformat() if _sub and _sub.end_date else None,
            }

    # ============================================================
    # CONSTRUCTION DE LA RÉPONSE (MODIFIÉE)
    # ============================================================
    response_data = {
        **token_pair,
        "subscription_active": subscription_active,
        "read_only_mode": not subscription_active,
        "subscription_expired": not subscription_active, 
        "is_first_login": is_first_login,
        "branch_mode": "global" if branchless_admin else "branch",
        "branch_write_policy": (
            "active_subscription_only" if branchless_admin else "own_branch_subscription"
        ),
        "user": {
            "id": str(user.id),
            "email": user.email,
            "nom_complet": user.nom_complet,
            "role": user.role,
            "tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "actif": user.actif,
            "telephone": user.telephone,
            "active_branch_id": active_branch_id,
            "active_branch_name": active_branch_name,
            "active_pharmacy_id": str(user.active_pharmacy_id) if user.active_pharmacy_id else None
        },
        "tenant": tenant_data,
        # LISTE DES BRANCHES (remplace pharmacies)
        "branches": [
            {
                "id": str(branch.id),
                "name": branch.name,
                "code": branch.code,
                "address": branch.address,
                "city": branch.city,
                "phone": branch.phone,
                "email": branch.email,
                "is_active": branch.is_active,
                "is_main_branch": branch.is_main_branch,
                "parent_pharmacy_id": str(branch.parent_pharmacy_id) if branch.parent_pharmacy_id else None,
                "created_at": branch.created_at.isoformat() if branch.created_at else None,
                **branch_access_map.get(str(branch.id), {
                    "subscription_active": False,
                    "has_subscription": False,
                    "access_mode": "read_only",
                    "subscription_end_date": None,
                }),
            }
            for branch in branches
        ],
        # Pharmacie principale (pour compatibilité)
        "current_pharmacy": {
            "id": str(main_pharmacy.id) if main_pharmacy else None,
            "name": main_pharmacy.name if main_pharmacy else None,
            "address": main_pharmacy.address if main_pharmacy else None,
            "city": main_pharmacy.city if main_pharmacy else None,
            "phone": main_pharmacy.phone if main_pharmacy else None,
            "email": main_pharmacy.email if main_pharmacy else None,
            "is_main": main_pharmacy.is_main if main_pharmacy else None,
            "pharmacy_code": main_pharmacy.pharmacy_code if main_pharmacy else None
        } if main_pharmacy else None,
        # Branche courante (NOUVEAU)
        "current_branch": {
            "id": str(main_branch.id) if main_branch else None,
            "name": main_branch.name if main_branch else None,
            "code": main_branch.code if main_branch else None,
            "address": main_branch.address if main_branch else None,
            "city": main_branch.city if main_branch else None,
            "phone": main_branch.phone if main_branch else None,
            "email": main_branch.email if main_branch else None,
            "is_main_branch": main_branch.is_main_branch if main_branch else None,
            "parent_pharmacy_id": str(main_branch.parent_pharmacy_id) if main_branch and main_branch.parent_pharmacy_id else None
        } if main_branch else None
    }

    # Ajouter les informations de renouvellement si abonnement inactif
    if not subscription_active and main_branch:
        from app.models.branch_subscription import BranchSubscription
        
        subscription = db.query(BranchSubscription).filter(
            BranchSubscription.branch_id == main_branch.id
        ).first()
        
        if subscription:
            response_data["renewal_info"] = {
                "required": True,
                "message": "Votre abonnement a expiré. Veuillez le renouveler.",
                "url": "/api/v1/subscriptions/plans",
                "expiry_date": subscription.end_date.isoformat() if subscription.end_date else None,
                "days_overdue": abs(subscription.days_remaining()) if not subscription.is_active() else 0
            }
        elif tenant and tenant.trial_end_date:
            # Fallback pour compatibilité
            response_data["renewal_info"] = {
                "required": True,
                "message": "Votre période d'essai a expiré. Veuillez souscrire un abonnement.",
                "url": "/api/v1/subscriptions/plans",
                "expiry_date": tenant.trial_end_date.isoformat() if tenant.trial_end_date else None,
                "days_overdue": abs((tenant.trial_end_date - datetime.utcnow()).days) if tenant.trial_end_date else 0
            }

    logger.info(f"Login réussi pour: {email} (branche: {active_branch_name})")
    return response_data

# =========================
# ENDPOINTS RÉINITIALISATION MOT DE PASSE
# =========================
@router.post("/password/reset/request")
def request_reset(data: ResetRequestSchema, db: Session = Depends(get_db)):
    """Demande de réinitialisation de mot de passe - envoie un code par SMS"""
    user = db.query(User).filter(User.email == data.email.lower()).first()
    if not user:
        return {"message": "Si le compte existe, un code sera envoyé"}

    code = generate_otp()
    user.reset_code = code
    user.reset_expires = datetime.utcnow() + timedelta(minutes=RESET_EXPIRATION_MIN)
    db.commit()

    sms_sent = False
    try:
        if user.telephone:
            formatted_phone = format_phone_for_twilio(user.telephone)
            send_sms(formatted_phone, f"Code réinitialisation: {code}")
            send_whatsapp(formatted_phone, f"Code réinitialisation: {code}")
            sms_sent = True
    except Exception as e:
        logger.error(f"Erreur envoi SMS/WhatsApp: {e}")

    return {"message": "Code envoyé", "sms_sent": sms_sent}


@router.post("/password/reset/confirm")
def confirm_reset(data: ResetConfirmSchema, db: Session = Depends(get_db)):
    """Confirmation de réinitialisation de mot de passe"""
    user = db.query(User).filter(User.email == data.email.lower()).first()

    if not user or user.reset_code != data.code:
        raise HTTPException(400, "Code invalide")

    if user.reset_expires < datetime.utcnow():
        raise HTTPException(400, "Code expiré")

    if len(data.new_password.encode('utf-8')) > 72:
        raise HTTPException(400, "Mot de passe trop long (72 caractères max)")

    user.password_hash = hash_password(data.new_password)
    user.reset_code = None
    user.reset_expires = None
    user.login_attempts = 0
    user.locked_until = None
    db.commit()

    return {"message": "Mot de passe modifié"}


# =========================
# ENDPOINTS DE VÉRIFICATION DE DISPONIBILITÉ
# =========================
@router.post("/tenants/check-availability")
def check_availability(
    email: Optional[str] = None,
    pharmacy_name: Optional[str] = None,
    phone: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Vérifie la disponibilité des identifiants avant inscription"""
    results = {
        "available": True,
        "checks": [],
        "suggestions": []
    }
    
    if email:
        existing_user = db.query(User).filter(User.email == email.lower()).first()
        if existing_user:
            results["available"] = False
            results["checks"].append({
                "field": "email",
                "available": False,
                "message": "Email déjà utilisé"
            })
            results["suggestions"].append({
                "field": "email",
                "message": "Utilisez un autre email ou connectez-vous"
            })
        else:
            results["checks"].append({
                "field": "email",
                "available": True,
                "message": "Email disponible"
            })
    
    if pharmacy_name:
        existing_pharmacy = db.query(Tenant).filter(
            Tenant.nom_pharmacie.ilike(pharmacy_name.strip())
        ).first()
        
        if existing_pharmacy:
            results["available"] = False
            results["checks"].append({
                "field": "pharmacy_name",
                "available": False,
                "message": f"Nom '{pharmacy_name}' existe déjà"
            })
            
            suggestions = [
                f"{pharmacy_name} - {existing_pharmacy.ville}",
                f"{pharmacy_name} Centre",
                f"{pharmacy_name} Principal",
                f"Pharmacie {pharmacy_name}"
            ]
            
            results["suggestions"].append({
                "field": "pharmacy_name",
                "message": "Nom non disponible",
                "alternatives": suggestions[:3]
            })
        else:
            results["checks"].append({
                "field": "pharmacy_name",
                "available": True,
                "message": "Nom disponible"
            })
    
    if phone:
        phone_clean = re.sub(r'\D', '', phone)
        existing_phone = db.query(Tenant).filter(Tenant.telephone_principal == phone_clean).first()
        if existing_phone:
            results["available"] = False
            results["checks"].append({
                "field": "phone",
                "available": False,
                "message": "Téléphone déjà utilisé"
            })
        else:
            results["checks"].append({
                "field": "phone",
                "available": True,
                "message": "Téléphone disponible"
            })
    
    return results


@router.post("/check-phone-exists")
async def check_phone_exists(
    request: Request,  # Ajouter pour récupérer les paramètres
    db: Session = Depends(get_db)
):
    """Vérifie si un numéro existe déjà"""
    
    # Récupérer le paramètre phone depuis le body ou query params
    body = await request.json() if request.method == "POST" else {}
    phone = body.get("phone") or request.query_params.get("phone")
    
    if not phone:
        raise HTTPException(
            status_code=400,
            detail="Le paramètre phone est requis"
        )
    
    phone_clean = re.sub(r'\D', '', phone)
    
    tenant = db.query(Tenant).filter(
        Tenant.telephone_principal == phone_clean
    ).first()
    
    if not tenant:
        user = db.query(User).filter(User.telephone == phone_clean).first()
        if user:
            tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    
    if not tenant:
        return {"exists": False, "message": "Numéro disponible"}
    
    email = tenant.email_admin if tenant else None
    if email:
        email_parts = email.split('@')
        if len(email_parts) == 2:
            masked_email = f"{email_parts[0][:3]}***@{email_parts[1]}"
        else:
            masked_email = "utilisateur@..."
    else:
        masked_email = None
    
    return {
        "exists": True,
        "is_active": tenant.status == "active" if tenant else False,
        "email_hint": masked_email,
        "message": f"Ce numéro est associé à un compte existant ({masked_email})",
        "suggestions": [
            "Si c'est votre compte, connectez-vous avec votre email",
            "Si ce n'est pas votre compte, utilisez un autre numéro"
        ]
    }

# =========================
# ENDPOINTS UTILISATEUR
# =========================
@router.get("/me")
def get_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    tenant = None
    pharmacies = []
    current_pharmacy = None
    subscription_active = True

    if current_user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()

        pharmacies = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == current_user.tenant_id,
            Pharmacy.is_active == True
        ).order_by(Pharmacy.is_main.desc(), Pharmacy.name).all()

        current_pharmacy = next((p for p in pharmacies if p.is_main), pharmacies[0] if pharmacies else None)
        
        # ✅ CORRECTION : Utiliser active_branch_id
        branch_id = current_user.active_branch_id
        if branch_id:
            subscription_active = is_subscription_active(db, str(branch_id))
        else:
            subscription_active = True

    return {
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "nom_complet": current_user.nom_complet,
            "role": current_user.role,
            "tenant_id": str(current_user.tenant_id) if current_user.tenant_id else None,
            "actif": current_user.actif,
            "telephone": current_user.telephone
        },
        "tenant": {
            "id": str(tenant.id),
            "tenant_code": tenant.tenant_code,
            "nom_pharmacie": tenant.nom_pharmacie,
            "status": tenant.status
        } if tenant else None,
        "subscription_active": subscription_active,
        "current_pharmacy": {
            "id": str(current_pharmacy.id),
            "name": current_pharmacy.name,
            "pharmacy_code": current_pharmacy.pharmacy_code
        } if current_pharmacy else None
    }

@router.get("/tenants/me")
def get_current_tenant_info(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupère les informations du tenant de l'utilisateur connecté"""
    
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    active_pharmacies_count = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant.id,
        Pharmacy.is_active == True
    ).count()
    
    main_pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant.id,
        Pharmacy.is_main == True,
        Pharmacy.is_active == True
    ).first()
    
    response = {
        "tenant": {
            "id": str(tenant.id),
            "tenant_code": tenant.tenant_code,
            "slug": tenant.slug,
            "nom_pharmacie": tenant.nom_pharmacie,
            "nom_commercial": tenant.nom_commercial,
            "ville": tenant.ville,
            "pays": tenant.pays,
            "email_admin": tenant.email_admin,
            "nom_proprietaire": tenant.nom_proprietaire,
            "telephone_principal": tenant.telephone_principal,
            "telephone_secondaire": tenant.telephone_secondaire,
            "type_pharmacie": tenant.type_pharmacie,
            "status": tenant.status,
            "current_plan": tenant.current_plan,
            "plan_name": tenant.config.get("plan_name") if tenant.config else tenant.current_plan.capitalize(),
            "max_users": tenant.max_users,
            "max_products": tenant.max_products,
            "max_pharmacies": tenant.max_pharmacies,
            "active_pharmacies": active_pharmacies_count,
            "trial_start_date": tenant.trial_start_date.isoformat() if tenant.trial_start_date else None,
            "trial_end_date": tenant.trial_end_date.isoformat() if tenant.trial_end_date else None,
            "activated_at": tenant.activated_at.isoformat() if tenant.activated_at else None,
            "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
            "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
            "config": tenant.config or {}
        }
    }
    
    if main_pharmacy:
        response["main_pharmacy"] = {
            "id": str(main_pharmacy.id),
            "name": main_pharmacy.name,
            "address": main_pharmacy.address,
            "city": main_pharmacy.city,
            "phone": main_pharmacy.phone,
            "email": main_pharmacy.email,
            "pharmacy_code": main_pharmacy.pharmacy_code,
            "is_main": main_pharmacy.is_main,
            "is_active": main_pharmacy.is_active
        }
    
    return response


# =========================
# ENDPOINTS ABONNEMENT
# =========================
@router.get("/subscription/status")
def get_subscription_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Récupère le statut de l'abonnement de la branche active"""
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    # ✅ CORRECTION : Utiliser active_branch_id
    branch_id = current_user.active_branch_id
    if not branch_id and not is_branchless_admin(current_user):
        main_branch = db.query(Branch).filter(
            Branch.tenant_id == current_user.tenant_id,
            Branch.is_main_branch == True
        ).first()
        if main_branch:
            branch_id = main_branch.id
    
    # Admin sans branche : jamais soumis à un abonnement
    subscription_active = True if is_branchless_admin(current_user) else (
        is_subscription_active(db, str(branch_id)) if branch_id else True
    )
    
    # Récupérer l'abonnement de la branche
    branch_subscription = None
    if branch_id:
        from app.models.branch_subscription import BranchSubscription
        branch_subscription = db.query(BranchSubscription).filter(
            BranchSubscription.branch_id == branch_id
        ).first()
    
    last_payment = db.query(Payment).filter(
        Payment.tenant_id == current_user.tenant_id,
        Payment.status == "success"
    ).order_by(Payment.paid_at.desc()).first()
    
    days_remaining = None
    is_expired = False
    is_near_expiry = False
    
    if branch_subscription and branch_subscription.end_date:
        now = datetime.utcnow()
        days_remaining = (branch_subscription.end_date - now).days
        
        if days_remaining < 0:
            is_expired = True
        elif days_remaining <= 3:
            is_near_expiry = True
    
    return {
        "tenant_id": str(tenant.id),
        "tenant_code": tenant.tenant_code,
        "tenant_status": tenant.status,
        "branch_id": str(branch_id) if branch_id else None,
        "current_plan": branch_subscription.plan if branch_subscription else tenant.current_plan,
        "plan_name": branch_subscription.plan_name if branch_subscription else (tenant.config.get("plan_name") if tenant.config else tenant.current_plan.capitalize()),
        "subscription_active": subscription_active,
        "trial_end_date": branch_subscription.end_date.isoformat() if branch_subscription and branch_subscription.end_date else None,
        "days_remaining": days_remaining,
        "is_expired": is_expired,
        "is_near_expiry": is_near_expiry,
        "limits": {
            "max_users": branch_subscription.max_users if branch_subscription else tenant.max_users,
            "max_products": branch_subscription.max_products if branch_subscription else tenant.max_products,
            "max_pharmacies": tenant.max_pharmacies
        },
        "last_payment": {
            "id": str(last_payment.id) if last_payment else None,
            "amount": last_payment.amount if last_payment else None,
            "payment_method": last_payment.payment_method if last_payment else None,
            "paid_at": last_payment.paid_at.isoformat() if last_payment and last_payment.paid_at else None
        } if last_payment else None
    }

@router.post("/subscription/payment")
def create_subscription_payment(
    data: CreateSubscriptionPaymentSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Crée un paiement pour un abonnement"""
    if current_user.role != "admin":
        raise HTTPException(403, "Seuls les administrateurs peuvent effectuer des paiements")
    
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    valid_plans = ["starter", "professional", "enterprise"]
    if data.plan not in valid_plans:
        raise HTTPException(400, f"Plan invalide. Options: {', '.join(valid_plans)}")
    
    valid_methods = ["cash", "mobile_money", "visa", "bank_transfer"]
    if data.payment_method not in valid_methods:
        raise HTTPException(400, f"Méthode de paiement invalide. Options: {', '.join(valid_methods)}")
    
    period_start = datetime.utcnow()
    if data.billing_period == "monthly":
        period_end = period_start + timedelta(days=30)
    else:
        period_end = period_start + timedelta(days=365)
    
    try:
        payment = Payment(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            amount=data.amount,
            payment_method=data.payment_method,
            reference=data.reference,
            status="success",
            paid_at=datetime.utcnow()
        )
        db.add(payment)
        
        old_plan = tenant.current_plan
        tenant.current_plan = data.plan
        
        plan_limits = {
            "trial": {"max_users": 5, "max_products": 2000, "max_pharmacies": 1},
            "starter": {"max_users": 5, "max_products": 1500, "max_pharmacies": 1},
            "professional": {"max_users": 20, "max_products": 3000, "max_pharmacies": 3},
            "enterprise": {"max_users": 20, "max_products": 10000, "max_pharmacies": 0},
            "infinite": {"max_users": 0, "max_products": 0, "max_pharmacies": 0}
        }
        
        limits = plan_limits.get(data.plan)
        if limits:
            tenant.max_users = limits["max_users"]
            tenant.max_products = limits["max_products"]
            tenant.max_pharmacies = limits["max_pharmacies"]
        
        if not tenant.config:
            tenant.config = {}
        
        tenant.config["subscription"] = {
            "plan": data.plan,
            "plan_name": data.plan.capitalize(),
            "billing_period": data.billing_period,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "last_payment_id": str(payment.id),
            "last_payment_date": payment.paid_at.isoformat()
        }
        
        if tenant.status == "trial":
            tenant.status = "active"
            tenant.activated_at = datetime.utcnow()
        
        db.commit()
        
        logger.info(f"Paiement abonnement réussi pour {tenant.tenant_code}: {old_plan} -> {data.plan}")
        
        return {
            "message": "Paiement réussi. Votre abonnement est maintenant actif.",
            "payment": {
                "id": str(payment.id),
                "amount": payment.amount,
                "payment_method": payment.payment_method,
                "status": payment.status,
                "reference": payment.reference,
                "paid_at": payment.paid_at.isoformat()
            },
            "subscription": {
                "plan": data.plan,
                "billing_period": data.billing_period,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "tenant_status": tenant.status,
                "limits": {
                    "max_users": tenant.max_users,
                    "max_products": tenant.max_products,
                    "max_pharmacies": tenant.max_pharmacies
                }
            }
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors du paiement: {e}", exc_info=True)
        raise HTTPException(500, "Erreur lors du traitement du paiement")


# =========================
# ENDPOINTS SUPER ADMIN
# =========================
@router.post("/super-admin/verify-key")
def verify_super_admin_key(data: dict, db: Session = Depends(get_db)):
    """Vérifie la clé d'accès super administrateur"""
    key = data.get("key", "")
    
    logger.info(f"Vérification clé super admin - Longueur: {len(key)}")
    
    master_key = os.getenv("SUPER_ADMIN_ACCESS_KEY")
    create_key = os.getenv("SUPER_ADMIN_CREATE_KEY")
    
    if master_key and key == master_key:
        logger.info("✅ Clé maître valide")
        return {"valid": True, "access_type": "full"}
    
    if create_key and key == create_key:
        logger.info("✅ Clé de création valide")
        return {"valid": True, "access_type": "setup"}
    
    logger.warning(f"❌ Clé invalide: {key[:5]}...")
    return {"valid": False, "message": "Clé d'accès invalide"}

# =========================
# ENDPOINTS SUPER ADMIN LOGIN (TOKEN 100 ANS)
# =========================
@router.post("/super-admin/login")
def super_admin_login(data: dict, db: Session = Depends(get_db)):
    """Login super admin avec clé spéciale - Génère un token valable 100 ans"""
    key = data.get("key", "")
    
    if not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Clé d'accès requise"
        )
    
    # Vérifier la clé depuis les variables d'environnement
    master_key = os.getenv("SUPER_ADMIN_ACCESS_KEY")
    create_key = os.getenv("SUPER_ADMIN_CREATE_KEY")
    
    is_valid = False
    access_type = None
    
    if master_key and key == master_key:
        is_valid = True
        access_type = "full"
        logger.info(f"✅ Authentification super admin avec clé maître")
    
    elif create_key and key == create_key:
        is_valid = True
        access_type = "setup"
        logger.info(f"✅ Authentification super admin avec clé de création")
    
    if not is_valid:
        logger.warning(f"❌ Tentative d'authentification super admin avec clé invalide")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé d'accès invalide"
        )
    
    # Trouver ou créer un super_admin
    super_admin = db.query(User).filter(
        User.role == "super_admin",
        User.actif == True
    ).first()
    
    is_newly_created = False
    permanent_password = None
    
    if not super_admin:
        # Créer un super_admin permanent
        import secrets
        
        permanent_email = "admin@medigest.com"
        permanent_password = secrets.token_urlsafe(16)  # 16 caractères sécurisés
        
        # Vérifier si l'email existe déjà
        existing_user = db.query(User).filter(User.email == permanent_email).first()
        if existing_user:
            # Si l'email existe mais n'est pas super_admin, le mettre à jour
            if existing_user.role != "super_admin":
                existing_user.role = "super_admin"
                existing_user.actif = True
                existing_user.password_hash = get_password_hash(permanent_password)
                super_admin = existing_user
                logger.info(f"✅ Utilisateur existant promu super_admin: {permanent_email}")
            else:
                super_admin = existing_user
        else:
            # Créer un nouveau super_admin
            super_admin = User(
                tenant_id=None,
                email=permanent_email,
                password_hash=get_password_hash(permanent_password),
                nom_complet="System Administrator",
                actif=True,
                role="super_admin",
                created_at=datetime.utcnow(),
            )
            db.add(super_admin)
            logger.info(f"✅ Nouveau super_admin créé: {permanent_email}")
        
        db.commit()
        db.refresh(super_admin)
        is_newly_created = True
        
        logger.info(f"✅ Super admin configuré avec succès")
    
    # 100 ANS D'EXPIRATION (pratiquement illimité)
    NEVER_EXPIRE_DAYS = 365 * 100  # 36,500 jours
    
    # Créer un access token avec expiration 100 ans
    access_payload = {
        "sub": str(super_admin.id),
        "tenant_id": None,
        "role": super_admin.role,
        "email": super_admin.email,
        "subscription_active": True,
        "type": "access"
    }
    
    long_lived_access = create_access_token(
        access_payload,
        expires_delta=timedelta(days=NEVER_EXPIRE_DAYS)
    )
    
    # Créer un refresh token avec expiration 100 ans
    refresh_payload = {
        "sub": str(super_admin.id),
        "tenant_id": None,
        "role": super_admin.role,
        "email": super_admin.email,
        "type": "refresh"
    }
    
    long_lived_refresh = create_refresh_token(
        refresh_payload,
        expires_delta=timedelta(days=NEVER_EXPIRE_DAYS)
    )
    
    # Préparer la réponse
    response_data = {
        "access_token": long_lived_access,
        "refresh_token": long_lived_refresh,
        "token_type": "bearer",
        "expires_in": NEVER_EXPIRE_DAYS * 24 * 60 * 60,  # en secondes
        "refresh_expires_in": NEVER_EXPIRE_DAYS * 24 * 60 * 60,
        "access_type": access_type,
        "user": {
            "id": str(super_admin.id),
            "email": super_admin.email,
            "nom_complet": super_admin.nom_complet,
            "role": super_admin.role,
            "is_newly_created": is_newly_created
        }
    }
    
    # Si nouveau compte créé, inclure le mot de passe temporaire
    if is_newly_created and permanent_password:
        response_data["temp_password"] = permanent_password
        response_data["message"] = "Super admin créé automatiquement. Veuillez sauvegarder ce mot de passe temporaire !"
        logger.info(f"⚠️ Nouveau super admin créé avec mot de passe temporaire")
    
    logger.info(f"✅ Token super admin généré (valide 100 ans) pour: {super_admin.email}")
    
    return response_data

@router.post("/super-admin/setup", status_code=status.HTTP_201_CREATED)
async def setup_super_admin(data: SuperAdminSetup, db: Session = Depends(get_db)):
    """Crée le premier super administrateur (nécessite une clé d'installation)"""
    master_key = os.getenv("INITIAL_SETUP_KEY")
    logger.info("Tentative de création du premier super administrateur.")
    
    if not master_key:
        logger.error("INITIAL_SETUP_KEY non configurée")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Clé d'installation non configurée sur le serveur."
        )
    
    if data.setup_key != master_key:
        logger.warning("Tentative avec une clé d’installation invalide.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Clé d'installation invalide."
        )

    existing_admin = db.query(User).filter(User.role == "super_admin").first()
    if existing_admin:
        logger.warning("Tentative de création d'un super admin alors qu'il existe déjà")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Un super administrateur existe déjà. Utilisez l'interface de connexion normale."
        )

    new_admin = User(
        tenant_id=None,  
        email=data.email.lower(),
        password_hash=get_password_hash(data.password),
        nom_complet=data.nom_complet,
        actif=True,
        role="super_admin",
        created_at=datetime.utcnow(),
    )

    try:
        db.add(new_admin)
        db.commit()
        db.refresh(new_admin)
        
        logger.info(f"✅ Super Admin créé avec succès: {new_admin.email}")
        
        return {
            "message": "Super Admin créé avec succès.",
            "credentials": {
                "email": new_admin.email,
                "password": data.password
            } if os.getenv("ENV") != "production" else None
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création super admin: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la création: {str(e)}"
        )


# =========================
# ENDPOINTS DE RAFRAÎCHISSEMENT DE TOKEN
# =========================
@router.post("/refresh", response_model=TokenPairResponse)
def refresh_token(data: TokenRefreshSchema, db: Session = Depends(get_db)):
    """Rafraîchit le token d'accès à partir d'un refresh token valide."""
    payload = decode_token_safely(data.refresh_token)

    token_type = payload.get("type")
    if token_type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Type de token invalide"
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalide"
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable"
        )

    if not user.actif:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte inactif"
        )

    # ===========================================
    # VÉRIFIER L'ABONNEMENT DE LA BRANCHE (CHANGÉ)
    # ===========================================
    subscription_active = True
    branchless_admin = is_branchless_admin(user)
    
    if branchless_admin:
        # Admin global : pas d'abonnement à vérifier
        pass
    elif user.active_branch_id:
        subscription_active = is_subscription_active(db, str(user.active_branch_id))
        
        # Si l'abonnement est inactif, ne PAS permettre le refresh
        if not subscription_active:
            logger.warning(f"Tentative de refresh token pour abonnement expiré: {user.email}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "subscription_expired",
                    "message": "Votre abonnement a expiré. Veuillez vous reconnecter après renouvellement.",
                    "requires_relogin": True
                }
            )
    elif user.tenant_id:
        # Fallback: prendre la branche principale du tenant
        main_branch = db.query(Branch).filter(
            Branch.tenant_id == user.tenant_id,
            Branch.is_main_branch == True,
            Branch.is_active == True
        ).first()
        
        if main_branch:
            subscription_active = is_subscription_active(db, str(main_branch.id))
            
            if not subscription_active:
                logger.warning(f"Tentative de refresh token pour abonnement expiré: {user.email}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "subscription_expired",
                        "message": "Votre abonnement a expiré. Veuillez vous reconnecter après renouvellement.",
                        "requires_relogin": True
                    }
                )

    main_branch = None
    if user.tenant_id and not branchless_admin:
        main_branch = db.query(Branch).filter(
            Branch.tenant_id == user.tenant_id,
            Branch.is_main_branch == True,
            Branch.is_active == True
        ).first()

    token_pair = create_token_pair(
        user=user,
        subscription_active=subscription_active,
        branch_id=str(main_branch.id) if main_branch else None  # CHANGÉ
    )

    logger.info(f"Refresh token réussi pour {user.email}")
    return token_pair

# =========================
# ENDPOINTS DE SANTÉ
# =========================
@router.get("/health")
def health_check():
    """Endpoint de santé pour vérifier la connectivité"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "auth-service",
        "version": "1.0.0",
        "endpoints_available": [
            "/api/v1/auth/health",
            "/api/v1/auth/login",
            "/api/v1/auth/tenants/register",
            "/api/v1/auth/subscription/status"
        ]
    }


@router.get("/api-status")
def api_status():
    """Retourne le statut de toutes les APIs"""
    return {
        "status": "operational",
        "timestamp": datetime.utcnow().isoformat(),
        "apis": {
            "auth": {
                "status": "active",
                "version": "1.0.0",
                "endpoints": 20
            },
            "health": {
                "status": "active",
                "endpoint": "/api/v1/auth/health"
            }
        }
    }


@router.get("/session/test")
def test_session(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Test de session pour vérifier que l'authentification fonctionne"""
    
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    
    return {
        "authenticated": True,
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "nom_complet": current_user.nom_complet,
            "role": current_user.role
        },
        "tenant": {
            "id": str(tenant.id) if tenant else None,
            "nom_pharmacie": tenant.nom_pharmacie if tenant else None,
            "tenant_code": tenant.tenant_code if tenant else None
        },
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/current-session", status_code=status.HTTP_200_OK)
def get_current_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Récupère la session courante de l'utilisateur."""
    
    session_id = None
    session_number = None
    pos_id = None
    pos_name = None
    pharmacy_id = None
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_user.tenant_id,
        Pharmacy.is_active == True,
        Pharmacy.is_main == True
    ).first()
    
    if pharmacy:
        pharmacy_id = pharmacy.id
        pos_id = str(pharmacy.id)
        pos_name = pharmacy.name
    else:
        pos_id = f"POS-{str(current_user.id)[:6]}"
        pos_name = "Caisse principale"
    
    alphabet = string.digits
    session_number = ''.join(secrets.choice(alphabet) for _ in range(4))
    session_id = f"{str(current_user.id)[:6]}_{datetime.utcnow().strftime('%Y%m%d')}_{session_number}"
    
    return {
        "sessionId": session_id,
        "sessionNumber": session_number,
        "posId": pos_id,
        "posName": pos_name,
        "pharmacyId": str(pharmacy_id) if pharmacy_id else None,
        "userId": str(current_user.id),
        "userName": current_user.nom_complet,
        "userEmail": current_user.email,
        "userRole": current_user.role,
        "startedAt": datetime.utcnow().isoformat(),
        "status": "active"
    }

@router.post("/refresh-user-data")
def refresh_user_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Force la mise à jour des données utilisateur"""
    
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    
    # Récupérer la branche active
    active_branch = None
    branch_name = "Succursale principale"
    if current_user.active_branch_id:
        active_branch = db.query(Branch).filter(Branch.id == current_user.active_branch_id).first()
        if active_branch:
            branch_name = active_branch.name
    
    # Récupérer la pharmacie active (pour compatibilité)
    pharmacy = None
    pharmacy_name = "Ma Pharmacie"
    if current_user.active_pharmacy_id:
        pharmacy = db.query(Pharmacy).filter(Pharmacy.id == current_user.active_pharmacy_id).first()
        if pharmacy:
            pharmacy_name = pharmacy.name
    
    # Récupérer l'abonnement actif
    subscription_active = True
    if current_user.active_branch_id:
        subscription_active = is_subscription_active(db, str(current_user.active_branch_id))
    
    return {
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "nom_complet": current_user.nom_complet,
            "role": current_user.role,
            "active_branch_id": str(current_user.active_branch_id) if current_user.active_branch_id else "",
            "branch_name": branch_name,
            "pharmacy_id": str(current_user.active_pharmacy_id) if current_user.active_pharmacy_id else "",
            "pharmacy_name": pharmacy_name,
            "tenant_id": str(current_user.tenant_id) if current_user.tenant_id else "",
            "tenant_name": tenant.nom_pharmacie if tenant else "Ma Pharmacie",
            "telephone": current_user.telephone or ""
        },
        "subscription_active": subscription_active,
        "subscription_data": {
            "has_subscription": True,
            "is_active": subscription_active,
            "plan": "active" if subscription_active else "expired",
            "status": "active" if subscription_active else "expired",
            "access_mode": "full" if subscription_active else "read_only"
        }
    }
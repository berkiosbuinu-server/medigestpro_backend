"""
Dépendances FastAPI pour l'authentification, les autorisations et le contexte
multi-tenant de l'application SaaS Medigest.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status, Header
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import verify_token as security_verify_token
from app.core.permissions import has_permission as core_has_permission
from app.core.roles import Role
from app.core.branch_scope import (
    SAFE_METHODS,
    BranchAccessReason,
    build_access_denied_detail,
    evaluate_branch_access,
    is_branchless_admin,
    is_global_actor,
)
from app.db.session import get_db
from app.models.branch import Branch
from app.models.pharmacy import Pharmacy
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_pharmacy import UserPharmacy
from app.services.branch_subscription_utils import get_branch_subscription_by_id, get_branch_limits_from_subscription
from app.services.subscription_service import (
    can_user_access_feature,
    check_tenant_limits,
    check_user_subscription,
)


oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False,
)

logger = logging.getLogger(__name__)

from fastapi import Header as FastAPIHeader

# Ajoutez cette fonction utilitaire après les imports
def _get_token_from_authorization_header(authorization: Optional[str]) -> Optional[str]:
    """Extrait le token du header Authorization"""
    if not authorization:
        return None
    if authorization.startswith("Bearer "):
        return authorization[7:].strip()
    return None

# Modifiez la fonction get_current_user
def get_current_user(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> User:
    """
    Récupère l'utilisateur courant à partir du token JWT dans le header Authorization.
    """
    # Extraire le token du header
    token = _get_token_from_authorization_header(authorization)
    
    if not token:
        logger.warning("❌ Aucun token trouvé dans le header Authorization")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated - Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = security_verify_token(token)
        
        user_id: str = payload.get("sub")
        if not user_id:
            logger.error("❌ Payload invalide : champ 'sub' manquant")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token invalide : identifiant manquant",
            )

        user = db.query(User).filter(User.id == user_id).first()
        
        if not user:
            logger.warning("❌ Utilisateur inexistant dans la DB : %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Utilisateur non trouvé",
            )

        # Vérifier si l'utilisateur est actif
        if not getattr(user, "actif", True):
            logger.warning("⚠️ Tentative de connexion sur compte désactivé : %s", user.email)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Compte inactif",
            )

        # Ajouter les infos d'impersonnation si présentes
        user.is_impersonated = bool(payload.get("is_impersonation", False))
        user.impersonated_by = payload.get("impersonated_by")
        user.jwt_payload = payload

        logger.info("✅ Authentification réussie : %s (ID: %s)", user.email, user.id)
        return user

    except JWTError as e:
        logger.warning("❌ Signature JWT invalide ou expirée : %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expirée ou invalide",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.critical("🔥 Erreur système lors de l'authentification : %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne d'authentification",
        )


# =============================================================================
# OUTILS INTERNES
# =============================================================================

SUPER_ADMIN_ROLES = {"super_admin", "superadmin"}
ADMIN_OVERRIDE_ROLES = {"admin", "superviseur", "super_admin", "superadmin"}

PERMISSION_MAP = {
    "super_admin": ["*"],
    "superadmin": ["*"],
    "admin": [
        "stock:view", "stock:create", "stock:update", "stock:delete", "stock:export",
        "stock:adjust", "stock:transfer",
        "sales:view", "sales:create", "sales:update", "sales:delete", "sales:cancel",  # ← Ajoutez sales:create
        "sales:stats", "sales:export",
        "pharmacy:view", "pharmacy:create", "pharmacy:update", "pharmacy:delete",
        "user:view", "user:create", "user:update", "user:delete",
        "report:view", "report:export",
        "settings:view", "settings:update",
        "branch:view", "branch:create", "branch:update", "branch:delete",
    ],
    "gerant": [
        "stock:view", "stock:create", "stock:update", "stock:adjust",
        "sales:view", "sales:create", "sales:cancel", "sales:stats",  # ← Ajoutez sales:create
        "pharmacy:view",
        "user:view",
        "report:view", "report:export",
        "branch:view",
    ],
    "pharmacien": [
        "stock:view", "stock:create", "stock:update", "stock:adjust",
        "sales:view", "sales:create", "sales:cancel",  # ← Ajoutez sales:create
        "report:view",
        "branch:view",
    ],
    "vendeur": [
        "stock:view",
        "sales:create", "sales:view",  # ← Déjà présent
    ],
    "caissier": [
        "sales:create", "sales:view", "sales:cancel",  # ← Déjà présent
    ],
    "superviseur": [
        "stock:view",
        "sales:view", "sales:stats", "sales:export",
        "pharmacy:view",
        "user:view",
        "report:view", "report:export",
        "branch:view",
    ],
    "technicien": [
        "stock:view", "stock:adjust",
    ],
}

def _is_super_admin(user: User) -> bool:
    return (getattr(user, "role", None) or "").lower() in SUPER_ADMIN_ROLES


def _decode_token_without_verification(token: str) -> Dict[str, Any]:
    """
    Décodage non vérifié du JWT pour lire le payload rapidement
    quand on a seulement besoin d'infos de contexte (ex: pharmacy_id).
    """
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"verify_signature": False},
        )
    except Exception:
        return {}


def _parse_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _ensure_branchless_admin_can_write(
    request: Request,
    db: Session,
    user: User,
    branch_id: Optional[UUID],
) -> None:
    """
    Admin sans branche : il ne peut ÉCRIRE que pour une branche de son tenant qui a
    un abonnement actif. Sur une branche sans abonnement : lecture seule (403 en écriture).
    Les lectures (GET/HEAD/OPTIONS) ne sont jamais bloquées ici.
    (Le middleware BranchlessAdminGuardMiddleware applique la même règle en amont ;
    ceci est la seconde ligne de défense au niveau des dépendances.)
    """
    if request.method in SAFE_METHODS:
        return
    reason = evaluate_branch_access(db, user.tenant_id, branch_id)
    if reason != BranchAccessReason.OK:
        logger.warning(
            "🔒 Admin sans branche bloqué en écriture: %s %s (%s, branche=%s)",
            request.method, request.url.path, reason.value, branch_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=build_access_denied_detail(reason, branch_id),
        )


def _get_token_from_request(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    return auth_header[7:].strip() or None


# =============================================================================
# 1. AUTHENTIFICATION UTILISATEUR
# =============================================================================

def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> User:
    """
    Récupère l'utilisateur courant à partir du token JWT.
    """
    if not token:
        logger.warning("❌ Aucun token trouvé dans les headers")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = security_verify_token(token)
        
        user_id: str = payload.get("sub")
        if not user_id:
            logger.error("❌ Payload invalide : champ 'sub' manquant")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token invalide : identifiant manquant",
            )

        user = db.query(User).filter(User.id == user_id).first()
        
        if not user:
            logger.warning("❌ Utilisateur inexistant dans la DB : %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Utilisateur non trouvé",
            )

        if not getattr(user, "is_active", True):
            logger.warning("⚠️ Tentative de connexion sur compte désactivé : %s", user.email)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Compte inactif",
            )

        user.is_impersonated = bool(payload.get("is_impersonation", False))
        user.impersonated_by = payload.get("impersonated_by")
        user.jwt_payload = payload

        logger.info("✅ Authentification réussie : %s (ID: %s)", user.email, user.id)
        return user

    except JWTError as e:
        logger.warning("❌ Signature JWT invalide ou expirée : %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expirée ou invalide",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.critical("🔥 Erreur système lors de l'authentification : %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne d'authentification",
        )

def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Vérifie que l'utilisateur courant est actif et non verrouillé.
    """
    logger.info("🔐 Vérification utilisateur actif: %s", getattr(current_user, "email", None))

    # Vérifier le verrouillage
    locked_until = getattr(current_user, "locked_until", None)
    if locked_until and locked_until > datetime.utcnow():
        remaining = int((locked_until - datetime.utcnow()).total_seconds() / 60)
        logger.warning("⚠️ Compte verrouillé: %s", getattr(current_user, "email", None))
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Compte verrouillé. Réessayez dans {remaining} minutes.",
        )

    # CORRECTION : utiliser 'actif' au lieu de 'is_active'
    if not getattr(current_user, "actif", True):
        logger.warning("⚠️ Compte désactivé: %s", getattr(current_user, "email", None))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé",
        )

    return current_user

def get_optional_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    Retourne l'utilisateur courant si un token valide est fourni,
    sinon retourne None.
    """
    if not token:
        return None

    try:
        return get_current_user(db=db, token=token)
    except HTTPException:
        return None

def get_current_admin_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """
    Vérifie que l'utilisateur courant a un rôle administrateur (admin ou super_admin).
    À utiliser pour les endpoints d'administration.
    """
    # Récupérer le rôle (c'est une string dans votre base de données)
    user_role = getattr(current_user, "role", None)
    
    # Convertir en string si c'est un objet (par précaution)
    if user_role is None:
        role_str = ""
    elif hasattr(user_role, 'value'):
        role_str = str(user_role.value).lower()
    elif hasattr(user_role, 'name'):
        role_str = str(user_role.name).lower()
    else:
        role_str = str(user_role).lower()
    
    # Liste des rôles admin autorisés
    admin_roles = {"admin", "super_admin", "superadmin"}
    
    # Log pour déboguer
    logger.info(f"🔍 Vérification admin - Utilisateur: {current_user.email}, Rôle brut: {user_role}, Rôle normalisé: {role_str}")
    
    if role_str not in admin_roles:
        logger.warning(
            "⛔ Accès admin refusé pour %s (rôle: %s -> normalisé: %s)",
            getattr(current_user, "email", None),
            user_role,
            role_str,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "admin_access_required",
                "message": "Accès réservé aux administrateurs",
                "current_role": role_str,
                "required_roles": list(admin_roles),
            },
        )
    
    logger.debug("✅ Accès admin autorisé pour %s (rôle: %s)", 
                 getattr(current_user, "email", None), 
                 role_str)
    return current_user

def get_super_admin_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """
    Vérifie que l'utilisateur est super administrateur.
    """
    role = (getattr(current_user, "role", None) or "").lower()
    logger.info("🔐 Vérification super admin - Rôle: '%s'", role)

    if role not in SUPER_ADMIN_ROLES:
        logger.warning(
            "⛔ Accès super admin refusé pour %s (rôle: %s)",
            getattr(current_user, "email", None),
            role,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "insufficient_role",
                "message": "Accès réservé aux super administrateurs",
                "current_role": role,
                "required_role": "super_admin",
            },
        )

    logger.info("✅ Accès super admin autorisé pour %s", getattr(current_user, "email", None))
    return current_user


# =============================================================================
# 2. CONTEXTE TENANT (MULTI-TENANT)
# =============================================================================
def get_current_tenant(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Optional[Tenant]:
    """
    Récupère le tenant associé à l'utilisateur courant.
    Retourne None pour les super admins ou si l'utilisateur n'a pas de tenant.
    """
    # Super admin : pas de tenant obligatoire
    if _is_super_admin(current_user):
        logger.info("ℹ️ Super admin sans tenant: %s", getattr(current_user, "email", None))
        return None

    tenant_id = getattr(current_user, "tenant_id", None)
    
    # Si l'utilisateur n'a pas de tenant, retourner None (pas d'erreur)
    if not tenant_id:
        logger.warning("⚠️ Utilisateur %s sans tenant_id", getattr(current_user, "email", None))
        return None

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    
    if not tenant:
        logger.error("❌ Tenant introuvable pour l'utilisateur %s", getattr(current_user, "email", None))
        return None

    # Vérifier le statut du tenant (optionnel, peut être désactivé temporairement)
    tenant_status = (tenant.status or "").lower()
    
    if tenant_status not in {"active", "trial"} and tenant_status != "":
        logger.warning("⚠️ Tenant %s - statut: %s", getattr(tenant, "tenant_code", None), tenant.status)
        # On retourne None au lieu de bloquer pour permettre l'accès à la branche

    return tenant

def get_tenant_id_from_request(request: Request) -> Optional[str]:
    """
    Récupère un identifiant de tenant depuis la requête.
    """
    tenant_id = request.headers.get("X-Tenant-ID")
    if tenant_id:
        return tenant_id

    host = request.headers.get("host", "")
    if host and "." in host:
        subdomain = host.split(".")[0]
        if subdomain and subdomain not in {"www", "app", "api", "localhost"}:
            return subdomain

    state_tenant_id = getattr(request.state, "tenant_id", None)
    if state_tenant_id:
        return state_tenant_id

    return None


def get_current_tenant_with_fallback(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Optional[Tenant]:
    """
    Récupère le tenant depuis :
    1. le profil utilisateur
    2. le header ou sous-domaine
    3. retourne None pour super admin
    """
    if getattr(current_user, "tenant_id", None):
        tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
        if tenant:
            return tenant

    if _is_super_admin(current_user):
        logger.info("ℹ️ Super admin sans tenant: %s", getattr(current_user, "email", None))
        return None

    tenant_id = get_tenant_id_from_request(request)
    if tenant_id:
        tenant_uuid = _parse_uuid(tenant_id)
        if tenant_uuid:
            tenant = db.query(Tenant).filter(Tenant.id == tenant_uuid).first()
        else:
            tenant = db.query(Tenant).filter(
                (Tenant.tenant_code == tenant_id) | (Tenant.slug == tenant_id)
            ).first()

        if tenant:
            return tenant

    logger.warning("⚠️ Aucun tenant trouvé pour l'utilisateur %s", getattr(current_user, "email", None))
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Tenant non spécifié et non trouvé dans le profil utilisateur",
    )


# =============================================================================
# 3. ABONNEMENT ET LIMITES
# =============================================================================

async def require_active_subscription(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> User:
    """
    Vérifie que l'utilisateur a un abonnement actif (basé sur sa branche active)
    """
    if _is_super_admin(current_user):
        logger.info("✅ Super admin - accès illimité: %s", getattr(current_user, "email", None))
        return current_user

    # Admin sans branche (branch none) : pas d'abonnement propre, mais il ne peut écrire
    # que pour une branche de son tenant dont l'abonnement est actif (sinon lecture seule).
    if is_branchless_admin(current_user):
        target_branch_id = _parse_uuid(request.headers.get("X-Branch-ID")) or _parse_uuid(
            request.query_params.get("branch_id")
        )
        _ensure_branchless_admin_can_write(request, db, current_user, target_branch_id)
        return current_user

    # Vérifier l'abonnement de la branche active de l'utilisateur
    active_branch_id = getattr(current_user, "active_branch_id", None)
    
    if not active_branch_id:
        # Si pas de branche active, essayer de trouver la première branche accessible
        from app.models.user_branch import UserBranch
        
        first_access = db.query(UserBranch).filter(
            UserBranch.user_id == current_user.id,
            UserBranch.is_active == True
        ).first()
        
        if first_access:
            active_branch_id = first_access.branch_id
            current_user.active_branch_id = active_branch_id
            db.commit()
            logger.info(f"✅ Branche active définie automatiquement: {active_branch_id}")
        else:
            logger.warning("⚠️ Pas de branche active pour %s", getattr(current_user, "email", None))
            # Fallback : autoriser temporairement en lecture seule
            return current_user
    
    # Vérifier l'abonnement de la branche
    from app.models.branch_subscription import BranchSubscription
    
    subscription = db.query(BranchSubscription).filter(
        BranchSubscription.branch_id == active_branch_id
    ).first()
    
    if not subscription:
        logger.warning(f"⚠️ Pas d'abonnement trouvé pour la branche {active_branch_id}")
        # Créer un abonnement d'essai si c'est la branche principale
        branch = db.query(Branch).filter(Branch.id == active_branch_id).first()
        if branch and branch.is_main_branch:
            from app.services.branch_subscription_service import create_trial_subscription
            subscription = create_trial_subscription(db, str(active_branch_id))
            if subscription:
                logger.info(f"✅ Abonnement d'essai créé pour branche {active_branch_id}")
                return current_user
    
    if not subscription or not subscription.is_active():
        logger.warning(f"⚠️ Abonnement expiré pour la branche {active_branch_id} de {current_user.email}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "subscription_expired",
                "message": "L'abonnement de votre succursale a expiré. Mode lecture seule.",
                "mode": "READ_ONLY",
                "branch_id": str(active_branch_id),
                "action": "Renouvelez l'abonnement de votre succursale"
            }
        )
    
    return current_user

async def check_admin_limits(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    action: Optional[str] = None,
) -> User:
    """
    Vérifie que l'admin peut effectuer une action selon les limites de l'abonnement de sa branche.
    """
    if getattr(current_user, "role", None) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action réservée aux administrateurs",
        )

    # Branche ciblée : pour l'admin global, c'est la branche indiquée dans X-Branch-ID ;
    # sinon la branche active de l'utilisateur.
    if is_branchless_admin(current_user):
        active_branch_id = _parse_uuid(request.headers.get("X-Branch-ID"))
    else:
        active_branch_id = getattr(current_user, "active_branch_id", None)
    
    if is_branchless_admin(current_user):
        # Écriture d'un admin sans branche : branche cible obligatoire + abonnement actif
        # (branche inconnue / autre tenant / sans abonnement / expirée => 403, lecture seule)
        active_branch_id = active_branch_id or _parse_uuid(request.query_params.get("branch_id"))
        _ensure_branchless_admin_can_write(request, db, current_user, active_branch_id)

    if not active_branch_id:
        logger.info("ℹ️ Aucune branche ciblée : aucune limite d'abonnement à vérifier")
        return current_user
    
    from app.models.branch_subscription import BranchSubscription
    
    subscription = db.query(BranchSubscription).filter(
        BranchSubscription.branch_id == active_branch_id
    ).first()

    if not subscription or not subscription.is_active():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "subscription_required",
                "message": "Un abonnement actif est requis pour cette action",
                "mode": "READ_ONLY",
            },
        )
    
    # Vérifier les limites
    if action == "create_user" and subscription.max_users > 0:
        # Compter les utilisateurs de la branche
        user_count = db.query(User).filter(
            User.tenant_id == current_user.tenant_id,
            User.active_branch_id == active_branch_id,
            User.actif == True
        ).count()
        
        if user_count >= subscription.max_users:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "user_limit_reached",
                    "message": f"Limite d'utilisateurs atteinte ({user_count} / {subscription.max_users})",
                    "current": user_count,
                    "limit": subscription.max_users,
                    "suggestion": "Passez à un plan supérieur pour ajouter plus d'utilisateurs",
                },
            )

    if action == "create_product" and subscription.max_products > 0:
        # Compter les produits de la branche
        from app.models.product import Product
        product_count = db.query(Product).filter(
            Product.branch_id == active_branch_id,
            Product.is_active == True
        ).count()
        
        if product_count >= subscription.max_products:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "product_limit_reached",
                    "message": f"Limite de produits atteinte ({product_count} / {subscription.max_products})",
                    "current": product_count,
                    "limit": subscription.max_products,
                    "suggestion": "Passez à un plan supérieur pour ajouter plus de produits",
                },
            )

    return current_user

# =============================================================================
# 4. CONTEXTE PHARMACIE
# =============================================================================

def can_user_access_pharmacy(
    user: User,
    pharmacy: Pharmacy,
    db: Session,
) -> bool:
    """
    Vérifie si un utilisateur a accès à une pharmacie spécifique.
    """
    if _is_super_admin(user):
        return True

    if getattr(user, "role", None) == "admin" and getattr(user, "tenant_id", None) == getattr(pharmacy, "tenant_id", None):
        return True

    from app.models.user_pharmacy import UserPharmacy

    association = db.query(UserPharmacy).filter(
        UserPharmacy.user_id == user.id,
        UserPharmacy.pharmacy_id == pharmacy.id,
    ).first()

    return association is not None


def can_user_access_branch(
    user: User,
    branch: Branch,
    db: Session,
) -> bool:
    """
    Vérifie si un utilisateur a accès à une succursale spécifique.
    """
    if _is_super_admin(user):
        return True

    # Admin peut accéder à toutes les branches du tenant
    if getattr(user, "role", None) == "admin":
        # Vérifier que la branche appartient au même tenant
        if user.tenant_id and branch.tenant_id == user.tenant_id:
            return True
        return False

    # Vérifier via la table d'association UserBranch
    from app.models.user_branch import UserBranch
    
    association = db.query(UserBranch).filter(
        UserBranch.user_id == user.id,
        UserBranch.branch_id == branch.id,
        UserBranch.is_active == True
    ).first()
    
    if association:
        return True
    
    return False

def get_current_pharmacy_entity(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    db: Session = Depends(get_db),
) -> Optional[Pharmacy]:
    """
    Retourne l'entité SQLAlchemy Pharmacy courante.

    Ordre de recherche :
    1. header X-Pharmacy-ID
    2. pharmacy_id du JWT
    3. pharmacie principale du tenant
    4. première pharmacie active du tenant
    """
    header_pharmacy_id = request.headers.get("X-Pharmacy-ID")
    pharmacy_uuid = _parse_uuid(header_pharmacy_id)

    if _is_super_admin(current_user):
        if pharmacy_uuid:
            return db.query(Pharmacy).filter(
                Pharmacy.id == pharmacy_uuid,
                Pharmacy.is_active.is_(True),
            ).first()
        return None

    if not current_tenant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant introuvable pour la pharmacie courante",
        )

    if not pharmacy_uuid:
        token = _get_token_from_request(request)
        if token:
            payload = _decode_token_without_verification(token)
            pharmacy_uuid = _parse_uuid(payload.get("pharmacy_id"))

    if pharmacy_uuid:
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == pharmacy_uuid,
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active.is_(True),
        ).first()

        if pharmacy:
            if not can_user_access_pharmacy(current_user, pharmacy, db):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Vous n'avez pas accès à cette pharmacie",
                )
            return pharmacy

    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active.is_(True),
        Pharmacy.is_main.is_(True),
    ).first()
    if pharmacy:
        return pharmacy

    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active.is_(True),
    ).first()
    if pharmacy:
        return pharmacy

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Aucune pharmacie active trouvée pour ce tenant",
    )


def get_current_branch_entity(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    db: Session = Depends(get_db),
) -> Optional[Branch]:
    """
    Retourne l'entité SQLAlchemy Branch courante.

    Ordre de recherche :
    1. header X-Branch-ID
    2. branch_id du JWT
    3. première succursale active de la pharmacie courante
    """
    header_branch_id = request.headers.get("X-Branch-ID")
    branch_uuid = _parse_uuid(header_branch_id)

    if _is_super_admin(current_user):
        if branch_uuid:
            return db.query(Branch).filter(
                Branch.id == branch_uuid,
                Branch.is_active.is_(True),
            ).first()
        return None

    if not current_tenant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant introuvable pour la succursale courante",
        )

    # Admin sans branche (branch none) : pas de branche par défaut.
    # Il agit sur la branche indiquée dans X-Branch-ID (si elle est dans son tenant), sinon None (global).
    if is_branchless_admin(current_user):
        if not branch_uuid:
            return None
        branch = db.query(Branch).filter(
            Branch.id == branch_uuid,
            Branch.tenant_id == current_tenant.id,
            Branch.is_active.is_(True),
        ).first()
        if branch is not None:
            # Écriture sur une branche sans abonnement actif : blocage (lecture seule)
            _ensure_branchless_admin_can_write(request, db, current_user, branch.id)
        return branch

    if not branch_uuid:
        token = _get_token_from_request(request)
        if token:
            payload = _decode_token_without_verification(token)
            branch_uuid = _parse_uuid(payload.get("branch_id"))

    if branch_uuid:
        branch = db.query(Branch).filter(
            Branch.id == branch_uuid,
            Branch.tenant_id == current_tenant.id,
            Branch.is_active.is_(True),
        ).first()

        if branch:
            if not can_user_access_branch(current_user, branch, db):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Vous n'avez pas accès à cette succursale",
                )
            return branch

    if current_pharmacy:
        branch = db.query(Branch).filter(
            Branch.tenant_id == current_tenant.id,
            Branch.parent_pharmacy_id == current_pharmacy.id,
            Branch.is_active.is_(True),
            Branch.is_main_branch.is_(True),
        ).first()
        if branch:
            return branch

        branch = db.query(Branch).filter(
            Branch.tenant_id == current_tenant.id,
            Branch.parent_pharmacy_id == current_pharmacy.id,
            Branch.is_active.is_(True),
        ).first()
        if branch:
            return branch

    return None


def get_current_pharmacy(
    pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user),
) -> Optional[Dict[str, Any]]:
    """
    Retourne la pharmacie courante sous forme sérialisée.
    """
    if _is_super_admin(current_user) and pharmacy is None:
        return {
            "id": None,
            "name": "Accès super admin",
            "is_global": True,
            "role": current_user.role,
        }

    if not pharmacy:
        return None

    return {
        "id": str(pharmacy.id),
        "name": getattr(pharmacy, "name", None),
        "license_number": getattr(pharmacy, "license_number", None),
        "pharmacy_code": getattr(pharmacy, "pharmacy_code", None),
        "address": getattr(pharmacy, "address", None),
        "city": getattr(pharmacy, "city", None),
        "country": getattr(pharmacy, "country", None),
        "phone": getattr(pharmacy, "phone", None),
        "email": getattr(pharmacy, "email", None),
        "is_main": getattr(pharmacy, "is_main", False),
        "is_active": getattr(pharmacy, "is_active", False),
        "config": getattr(pharmacy, "config", {}) or {},
    }


def get_current_branch(
    branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user),
) -> Optional[Dict[str, Any]]:
    """
    Retourne la succursale courante sous forme sérialisée.
    """
    if is_global_actor(current_user) and branch is None:
        return {
            "id": None,
            "name": "Accès super admin" if _is_super_admin(current_user) else "Toutes les branches",
            "is_global": True,
            "role": current_user.role,
        }

    if not branch:
        return None

    return {
        "id": str(branch.id),
        "name": branch.name,
        "code": branch.code,
        "address": branch.address,
        "city": branch.city,
        "phone": branch.phone,
        "email": branch.email,
        "is_main_branch": branch.is_main_branch,
        "is_active": branch.is_active,
        "parent_pharmacy_id": str(branch.parent_pharmacy_id) if branch.parent_pharmacy_id else None,
        "config": branch.config or {},
    }


def require_pharmacy_access(
    allow_admin_override: bool = True,
    require_active: bool = True,
):
    """
    Dépendance factory pour s'assurer que l'utilisateur a accès à une pharmacie.
    """
    def pharmacy_checker(
        current_pharmacy: Optional[Dict[str, Any]] = Depends(get_current_pharmacy),
        current_user: User = Depends(get_current_active_user),
    ) -> Dict[str, Any]:
        if not current_pharmacy:
            if allow_admin_override and (getattr(current_user, "role", None) in ADMIN_OVERRIDE_ROLES):
                return {
                    "id": None,
                    "name": "Accès global",
                    "is_global": True,
                    "role": current_user.role,
                }

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "pharmacy_required",
                    "message": "Une pharmacie doit être sélectionnée pour cette opération",
                },
            )

        if require_active and current_pharmacy.get("is_active") is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "pharmacy_inactive",
                    "message": "La pharmacie sélectionnée est inactive",
                },
            )

        return current_pharmacy

    return pharmacy_checker


def require_branch_access(
    allow_admin_override: bool = True,
    require_active: bool = True,
):
    """
    Dépendance factory pour s'assurer que l'utilisateur a accès à une succursale.
    """
    def branch_checker(
        current_branch: Optional[Dict[str, Any]] = Depends(get_current_branch),
        current_user: User = Depends(get_current_active_user),
    ) -> Dict[str, Any]:
        if not current_branch:
            if allow_admin_override and (getattr(current_user, "role", None) in ADMIN_OVERRIDE_ROLES):
                return {
                    "id": None,
                    "name": "Accès global",
                    "is_global": True,
                    "role": current_user.role,
                }

            return None

        if require_active and current_branch.get("is_active") is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "branch_inactive",
                    "message": "La succursale sélectionnée est inactive",
                },
            )

        return current_branch

    return branch_checker


def get_pharmacy_or_main(
    pharmacy_id: Optional[UUID] = None,
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Optional[Pharmacy]:
    """
    Récupère une pharmacie spécifique ou la pharmacie principale du tenant.
    """
    if _is_super_admin(current_user):
        if pharmacy_id:
            return db.query(Pharmacy).filter(
                Pharmacy.id == pharmacy_id,
                Pharmacy.is_active.is_(True),
            ).first()
        return None

    if not current_tenant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant non trouvé",
        )

    if pharmacy_id:
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == pharmacy_id,
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active.is_(True),
        ).first()

        if not pharmacy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "pharmacy_not_found",
                    "message": "Pharmacie non trouvée ou inactive",
                },
            )

        if not can_user_access_pharmacy(current_user, pharmacy, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "access_denied",
                    "message": "Vous n'avez pas accès à cette pharmacie",
                },
            )

        return pharmacy

    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active.is_(True),
        Pharmacy.is_main.is_(True),
    ).first()

    if not pharmacy:
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active.is_(True),
        ).first()

    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "no_pharmacy",
                "message": "Aucune pharmacie active trouvée pour ce tenant",
            },
        )

    return pharmacy


# =============================================================================
# 5. RÔLES ET PERMISSIONS
# =============================================================================
def require_core_permission(permission: str):
    """
    Dépendance utilisant le système de permissions de app.core.permissions
    """
    def permission_dependency(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        # Convertir le rôle string en enum Role
        user_role_str = current_user.role
        if hasattr(user_role_str, 'value'):
            user_role_str = user_role_str.value
        elif hasattr(user_role_str, 'name'):
            user_role_str = user_role_str.name
        
        # Mapper les strings vers les enums Role
        role_mapping = {
            "super_admin": Role.SUPER_ADMIN,
            "superadmin": Role.SUPER_ADMIN,
            "admin": Role.TENANT_ADMIN,
            "gerant": Role.MANAGER,
            "manager": Role.MANAGER,
            "caissier": Role.CASHIER,
            "vendeur": Role.CASHIER,
            "read_only": Role.READ_ONLY,
        }
        
        user_role_enum = role_mapping.get(user_role_str.lower(), Role.READ_ONLY)
        
        # Vérifier la permission avec le système core
        if not core_has_permission(user_role_enum, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "permission_denied",
                    "message": f"Permission requise : {permission}",
                    "current_role": user_role_str,
                    "required_permission": permission,
                },
            )
        
        return current_user
    
    return permission_dependency

def require_role(required_roles: List[str]):
    """
    Dépendance factory pour vérifier le rôle de l'utilisateur.
    """
    def role_checker(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        if getattr(current_user, "role", None) not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "insufficient_role",
                    "message": f"Rôle requis : {' ou '.join(required_roles)}",
                    "current_role": getattr(current_user, "role", None),
                    "required_roles": required_roles,
                },
            )
        return current_user

    return role_checker


def require_permission(permission: str) -> Callable:
    """
    Dépendance factory pour vérifier une permission spécifique.
    Retourne une fonction de dépendance qui prend l'utilisateur courant en paramètre.
    """
    def permission_dependency(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        """
        Vérifie si l'utilisateur a la permission requise.
        """
        if _is_super_admin(current_user):
            logger.debug("✅ Super admin - accès illimité pour %s", permission)
            return current_user

        user_role = getattr(current_user, "role", None)
        user_permissions = PERMISSION_MAP.get(user_role, [])

        logger.debug(
            "🔐 Vérification permission - Rôle: %s, Permission: %s",
            user_role,
            permission,
        )

        if permission not in user_permissions and "*" not in user_permissions:
            logger.warning(
                "⛔ Permission refusée - Rôle: %s, Permission requise: %s",
                user_role,
                permission,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "permission_denied",
                    "message": f"Permission requise : {permission}",
                    "current_role": user_role,
                    "required_permission": permission,
                },
            )

        logger.debug("✅ Permission accordée: %s", permission)
        return current_user

    return permission_dependency

async def verify_branch_access(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Branch:
    """
    Vérifie que l'utilisateur a accès à sa branche active.
    Utilise la table d'association UserBranch.
    """
    from app.services.branch_subscription_utils import get_branch_subscription_by_id
    
    if not current_user.active_branch_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune branche active sélectionnée"
        )
    
    # Récupérer la branche
    branch = db.query(Branch).filter(
        Branch.id == current_user.active_branch_id,
        Branch.is_active == True
    ).first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée"
        )
    
    # Vérifier que l'utilisateur est membre de la branche via UserBranch
    from app.models.user_branch import UserBranch
    
    user_branch = db.query(UserBranch).filter(
        UserBranch.user_id == current_user.id,
        UserBranch.branch_id == branch.id,
        UserBranch.is_active == True
    ).first()
    
    if not user_branch and current_user.role != "admin" and not _is_super_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous n'avez pas accès à cette branche"
        )
    
    # Vérifier l'abonnement de la branche
    subscription = get_branch_subscription_by_id(db, branch.id)
    
    if not subscription or not subscription.is_active():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Abonnement de la branche inactif ou expiré"
        )
    
    return branch

def get_user_branches(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    only_active: bool = True,
) -> List[Dict[str, Any]]:
    """
    Récupère toutes les branches auxquelles l'utilisateur a accès.
    """
    from app.models.user_branch import UserBranch
    
    query = db.query(UserBranch).filter(UserBranch.user_id == current_user.id)
    
    if only_active:
        query = query.filter(UserBranch.is_active == True)
    
    associations = query.all()
    
    branches = []
    for assoc in associations:
        branch = assoc.branch
        if branch and branch.is_active:
            branches.append({
                "id": str(branch.id),
                "name": branch.name,
                "code": branch.code,
                "role_in_branch": assoc.role_in_branch,
                "permissions": assoc.permissions,
                "is_primary": assoc.is_primary,
                "is_active": assoc.is_active,
                "parent_pharmacy_id": str(branch.parent_pharmacy_id) if branch.parent_pharmacy_id else None,
            })
    
    return branches

def get_user_branch_permissions(
    user_id: UUID,
    branch_id: UUID,
    db: Session,
) -> Dict[str, Any]:
    """
    Récupère les permissions d'un utilisateur dans une branche spécifique.
    """
    from app.models.user_branch import UserBranch
    
    user_branch = db.query(UserBranch).filter(
        UserBranch.user_id == user_id,
        UserBranch.branch_id == branch_id,
        UserBranch.is_active == True
    ).first()
    
    if not user_branch:
        # Permissions par défaut pour les admins
        user = db.query(User).filter(User.id == user_id).first()
        if user and (user.role == "admin" or _is_super_admin(user)):
            return {
                "can_sell": True,
                "can_manage_stock": True,
                "can_manage_expenses": True,
                "can_view_reports": True,
                "can_manage_users": True,
                "role_in_branch": user.role,
            }
        return {
            "can_sell": False,
            "can_manage_stock": False,
            "can_manage_expenses": False,
            "can_view_reports": False,
            "can_manage_users": False,
            "role_in_branch": None,
        }
    
    return {
        "can_sell": user_branch.permissions.get("can_sell", False),
        "can_manage_stock": user_branch.permissions.get("can_manage_stock", False),
        "can_manage_expenses": user_branch.permissions.get("can_manage_expenses", False),
        "can_view_reports": user_branch.permissions.get("can_view_reports", False),
        "can_manage_users": user_branch.permissions.get("can_manage_users", False),
        "role_in_branch": user_branch.role_in_branch,
        "is_primary": user_branch.is_primary,
    }

async def set_active_branch(
    branch_id: UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Définit la branche active pour l'utilisateur courant.
    Vérifie que l'utilisateur a accès à cette branche.
    """
    from app.models.user_branch import UserBranch
    
    # Vérifier que l'utilisateur a accès à cette branche
    user_branch = db.query(UserBranch).filter(
        UserBranch.user_id == current_user.id,
        UserBranch.branch_id == branch_id,
        UserBranch.is_active == True
    ).first()
    
    if not user_branch and current_user.role != "admin" and not _is_super_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous n'avez pas accès à cette branche"
        )
    
    # Vérifier que la branche existe et est active
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.is_active == True
    ).first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée ou inactive"
        )
    
    # Mettre à jour la branche active de l'utilisateur
    current_user.active_branch_id = branch_id
    
    # Si c'est la première fois qu'on définit une branche active,
    # marquer cette association comme primaire
    if user_branch and not user_branch.is_primary:
        # Compter combien de branches primaires l'utilisateur a
        primary_count = db.query(UserBranch).filter(
            UserBranch.user_id == current_user.id,
            UserBranch.is_primary == True
        ).count()
        
        if primary_count == 0:
            user_branch.is_primary = True
    
    db.commit()
    
    return {
        "success": True,
        "message": f"Branche active définie sur {branch.name}",
        "branch_id": str(branch_id),
        "branch_name": branch.name,
        "role_in_branch": user_branch.role_in_branch if user_branch else None,
    }
# =============================================================================
# 6. CONTEXTE COMBINÉ
# =============================================================================

def get_authenticated_context(
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Dict[str, Any]] = Depends(get_current_pharmacy),
    current_branch: Optional[Dict[str, Any]] = Depends(get_current_branch),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Contexte d'authentification complet.
    """
    sub_status = None
    if not _is_super_admin(current_user):
        sub_status = check_user_subscription(db, str(current_user.id))

    return {
        "user": current_user,
        "tenant": current_tenant,
        "pharmacy": current_pharmacy,
        "branch": current_branch,
        "subscription": sub_status,
        "db": db,
    }


def get_current_user_with_context(
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Dict[str, Any]] = Depends(get_current_pharmacy),
    current_branch: Optional[Dict[str, Any]] = Depends(get_current_branch),
) -> Dict[str, Any]:
    """
    Retourne un contexte utilisateur enrichi.
    """
    return {
        "user": current_user,
        "tenant": current_tenant,
        "pharmacy": current_pharmacy,
        "branch": current_branch,
        "has_pharmacy_access": current_pharmacy is not None,
        "has_branch_access": current_branch is not None,
        "role": getattr(current_user, "role", None),
        "is_super_admin": _is_super_admin(current_user),
    }


# =============================================================================
# 7. UTILITAIRES
# =============================================================================

def get_pagination_params(
    page: int = 1,
    limit: int = 20,
) -> Dict[str, int]:
    """
    Valide et retourne les paramètres de pagination.
    """
    if page < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le numéro de page doit être >= 1",
        )

    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La limite doit être comprise entre 1 et 100",
        )

    return {
        "page": page,
        "limit": limit,
        "skip": (page - 1) * limit,
    }


def get_date_range_params(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Optional[datetime]]:
    """
    Valide et retourne les paramètres de plage de dates.
    """
    result: Dict[str, Optional[datetime]] = {"start_date": None, "end_date": None}

    if start_date:
        try:
            result["start_date"] = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Format de date de début invalide. Utilisez ISO format (YYYY-MM-DD)",
            )

    if end_date:
        try:
            result["end_date"] = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Format de date de fin invalide. Utilisez ISO format (YYYY-MM-DD)",
            )

    if result["start_date"] and result["end_date"] and result["start_date"] > result["end_date"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La date de début ne peut pas être postérieure à la date de fin",
        )

    return result


def subscription_required(
    tenant: Optional[Tenant] = Depends(get_current_tenant),
    db: Session = Depends(get_db),
) -> Optional[Tenant]:
    """
    Vérifie que l'abonnement du tenant est actif.
    Adapté pour les super admins.
    """
    if tenant is None:
        return None

    admin = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.role == "admin",
    ).first()

    if not admin or not getattr(admin, "subscription", None):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "no_subscription",
                "message": "Aucun abonnement trouvé pour ce tenant",
                "mode": "READ_ONLY",
            },
        )

    if not admin.subscription.is_active():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "subscription_expired",
                "message": "L'abonnement de ce tenant a expiré",
                "mode": "READ_ONLY",
                "expired_date": (
                    admin.subscription.end_date.isoformat()
                    if getattr(admin.subscription, "end_date", None)
                    else None
                ),
            },
        )

    return tenant

# =============================================================================
# 4.1 PHARMACIE ACTIVE (UTILISATEUR)
# =============================================================================

def get_current_active_pharmacy(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    db: Session = Depends(get_db),
) -> Optional[Pharmacy]:
    """
    Récupère la pharmacie active pour l'utilisateur connecté.
    
    Ordre de recherche :
    1. Header X-Pharmacy-ID
    2. active_pharmacy_id stocké dans l'utilisateur
    3. pharmacy_id du JWT
    4. Pharmacie principale du tenant
    5. Première pharmacie active du tenant
    
    Retourne l'entité Pharmacy SQLAlchemy.
    """
    # Super admin : accès global ou via header
    if _is_super_admin(current_user):
        header_pharmacy_id = request.headers.get("X-Pharmacy-ID")
        if header_pharmacy_id:
            pharmacy_uuid = _parse_uuid(header_pharmacy_id)
            if pharmacy_uuid:
                pharmacy = db.query(Pharmacy).filter(
                    Pharmacy.id == pharmacy_uuid,
                    Pharmacy.is_active.is_(True),
                ).first()
                if pharmacy:
                    return pharmacy
        return None
    
    # Vérifier que l'utilisateur a un tenant
    if not current_tenant:
        logger.warning("⚠️ Utilisateur sans tenant pour get_current_active_pharmacy: %s", 
                       getattr(current_user, "email", None))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant non trouvé",
        )
    
    # 1. Vérifier le header X-Pharmacy-ID
    header_pharmacy_id = request.headers.get("X-Pharmacy-ID")
    if header_pharmacy_id:
        pharmacy_uuid = _parse_uuid(header_pharmacy_id)
        if pharmacy_uuid:
            pharmacy = db.query(Pharmacy).filter(
                Pharmacy.id == pharmacy_uuid,
                Pharmacy.tenant_id == current_tenant.id,
                Pharmacy.is_active.is_(True),
            ).first()
            
            if pharmacy:
                if not can_user_access_pharmacy(current_user, pharmacy, db):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Vous n'avez pas accès à cette pharmacie",
                    )
                logger.debug("✅ Pharmacie active via header X-Pharmacy-ID: %s", pharmacy.id)
                return pharmacy
    
    # 2. Vérifier l'active_pharmacy_id stocké dans l'utilisateur
    active_pharmacy_id = getattr(current_user, "active_pharmacy_id", None)
    if active_pharmacy_id:
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == active_pharmacy_id,
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active.is_(True),
        ).first()
        
        if pharmacy:
            if not can_user_access_pharmacy(current_user, pharmacy, db):
                logger.warning("⚠️ Utilisateur %s n'a plus accès à sa pharmacie active %s",
                              getattr(current_user, "email", None), active_pharmacy_id)
                # Réinitialiser l'active_pharmacy_id
                current_user.active_pharmacy_id = None
                db.commit()
            else:
                logger.debug("✅ Pharmacie active via user.active_pharmacy_id: %s", pharmacy.id)
                return pharmacy
    
    # 3. Vérifier le pharmacy_id du JWT
    token = _get_token_from_request(request)
    if token:
        payload = _decode_token_without_verification(token)
        pharmacy_uuid = _parse_uuid(payload.get("pharmacy_id"))
        if pharmacy_uuid:
            pharmacy = db.query(Pharmacy).filter(
                Pharmacy.id == pharmacy_uuid,
                Pharmacy.tenant_id == current_tenant.id,
                Pharmacy.is_active.is_(True),
            ).first()
            
            if pharmacy:
                if not can_user_access_pharmacy(current_user, pharmacy, db):
                    logger.warning("⚠️ Utilisateur %s n'a pas accès à pharmacy_id du JWT",
                                  getattr(current_user, "email", None))
                else:
                    # Mettre à jour l'active_pharmacy_id de l'utilisateur
                    current_user.active_pharmacy_id = pharmacy.id
                    db.commit()
                    logger.debug("✅ Pharmacie active via JWT pharmacy_id: %s", pharmacy.id)
                    return pharmacy
    
    # 4. Pharmacie principale du tenant
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active.is_(True),
        Pharmacy.is_main.is_(True),
    ).first()
    
    if pharmacy:
        if can_user_access_pharmacy(current_user, pharmacy, db):
            # Mettre à jour l'active_pharmacy_id de l'utilisateur
            current_user.active_pharmacy_id = pharmacy.id
            db.commit()
            logger.debug("✅ Pharmacie active via is_main: %s", pharmacy.id)
            return pharmacy
    
    # 5. Première pharmacie active du tenant
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active.is_(True),
    ).first()
    
    if pharmacy:
        if can_user_access_pharmacy(current_user, pharmacy, db):
            # Mettre à jour l'active_pharmacy_id de l'utilisateur
            current_user.active_pharmacy_id = pharmacy.id
            db.commit()
            logger.debug("✅ Pharmacie active via first active: %s", pharmacy.id)
            return pharmacy
    
    # Aucune pharmacie trouvée
    logger.warning("⚠️ Aucune pharmacie active trouvée pour tenant %s, utilisateur %s",
                   getattr(current_tenant, "id", None), getattr(current_user, "email", None))
    
    # Pour les admins, on retourne None (peut-être qu'ils n'ont pas encore de pharmacie)
    if getattr(current_user, "role", None) in ADMIN_OVERRIDE_ROLES:
        return None
    
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": "no_active_pharmacy",
            "message": "Aucune pharmacie active trouvée pour cet utilisateur",
            "suggestion": "Créez une pharmacie ou contactez votre administrateur",
        },
    )


def get_current_active_branch(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_active_pharmacy),
    db: Session = Depends(get_db),
) -> Optional[Branch]:
    """
    Récupère la succursale active pour l'utilisateur connecté.
    
    Ordre de recherche :
    1. Header X-Branch-ID
    2. active_branch_id stocké dans l'utilisateur
    3. branch_id du JWT
    4. Succursale principale de la pharmacie active
    5. Première succursale active de la pharmacie active
    
    Retourne l'entité Branch SQLAlchemy.
    """
    # Super admin : accès global ou via header
    if _is_super_admin(current_user):
        header_branch_id = request.headers.get("X-Branch-ID")
        if header_branch_id:
            branch_uuid = _parse_uuid(header_branch_id)
            if branch_uuid:
                branch = db.query(Branch).filter(
                    Branch.id == branch_uuid,
                    Branch.is_active.is_(True),
                ).first()
                if branch:
                    return branch
        return None
    
    # Vérifier que l'utilisateur a un tenant
    if not current_tenant:
        logger.warning("⚠️ Utilisateur sans tenant pour get_current_active_branch: %s",
                       getattr(current_user, "email", None))
        return None
    
    # Vérifier qu'on a une pharmacie active
    if not current_pharmacy:
        logger.debug("ℹ️ Aucune pharmacie active, impossible de déterminer la branche")
        return None
    
    # 1. Vérifier le header X-Branch-ID
    header_branch_id = request.headers.get("X-Branch-ID")
    if header_branch_id:
        branch_uuid = _parse_uuid(header_branch_id)
        if branch_uuid:
            branch = db.query(Branch).filter(
                Branch.id == branch_uuid,
                Branch.tenant_id == current_tenant.id,
                Branch.parent_pharmacy_id == current_pharmacy.id,
                Branch.is_active.is_(True),
            ).first()
            
            if branch:
                if not can_user_access_branch(current_user, branch, db):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Vous n'avez pas accès à cette succursale",
                    )
                logger.debug("✅ Branche active via header X-Branch-ID: %s", branch.id)
                return branch
    
    # 2. Vérifier l'active_branch_id stocké dans l'utilisateur
    active_branch_id = getattr(current_user, "active_branch_id", None)
    if active_branch_id:
        branch = db.query(Branch).filter(
            Branch.id == active_branch_id,
            Branch.tenant_id == current_tenant.id,
            Branch.parent_pharmacy_id == current_pharmacy.id,
            Branch.is_active.is_(True),
        ).first()
        
        if branch:
            if not can_user_access_branch(current_user, branch, db):
                logger.warning("⚠️ Utilisateur %s n'a plus accès à sa branche active %s",
                              getattr(current_user, "email", None), active_branch_id)
                # Réinitialiser l'active_branch_id
                current_user.active_branch_id = None
                db.commit()
            else:
                logger.debug("✅ Branche active via user.active_branch_id: %s", branch.id)
                return branch
    
    # 3. Vérifier le branch_id du JWT
    token = _get_token_from_request(request)
    if token:
        payload = _decode_token_without_verification(token)
        branch_uuid = _parse_uuid(payload.get("branch_id"))
        if branch_uuid:
            branch = db.query(Branch).filter(
                Branch.id == branch_uuid,
                Branch.tenant_id == current_tenant.id,
                Branch.parent_pharmacy_id == current_pharmacy.id,
                Branch.is_active.is_(True),
            ).first()
            
            if branch:
                if not can_user_access_branch(current_user, branch, db):
                    logger.warning("⚠️ Utilisateur %s n'a pas accès à branch_id du JWT",
                                  getattr(current_user, "email", None))
                else:
                    # Mettre à jour l'active_branch_id de l'utilisateur
                    current_user.active_branch_id = branch.id
                    db.commit()
                    logger.debug("✅ Branche active via JWT branch_id: %s", branch.id)
                    return branch
    
    # 4. Succursale principale de la pharmacie active
    branch = db.query(Branch).filter(
        Branch.tenant_id == current_tenant.id,
        Branch.parent_pharmacy_id == current_pharmacy.id,
        Branch.is_active.is_(True),
        Branch.is_main_branch.is_(True),
    ).first()
    
    if branch:
        if can_user_access_branch(current_user, branch, db):
            # Mettre à jour l'active_branch_id de l'utilisateur
            current_user.active_branch_id = branch.id
            db.commit()
            logger.debug("✅ Branche active via is_main_branch: %s", branch.id)
            return branch
    
    # 5. Première succursale active de la pharmacie active
    branch = db.query(Branch).filter(
        Branch.tenant_id == current_tenant.id,
        Branch.parent_pharmacy_id == current_pharmacy.id,
        Branch.is_active.is_(True),
    ).first()
    
    if branch:
        if can_user_access_branch(current_user, branch, db):
            # Mettre à jour l'active_branch_id de l'utilisateur
            current_user.active_branch_id = branch.id
            db.commit()
            logger.debug("✅ Branche active via first active: %s", branch.id)
            return branch
    
    # Aucune branche trouvée - ce n'est pas une erreur, certaines pharmacies n'ont pas de branches
    logger.debug("ℹ️ Aucune branche active trouvée pour pharmacie %s", current_pharmacy.id)
    return None


def get_current_active_pharmacy_dict(
    pharmacy: Optional[Pharmacy] = Depends(get_current_active_pharmacy),
    current_user: User = Depends(get_current_active_user),
) -> Optional[Dict[str, Any]]:
    """
    Retourne la pharmacie active sous forme sérialisée (dictionnaire).
    """
    if _is_super_admin(current_user) and pharmacy is None:
        return {
            "id": None,
            "name": "Accès super admin",
            "is_global": True,
            "role": current_user.role,
        }
    
    if not pharmacy:
        return None
    
    return {
        "id": str(pharmacy.id),
        "name": getattr(pharmacy, "name", None),
        "license_number": getattr(pharmacy, "license_number", None),
        "pharmacy_code": getattr(pharmacy, "pharmacy_code", None),
        "address": getattr(pharmacy, "address", None),
        "city": getattr(pharmacy, "city", None),
        "country": getattr(pharmacy, "country", None),
        "phone": getattr(pharmacy, "phone", None),
        "email": getattr(pharmacy, "email", None),
        "is_main": getattr(pharmacy, "is_main", False),
        "is_active": getattr(pharmacy, "is_active", False),
        "config": getattr(pharmacy, "config", {}) or {},
    }


def get_current_active_branch_dict(
    branch: Optional[Branch] = Depends(get_current_active_branch),
    current_user: User = Depends(get_current_active_user),
) -> Optional[Dict[str, Any]]:
    """
    Retourne la succursale active sous forme sérialisée (dictionnaire).
    """
    if _is_super_admin(current_user) and branch is None:
        return {
            "id": None,
            "name": "Accès super admin",
            "is_global": True,
            "role": current_user.role,
        }
    
    if not branch:
        return None
    
    return {
        "id": str(branch.id),
        "name": branch.name,
        "code": branch.code,
        "address": branch.address,
        "city": branch.city,
        "phone": branch.phone,
        "email": branch.email,
        "is_main_branch": branch.is_main_branch,
        "is_active": branch.is_active,
        "parent_pharmacy_id": str(branch.parent_pharmacy_id) if branch.parent_pharmacy_id else None,
        "config": branch.config or {},
    }

def get_optional_token_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    Retourne l'utilisateur si un token valide est fourni, sinon None.
    Utile pour les endpoints qui supportent à la fois auth et non-auth.
    """
    token = _get_token_from_authorization_header(authorization)
    if not token:
        return None
    
    try:
        payload = security_verify_token(token)
        user_id = payload.get("sub")
        if not user_id:
            return None
        
        user = db.query(User).filter(User.id == user_id).first()
        if user and getattr(user, "actif", True):
            return user
        return None
    except Exception:
        return None


# =============================================================================
# 8. EXPORTS
# =============================================================================

__all__ = [
    "get_db",
    "oauth2_scheme",
    "get_current_user",
    "get_current_active_user",
    "get_optional_current_user",
    "get_super_admin_user",
    "get_current_tenant",
    "get_current_tenant_with_fallback",
    "get_tenant_id_from_request",
    "require_active_subscription",
    "check_admin_limits",
    "get_current_pharmacy_entity",
    "get_current_branch_entity",
    "get_current_pharmacy",
    "get_current_branch",
    "require_pharmacy_access",
    "require_branch_access",
    "get_pharmacy_or_main",
    "can_user_access_pharmacy",
    "can_user_access_branch",
    "require_role",
    "require_permission",
    "get_authenticated_context",
    "get_current_user_with_context",
    "get_pagination_params",
    "get_date_range_params",
    "subscription_required",
    "get_current_admin_user",
    "get_current_active_pharmacy",
    "get_current_active_branch",
    "get_current_active_pharmacy_dict",
    "get_current_active_branch_dict",
    "get_optional_token_user",
    "_get_token_from_authorization_header",
    "get_user_branches",
    "set_active_branch",
    "verify_branch_access",
    "can_user_access_branch",
]
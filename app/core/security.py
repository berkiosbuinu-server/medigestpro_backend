# app/core/security.py (version corrigée)
from datetime import datetime, timedelta
from typing import Optional, List, Callable, Any, Dict
from functools import wraps
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
import re
import secrets
import string
import inspect
from app.models.user import User
from app.db.session import get_db
from app.core.config import settings

# ===========================================
# GESTION DES MOTS DE PASSE
# ===========================================
import logging

logger = logging.getLogger("uvicorn.error")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: object) -> str:
    pw = "" if password is None else (password if isinstance(password, str) else str(password))
    pw = pw.strip()

    # bcrypt limite 72 bytes
    pw_len = len(pw.encode("utf-8"))
    if pw_len > 72:
        logger.error("bcrypt password too long: bytes=%s", pw_len)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mot de passe trop long (max ~72 bytes avec bcrypt)."
        )

    return pwd_context.hash(pw)

def hash_password(password: object) -> str:
    return get_password_hash(password)

def verify_password(plain_password: object, hashed_password: str) -> bool:
    pw = "" if plain_password is None else (plain_password if isinstance(plain_password, str) else str(plain_password))
    pw = pw.strip()
    return pwd_context.verify(pw, hashed_password)

# ===========================================
# JWT TOKENS - FONCTIONS MANQUANTES
# ===========================================
def verify_token(token: str) -> Dict[str, Any]:
    """Vérifie et décode un token JWT"""
    try:
        payload = jwt.decode(
            token, 
            settings.SECRET_KEY, 
            algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token JWT invalide: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Erreur de validation du token: {str(e)}"
        )

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Crée un token JWT d'accès"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Crée un token JWT de rafraîchissement"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def decode_token(token: str) -> Dict[str, Any]:
    """Décode un token JWT"""
    return verify_token(token)

def decode_access_token(token: str) -> Dict[str, Any]:
    """Décode un token d'accès JWT (alias pour decode_token avec vérification du type)"""
    try:
        payload = decode_token(token)
        token_type = payload.get("type")
        
        if token_type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token invalide: ce n'est pas un token d'accès",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        return payload
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Erreur de décodage du token d'accès: {str(e)}"
        )

# ===========================================
# CRÉATION DE LA PAIRE DE TOKENS (FONCTION AJOUTÉE)
# ===========================================
def create_token_pair(
    user: User, 
    subscription_active: bool = True, 
    pharmacy_id: Optional[str] = None
) -> Dict[str, Any]:
    """Crée une paire de tokens (access + refresh) avec le rôle inclus"""
    
    # 🔥 DONNÉES DU TOKEN D'ACCÈS - INCLURE LE RÔLE ICI
    access_token_data = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,  # ← CRUCIAL: inclure le rôle
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "subscription_active": subscription_active,
        "is_impersonation": False
    }
    
    if pharmacy_id:
        access_token_data["pharmacy_id"] = pharmacy_id
    
    # Créer le token d'accès
    access_token = create_access_token(
        data=access_token_data,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    
    # 🔥 DONNÉES DU TOKEN DE RAFRAÎCHISSEMENT
    refresh_token_data = {
        "sub": str(user.id),
        "role": user.role,  # Inclure le rôle aussi dans le refresh token
        "type": "refresh"
    }
    
    # Créer le token de rafraîchissement
    refresh_token = create_refresh_token(
        data=refresh_token_data,
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    )
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

# ===========================================
# OAUTH2 & AUTHENTIFICATION
# ===========================================
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """Récupère l'utilisateur courant depuis le token avec vérification d'abonnement"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Impossible de valider les informations d'identification",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    subscription_expired_exception = HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Votre abonnement a expiré. Veuillez renouveler votre abonnement.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = verify_token(token)
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")
        
        if user_id is None:
            raise credentials_exception
            
        if token_type != "access":
            raise credentials_exception
            
    except HTTPException:
        raise
    except Exception:
        raise credentials_exception

    # Récupérer l'utilisateur depuis la base de données
    user = db.query(User).filter(
        User.id == user_id,
        User.actif == True
    ).first()

    if user is None:
        raise credentials_exception
    
    # ===========================================
    # NOUVEAU : VÉRIFICATION DE L'ABONNEMENT
    # ===========================================
    from app.core.branch_scope import is_global_actor
    if user.tenant_id and not is_global_actor(user):
        from app.models.tenant import Tenant
        from datetime import datetime
        
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        
        if tenant:
            # Vérifier la période d'essai
            if tenant.current_plan == "trial" and tenant.trial_end_date:
                if tenant.trial_end_date < datetime.utcnow():
                    raise subscription_expired_exception
            
            # Vérifier l'abonnement actif via le service
            from app.api.v1.auth import is_subscription_active
            if not is_subscription_active(db, str(user.tenant_id)):
                raise subscription_expired_exception
    
    return user

async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Vérifie que l'utilisateur est actif"""
    if not current_user.actif:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte utilisateur désactivé"
        )
    return current_user

async def get_current_superuser(current_user: User = Depends(get_current_user)) -> User:
    """Vérifie que l'utilisateur est super admin"""
    if current_user.role not in ["super_admin", "superadmin", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Privilèges insuffisants. Rôle requis: super_admin"
        )
    return current_user

# app/core/security.py (section corrigée pour require_permission)

# ===========================================
# PERMISSIONS ET RÔLES
# ===========================================
ROLE_PERMISSIONS = {
    "super_admin": ["*"],  # Toutes les permissions
    "superadmin": ["*"],   # Alias
    "admin": ["*"],
    "pharmacien": ["ventes:create", "ventes:read", "ventes:update", "inventory:read", 
                   "inventory:update", "clients:read", "clients:create", "reports:read"],
    "gerant": ["ventes:create", "ventes:read", "ventes:update", "ventes:delete", 
               "inventory:*", "clients:*", "reports:*"],
    "vendeur": ["ventes:create", "ventes:read", "clients:read"],
    "caissier": ["ventes:create", "ventes:read"],
}

def has_permission(user_role: str, permission: str) -> bool:
    """Vérifie si un rôle a une permission"""
    permissions = ROLE_PERMISSIONS.get(user_role, [])
    
    # Si l'utilisateur a "*", il a toutes les permissions
    if "*" in permissions:
        return True
    
    # Vérifier la permission exacte
    if permission in permissions:
        return True
    
    # Vérifier les permissions de module (ex: "ventes:*")
    if ":" in permission:
        module = permission.split(":")[0] + ":*"
        return module in permissions
    
    return False


def require_permission(permission_code: str):
    """
    Décorateur pour vérifier les permissions
    Supporte les fonctions synchrones ET asynchrones
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Récupérer l'utilisateur depuis les kwargs (paramètres nommés)
            current_user = kwargs.get('current_user')
            
            # Si pas trouvé dans kwargs, chercher dans les args (paramètres positionnels)
            if not current_user:
                for arg in args:
                    if isinstance(arg, User):
                        current_user = arg
                        break
            
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Utilisateur non authentifié"
                )
            
            # Vérifier la permission
            if not has_permission(current_user.role, permission_code):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Permission '{permission_code}' requise. Rôle: {current_user.role}"
                )
            
            # Exécuter la fonction originale (asynchrone)
            return await func(*args, **kwargs)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # Récupérer l'utilisateur depuis les kwargs (paramètres nommés)
            current_user = kwargs.get('current_user')
            
            # Si pas trouvé dans kwargs, chercher dans les args (paramètres positionnels)
            if not current_user:
                for arg in args:
                    if isinstance(arg, User):
                        current_user = arg
                        break
            
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Utilisateur non authentifié"
                )
            
            # Vérifier la permission
            if not has_permission(current_user.role, permission_code):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Permission '{permission_code}' requise. Rôle: {current_user.role}"
                )
            
            # Exécuter la fonction originale (synchrone)
            return func(*args, **kwargs)
        
        # Déterminer si la fonction est asynchrone ou synchrone
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def require_role(allowed_roles: List[str]):
    """
    Décorateur pour vérifier le rôle
    Supporte les fonctions synchrones ET asynchrones
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Récupérer l'utilisateur depuis les kwargs (paramètres nommés)
            current_user = kwargs.get('current_user')
            
            # Si pas trouvé dans kwargs, chercher dans les args (paramètres positionnels)
            if not current_user:
                for arg in args:
                    if isinstance(arg, User):
                        current_user = arg
                        break
            
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Utilisateur non authentifié"
                )
            
            # Vérifier le rôle
            if current_user.role not in allowed_roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Rôles autorisés: {allowed_roles}. Votre rôle: {current_user.role}"
                )
            
            # Exécuter la fonction originale (asynchrone)
            return await func(*args, **kwargs)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # Récupérer l'utilisateur depuis les kwargs (paramètres nommés)
            current_user = kwargs.get('current_user')
            
            # Si pas trouvé dans kwargs, chercher dans les args (paramètres positionnels)
            if not current_user:
                for arg in args:
                    if isinstance(arg, User):
                        current_user = arg
                        break
            
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Utilisateur non authentifié"
                )
            
            # Vérifier le rôle
            if current_user.role not in allowed_roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Rôles autorisés: {allowed_roles}. Votre rôle: {current_user.role}"
                )
            
            # Exécuter la fonction originale (synchrone)
            return func(*args, **kwargs)
        
        # Déterminer si la fonction est asynchrone ou synchrone
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator
# ===========================================
# VALIDATION
# ===========================================
def validate_password_strength(password: str) -> Dict[str, bool]:
    """Valide la force d'un mot de passe"""
    validation = {
        "length": len(password) >= 8,
        "uppercase": any(c.isupper() for c in password),
        "lowercase": any(c.islower() for c in password),
        "digit": any(c.isdigit() for c in password),
        "special": any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?`~' for c in password),
    }
    return validation

def is_password_strong(password: str) -> bool:
    """Vérifie si un mot de passe est fort"""
    validation = validate_password_strength(password)
    return all(validation.values())

def validate_email(email: str) -> bool:
    """Valide un email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def validate_phone(phone: str) -> bool:
    """Valide un numéro de téléphone"""
    cleaned = re.sub(r'[^\d+]', '', phone)
    pattern = r'^(\+?243|0)[0-9]{9}$'
    return bool(re.match(pattern, cleaned))

# ===========================================
# GÉNÉRATION
# ===========================================
def generate_api_key(length: int = 32) -> str:
    """Génère une clé API sécurisée"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def generate_verification_token(length: int = 32) -> str:
    """Génère un token de vérification"""
    return secrets.token_urlsafe(length)

def generate_password_reset_token(length: int = 32) -> str:
    """Génère un token de réinitialisation de mot de passe"""
    return secrets.token_urlsafe(length)

def generate_invitation_code(length: int = 8) -> str:
    """Génère un code d'invitation"""
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

# ===========================================
# FONCTIONS D'AUTHENTIFICATION
# ===========================================
def authenticate_user(db: Session, email: str, password: str, tenant_id: str = None) -> Optional[User]:
    """Authentifie un utilisateur"""
    query = db.query(User).filter(User.email == email, User.actif == True)
    
    if tenant_id:
        query = query.filter(User.tenant_id == tenant_id)
    
    user = query.first()
    
    if not user:
        return None
    
    if not verify_password(password, user.password_hash):
        return None
    
    return user

async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm,
    db: Session = Depends(get_db),
    tenant_id: str = None
) -> Dict[str, Any]:
    """Connexion et génération de token"""
    user = authenticate_user(db, form_data.username, form_data.password, tenant_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Utiliser create_token_pair pour générer les tokens
    token_pair = create_token_pair(user=user, subscription_active=True)
    
    return {
        **token_pair,
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "nom_complet": user.nom_complet,
            "role": user.role,
            "tenant_id": str(user.tenant_id) if user.tenant_id else None
        }
    }

def require_active_subscription(func: Callable) -> Callable:
    """
    Décorateur pour vérifier que l'abonnement est actif
    """
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        current_user = kwargs.get('current_user')
        
        if not current_user:
            for arg in args:
                if isinstance(arg, User):
                    current_user = arg
                    break
        
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Utilisateur non authentifié"
            )
        
        from app.core.branch_scope import is_global_actor
        if current_user.tenant_id and not is_global_actor(current_user):
            from app.db.session import SessionLocal
            from app.api.v1.auth import is_subscription_active
            
            db = SessionLocal()
            try:
                subscription_active = is_subscription_active(db, str(current_user.tenant_id))
                if not subscription_active:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={
                            "error": "subscription_expired",
                            "message": "Votre abonnement a expiré. Veuillez renouveler votre abonnement.",
                            "requires_relogin": True
                        }
                    )
            finally:
                db.close()
        
        return await func(*args, **kwargs)
    
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        current_user = kwargs.get('current_user')
        
        if not current_user:
            for arg in args:
                if isinstance(arg, User):
                    current_user = arg
                    break
        
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Utilisateur non authentifié"
            )
        
        if current_user.tenant_id:
            from app.db.session import SessionLocal
            from app.api.v1.auth import is_subscription_active
            
            db = SessionLocal()
            try:
                subscription_active = is_subscription_active(db, str(current_user.tenant_id))
                if not subscription_active:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={
                            "error": "subscription_expired",
                            "message": "Votre abonnement a expiré. Veuillez renouveler votre abonnement.",
                            "requires_relogin": True
                        }
                    )
            finally:
                db.close()
        
        return func(*args, **kwargs)
    
    if inspect.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper

# ===========================================
# UTILITAIRES
# ===========================================
def check_password_reuse(new_password: str, old_hashed_password: str) -> bool:
    """Vérifie si le nouveau mot de passe a déjà été utilisé"""
    return verify_password(new_password, old_hashed_password)

def sanitize_input(input_string: str) -> str:
    """Nettoie une chaîne de caractères pour prévenir les injections"""
    if not input_string:
        return ""
    
    dangerous_chars = ["<", ">", "'", "\"", ";", "--", "/*", "*/"]
    for char in dangerous_chars:
        input_string = input_string.replace(char, "")
    
    return input_string.strip()
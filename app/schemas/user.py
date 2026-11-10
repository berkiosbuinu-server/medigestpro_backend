# app/schemas/user.py
from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    field_validator,
    model_validator,
    ConfigDict,
    FieldValidationInfo
)
from typing import Optional, Dict, List, Any, ClassVar
from datetime import datetime
from uuid import UUID
import re
from enum import Enum


# =========================
# Enums pour les types constants
# =========================

class UserRole(str, Enum):
    """Rôles disponibles dans l'application"""
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    PHARMACIST = "pharmacien"
    PREPARATOR = "preparateur"
    CASHIER = "caissier"
    SELLER = "vendeur"
    ACCOUNTANT = "comptable"
    STOCK_MANAGER = "stockiste"

class Permission(str, Enum):
    """Permissions disponibles"""
    GESTION_STOCK = "gestion_stock"
    GESTION_VENTES = "gestion_ventes"
    GESTION_CLIENTS = "gestion_clients"
    GESTION_FOURNISSEURS = "gestion_fournisseurs"
    RAPPORTS = "rapports"
    CONFIGURATION = "configuration"
    GESTION_UTILISATEURS = "gestion_utilisateurs"
    GESTION_CAISSE = "gestion_caisse"


# =========================
# Base - Modèle de base
# =========================

class UserBase(BaseModel):
    """Schéma de base pour tous les modèles d'utilisateur"""
    
    email: EmailStr = Field(
        ...,
        description="Adresse email valide et unique",
        examples=["pharmacie.central@example.com"]
    )
    
    nom_complet: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="Nom complet de l'utilisateur",
        examples=["Dr. Jean Dupont"]
    )
    
    telephone: Optional[str] = Field(
        None,
        min_length=9,
        max_length=20,
        description="Numéro de téléphone au format congolais (+243...)",
        examples=["+243811223344"]
    )
    
    poste: Optional[str] = Field(
        None,
        max_length=100,
        description="Poste occupé dans la pharmacie",
        examples=["Pharmacien en chef", "Responsable stock"]
    )
    
    model_config = ConfigDict(
        use_enum_values=True,
        from_attributes=True,
        extra="forbid"
    )

    @field_validator("telephone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        """Valide le format du numéro de téléphone congolais"""
        if not v:
            return v
        
        # Normaliser: retirer les espaces, tirets, etc.
        v = re.sub(r'[\s\-\(\)]+', '', v)
        
        # Formats acceptés: +2438XXXXXXXX, 0811XXXXXX, 0811XXXXXX
        pattern = r'^(\+?243|0)?[0-9]{9}$'
        
        if not re.match(pattern, v):
            raise ValueError(
                "Format de téléphone invalide. "
                "Utilisez: +243811223344 ou 0811223344"
            )
        
        # Normaliser vers format +243
        if v.startswith('0'):
            v = '+243' + v[1:]
        elif not v.startswith('+243'):
            v = '+243' + v
        
        return v


# =========================
# Création utilisateur (admin)
# =========================

class UserCreate(UserBase):
    """Schéma pour la création d'un utilisateur par un administrateur"""
    
    # Configuration pour masquer certains champs dans la réponse
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "nouveau.pharmacien@example.com",
                "nom_complet": "Dr. Marie Laurent",
                "telephone": "+243899887766",
                "poste": "Pharmacien adjoint",
                "password": "Mot2Passe$ecure123",
                "branch_id": "00000000-0000-0000-0000-000000000000",
                "role": "pharmacien"
            }
        }
    )
    
    password: str = Field(
        ...,
        min_length=8,
        max_length=100,
        description="Mot de passe sécurisé (min 8 caractères, 1 majuscule, 1 chiffre)",
        examples=["Mot2Passe$ecure123"]
    )

    branch_id: UUID = Field(
        ...,
        description="Branche d'affectation de l'utilisateur (obligatoire)"
    )
    
    role: UserRole = Field(
        default=UserRole.PHARMACIST,
        description="Rôle de l'utilisateur dans le système"
    )
    
    permissions: Dict[Permission, bool] = Field(
        default_factory=lambda: {
            Permission.GESTION_STOCK: True,
            Permission.GESTION_VENTES: True,
            Permission.GESTION_CLIENTS: True,
            Permission.GESTION_FOURNISSEURS: True,
            Permission.RAPPORTS: True,
            Permission.CONFIGURATION: False,
            Permission.GESTION_UTILISATEURS: False,
            Permission.GESTION_CAISSE: True,
        },
        description="Permissions spécifiques de l'utilisateur"
    )
    
    # Définition des permissions par rôle
    ROLE_PERMISSIONS: ClassVar[Dict[UserRole, Dict[Permission, bool]]] = {
        UserRole.SUPER_ADMIN: {perm: True for perm in Permission},
        UserRole.ADMIN: {perm: True for perm in Permission},
        UserRole.PHARMACIST: {
            Permission.GESTION_STOCK: True,
            Permission.GESTION_VENTES: True,
            Permission.GESTION_CLIENTS: True,
            Permission.GESTION_FOURNISSEURS: True,
            Permission.RAPPORTS: True,
            Permission.CONFIGURATION: False,
            Permission.GESTION_UTILISATEURS: False,
            Permission.GESTION_CAISSE: True,
        },
        UserRole.CASHIER: {
            Permission.GESTION_STOCK: False,
            Permission.GESTION_VENTES: True,
            Permission.GESTION_CLIENTS: True,
            Permission.GESTION_FOURNISSEURS: False,
            Permission.RAPPORTS: False,
            Permission.CONFIGURATION: False,
            Permission.GESTION_UTILISATEURS: False,
            Permission.GESTION_CAISSE: True,
        },
        UserRole.SELLER: {
            Permission.GESTION_STOCK: False,
            Permission.GESTION_VENTES: True,
            Permission.GESTION_CLIENTS: True,
            Permission.GESTION_FOURNISSEURS: False,
            Permission.RAPPORTS: False,
            Permission.CONFIGURATION: False,
            Permission.GESTION_UTILISATEURS: False,
            Permission.GESTION_CAISSE: True,
        }
    }
    
    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Valide la force du mot de passe"""
        if len(v) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères")
        
        if not any(c.isupper() for c in v):
            raise ValueError("Le mot de passe doit contenir au moins une majuscule")
        
        if not any(c.isdigit() for c in v):
            raise ValueError("Le mot de passe doit contenir au moins un chiffre")
        
        if not any(c.isalnum() and not c.isalnum() for c in v):
            # Optionnel: vérification de caractère spécial
            pass
        
        return v
    
    @model_validator(mode="after")
    def adjust_permissions_by_role(self) -> "UserCreate":
        """Ajuste automatiquement les permissions en fonction du rôle"""
        if self.role in self.ROLE_PERMISSIONS:
            # Fusionner les permissions par défaut du rôle avec celles fournies
            role_perms = self.ROLE_PERMISSIONS[self.role]
            for perm in Permission:
                if perm not in self.permissions:
                    self.permissions[perm] = role_perms.get(perm, False)
        
        return self


# =========================
# Inscription libre
# =========================

class UserRegister(UserBase):
    """Schéma pour l'inscription libre d'un utilisateur"""
    
    password: str = Field(
        ...,
        min_length=8,
        max_length=100,
        description="Mot de passe sécurisé"
    )
    
    code_invitation: Optional[str] = Field(
        None,
        max_length=50,
        description="Code d'invitation optionnel"
    )
    
    confirm_password: str = Field(
        ...,
        description="Confirmation du mot de passe"
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "nouveau@example.com",
                "nom_complet": "Pierre Martin",
                "telephone": "+243811223344",
                "poste": "Assistant pharmacie",
                "password": "MonMot2Passe123",
                "confirm_password": "MonMot2Passe123",
                "code_invitation": "PHARMA2024"
            }
        }
    )
    
    @model_validator(mode="after")
    def validate_passwords_match(self) -> "UserRegister":
        """Vérifie que le mot de passe et la confirmation correspondent"""
        if self.password != self.confirm_password:
            raise ValueError("Les mots de passe ne correspondent pas")
        return self


# =========================
# Mise à jour utilisateur
# =========================

class UserUpdate(BaseModel):
    """Schéma pour la mise à jour partielle d'un utilisateur"""
    
    nom_complet: Optional[str] = Field(
        None,
        min_length=2,
        max_length=100
    )
    
    telephone: Optional[str] = Field(
        None,
        min_length=9,
        max_length=20
    )
    
    poste: Optional[str] = Field(
        None,
        max_length=100
    )
    
    role: Optional[UserRole] = Field(
        None,
        description="Rôle de l'utilisateur"
    )
    
    permissions: Optional[Dict[Permission, bool]] = Field(
        None,
        description="Permissions mises à jour"
    )
    
    is_active: Optional[bool] = Field(
        None,
        description="Statut d'activation du compte"
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "nom_complet": "Dr. Jean Dupont (Modifié)",
                "telephone": "+243899887766",
                "poste": "Pharmacien principal",
                "role": "pharmacien",
                "is_active": True
            }
        }
    )

class AdminChangePasswordRequest(BaseModel):
    """Schéma pour le changement de mot de passe par un admin"""
    new_password: str = Field(..., min_length=8, description="Nouveau mot de passe")
    
    @field_validator('new_password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('Le mot de passe doit contenir au moins 8 caractères')
        return v

# =========================
# Authentification
# =========================

class UserLogin(BaseModel):
    """Schéma pour la connexion utilisateur"""
    
    email: EmailStr = Field(
        ...,
        description="Adresse email de connexion"
    )
    
    password: str = Field(
        ...,
        description="Mot de passe"
    )
    
    tenant_id: Optional[UUID] = Field(
        None,
        description="ID du tenant (optionnel pour multi-tenant)"
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "pharmacien@example.com",
                "password": "MonMot2Passe123"
            }
        }
    )


class TokenResponse(BaseModel):
    """Schéma de réponse pour l'authentification JWT"""
    
    access_token: str = Field(
        ...,
        description="Token JWT d'accès"
    )
    
    token_type: str = Field(
        default="bearer",
        description="Type de token"
    )
    
    expires_in: int = Field(
        default=3600,
        description="Durée de validité en secondes"
    )
    
    refresh_token: Optional[str] = Field(
        None,
        description="Token de rafraîchissement"
    )
    
    user: Dict[str, Any] = Field(
        ...,
        description="Informations de l'utilisateur authentifié"
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
                "token_type": "bearer",
                "expires_in": 3600,
                "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
                "user": {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "email": "pharmacien@example.com",
                    "nom_complet": "Dr. Jean Dupont",
                    "role": "pharmacien"
                }
            }
        }
    )


# =========================
# Réponses et profils
# =========================

class UserProfile(BaseModel):
    """Schéma pour le profil utilisateur (réponse publique)"""
    
    id: UUID = Field(..., description="ID unique de l'utilisateur")
    nom_complet: str = Field(..., description="Nom complet")
    email: EmailStr = Field(..., description="Email")
    telephone: Optional[str] = Field(None, description="Téléphone")
    poste: Optional[str] = Field(None, description="Poste")
    role: UserRole = Field(..., description="Rôle")
    is_active: bool = Field(..., description="Compte actif")
    is_verified: bool = Field(..., description="Email vérifié")
    pharmacie_nom: Optional[str] = Field(None, description="Nom de la pharmacie")
    derniere_connexion: Optional[datetime] = Field(None, description="Dernière connexion")
    date_creation: datetime = Field(..., description="Date de création")
    
    model_config = ConfigDict(from_attributes=True)


class UserListSchema(BaseModel):
    """Schéma pour lister les utilisateurs (correspond au modèle réel)"""
    id: UUID = Field(..., description="ID unique de l'utilisateur")
    tenant_id: UUID = Field(..., description="ID du tenant")
    nom_complet: str = Field(..., description="Nom complet")
    email: EmailStr = Field(..., description="Email")
    role: str = Field(..., description="Rôle")
    actif: bool = Field(..., description="Compte actif")
    telephone: Optional[str] = Field(None, description="Téléphone")
    adresse: Optional[str] = Field(None, description="Adresse")
    permissions: Optional[Dict] = Field(default_factory=dict)
    date_creation: Optional[datetime] = Field(None, description="Date de création")
    last_login: Optional[datetime] = Field(None, description="Dernière connexion")
    
    model_config = ConfigDict(from_attributes=True)


class UserInDB(UserProfile):
    """Schéma utilisateur complet pour la base de données"""
    
    tenant_id: UUID = Field(..., description="ID du tenant")
    username: Optional[str] = Field(None, description="Nom d'utilisateur")
    password_hash: str = Field(..., description="Hash du mot de passe", exclude=True)
    permissions: Dict[Permission, bool] = Field(..., description="Permissions")
    date_creation_mdp: Optional[datetime] = Field(None, description="Date création mdp")
    token_verification: Optional[str] = Field(None, description="Token vérification", exclude=True)
    date_modification: datetime = Field(..., description="Date de modification")
    
    model_config = ConfigDict(from_attributes=True)


class UserResponse(BaseModel):
    """Réponse standard pour les opérations utilisateur"""
    
    message: str = Field(..., description="Message de statut")
    user: UserProfile = Field(..., description="Utilisateur concerné")
    login_url: Optional[str] = Field(
        None,
        description="URL de connexion (pour les nouveaux comptes)"
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Utilisateur créé avec succès",
                "user": {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "nom_complet": "Dr. Jean Dupont",
                    "email": "pharmacien@example.com",
                    "role": "pharmacien",
                    "is_active": True,
                    "is_verified": False,
                    "date_creation": "2024-01-15T10:30:00"
                },
                "login_url": "https://app.medigest.com/login"
            }
        }
    )


class UserListResponse(BaseModel):
    """Réponse pour les listes paginées d'utilisateurs"""
    
    total: int = Field(..., description="Nombre total d'utilisateurs")
    page: int = Field(..., description="Page actuelle")
    limit: int = Field(..., description="Nombre d'éléments par page")
    users: List[UserProfile] = Field(..., description="Liste des utilisateurs")
    has_next: bool = Field(..., description="Page suivante disponible")
    has_prev: bool = Field(..., description="Page précédente disponible")
    
    @model_validator(mode="after")
    def calculate_pagination(self) -> "UserListResponse":
        """Calcule les indicateurs de pagination"""
        total_pages = (self.total + self.limit - 1) // self.limit
        self.has_next = self.page < total_pages
        self.has_prev = self.page > 1
        return self


# =========================
# Sécurité et mot de passe
# =========================

class UserPasswordChange(BaseModel):
    """Schéma pour le changement de mot de passe"""
    
    current_password: str = Field(..., description="Mot de passe actuel")
    new_password: str = Field(
        ...,
        min_length=8,
        max_length=100,
        description="Nouveau mot de passe"
    )
    
    confirm_password: str = Field(..., description="Confirmation du nouveau mot de passe")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "current_password": "AncienMdp123",
                "new_password": "NouveauMdp456",
                "confirm_password": "NouveauMdp456"
            }
        }
    )
    
    @model_validator(mode="after")
    def validate_password_change(self) -> "UserPasswordChange":
        """Valide le changement de mot de passe"""
        # Vérifier que les nouveaux mots de passe correspondent
        if self.new_password != self.confirm_password:
            raise ValueError("Les nouveaux mots de passe ne correspondent pas")
        
        # Vérifier que le nouveau mot de passe est différent
        if self.current_password == self.new_password:
            raise ValueError("Le nouveau mot de passe doit être différent de l'actuel")
        
        # Valider la force du nouveau mot de passe
        if len(self.new_password) < 8:
            raise ValueError("Le mot de passe doit contenir au moins 8 caractères")
        
        if not any(c.isupper() for c in self.new_password):
            raise ValueError("Le mot de passe doit contenir au moins une majuscule")
        
        if not any(c.isdigit() for c in self.new_password):
            raise ValueError("Le mot de passe doit contenir au moins un chiffre")
        
        return self


class UserResetPassword(BaseModel):
    """Schéma pour la réinitialisation de mot de passe"""
    
    email: EmailStr = Field(..., description="Email pour la réinitialisation")
    token: Optional[str] = Field(
        None,
        description="Token de réinitialisation (étape 2)"
    )
    new_password: Optional[str] = Field(
        None,
        min_length=8,
        max_length=100,
        description="Nouveau mot de passe (étape 2)"
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "example_step1": {
                "email": "pharmacien@example.com"
            },
            "example_step2": {
                "email": "pharmacien@example.com",
                "token": "abc123def456",
                "new_password": "NouveauMdp123"
            }
        }
    )
    
    @model_validator(mode="after")
    def validate_reset_steps(self) -> "UserResetPassword":
        """Valide les étapes de réinitialisation"""
        # Étape 1: demande de réinitialisation (email seulement)
        # Étape 2: confirmation avec token et nouveau mot de passe
        if self.token:
            if not self.new_password:
                raise ValueError("Le nouveau mot de passe est requis avec le token")
        return self


# =========================
# Export des schémas principaux
# =========================

__all__ = [
    "UserRole",
    "Permission",
    "UserBase",
    "UserCreate",
    "UserRegister",
    "UserUpdate",
    "UserLogin",
    "TokenResponse",  # Ajouté pour résoudre l'erreur d'import
    "UserProfile",
    "UserInDB",
    "UserResponse",
    "UserListResponse",
    "UserPasswordChange",
    "UserResetPassword",
]
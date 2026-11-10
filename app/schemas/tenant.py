# app/schemas/tenant.py
from pydantic import BaseModel, EmailStr, field_validator, Field, model_validator, ConfigDict
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, date
from uuid import UUID
import re
from decimal import Decimal
from enum import Enum
from app.models.tenant import TenantStatus, PharmacyType, BillingPeriod


# =======================
# ENUMS POUR VALIDATION
# =======================
class TenantStatusEnum(str, Enum):
    draft = "draft"
    trial = "trial"
    active = "active"
    suspended = "suspended"
    expired = "expired"
    cancelled = "cancelled"
    archived = "archived"


class PharmacyTypeEnum(str, Enum):
    officine = "officine"
    hospitaliere = "hospitaliere"
    veterinaire = "veterinaire"
    parapharmacie = "parapharmacie"
    grossiste = "grossiste"
    autre = "autre"


class BillingPeriodEnum(str, Enum):
    monthly = "monthly"
    quarterly = "quarterly"
    annual = "annual"
    lifetime = "lifetime"


class PaymentMethodEnum(str, Enum):
    mobile_money = "mobile_money"
    bank_transfer = "bank_transfer"
    card = "card"
    cash = "cash"
    other = "other"


# =======================
# SCHÉMAS DE BASE
# =======================
class TenantBase(BaseModel):
    """Schéma de base pour un tenant (pharmacie)"""
    nom_pharmacie: str = Field(..., min_length=2, max_length=200, description="Nom officiel de la pharmacie")
    nom_commercial: Optional[str] = Field(None, max_length=200, description="Nom commercial (si différent)")
    slogan: Optional[str] = Field(None, max_length=500, description="Slogan de la pharmacie")
    description: Optional[str] = Field(None, description="Description détaillée de la pharmacie")
    
    # Contact principal
    email_admin: EmailStr = Field(..., description="Email de l'administrateur principal")
    email_contact: Optional[EmailStr] = Field(None, description="Email de contact général")
    
    # Téléphones
    telephone_principal: str = Field(..., min_length=9, max_length=20, description="Téléphone principal")
    telephone_secondaire: Optional[str] = Field(None, min_length=9, max_length=20, description="Téléphone secondaire")
    telephone_mobile: Optional[str] = Field(None, min_length=9, max_length=20, description="Téléphone mobile")
    fax: Optional[str] = Field(None, max_length=20, description="Numéro de fax")
    
    # Adresse
    adresse_ligne1: str = Field(..., max_length=200, description="Adresse ligne 1")
    adresse_ligne2: Optional[str] = Field(None, max_length=200, description="Adresse ligne 2")
    quartier: Optional[str] = Field(None, max_length=100, description="Quartier")
    commune: Optional[str] = Field(None, max_length=100, description="Commune")
    ville: str = Field(..., max_length=100, description="Ville")
    province: Optional[str] = Field(None, max_length=100, description="Province")
    pays: str = Field(default="République Démocratique du Congo", max_length=100, description="Pays")
    code_postal: Optional[str] = Field(None, max_length=20, description="Code postal")
    
    # Coordonnées GPS
    latitude: Optional[float] = Field(None, ge=-90, le=90, description="Latitude")
    longitude: Optional[float] = Field(None, ge=-180, le=180, description="Longitude")
    
    # Informations légales
    nom_entreprise: Optional[str] = Field(None, max_length=200, description="Nom légal de l'entreprise")
    forme_juridique: Optional[str] = Field(None, max_length=50, description="Forme juridique (SARL, SA, EI, etc.)")
    registre_commerce: Optional[str] = Field(None, max_length=50, description="Numéro de registre de commerce")
    numero_ifu: Optional[str] = Field(None, max_length=50, description="Identification Fiscale Unique")
    numero_national: Optional[str] = Field(None, max_length=50, description="Numéro national d'entreprise")
    numero_agrement: Optional[str] = Field(None, max_length=50, description="Numéro d'agrément pharmaceutique")
    date_agrement: Optional[date] = Field(None, description="Date d'agrément")
    
    # Responsables
    nom_proprietaire: str = Field(..., max_length=150, description="Nom du propriétaire")
    prenom_proprietaire: Optional[str] = Field(None, max_length=150, description="Prénom du propriétaire")
    email_proprietaire: Optional[EmailStr] = Field(None, description="Email du propriétaire")
    telephone_proprietaire: Optional[str] = Field(None, min_length=9, max_length=20, description="Téléphone du propriétaire")
    
    nom_pharmacien: Optional[str] = Field(None, max_length=150, description="Nom du pharmacien responsable")
    prenom_pharmacien: Optional[str] = Field(None, max_length=150, description="Prénom du pharmacien responsable")
    numero_ordre: Optional[str] = Field(None, max_length=50, description="Numéro d'ordre du pharmacien")
    email_pharmacien: Optional[EmailStr] = Field(None, description="Email du pharmacien")
    telephone_pharmacien: Optional[str] = Field(None, min_length=9, max_length=20, description="Téléphone du pharmacien")
    
    # Caractéristiques
    type_pharmacie: PharmacyTypeEnum = Field(default=PharmacyTypeEnum.officine, description="Type de pharmacie")
    specialite: Optional[str] = Field(None, max_length=100, description="Spécialité de la pharmacie")
    superficie: Optional[float] = Field(None, ge=0, description="Superficie en m²")
    nombre_employes: Optional[int] = Field(None, ge=1, description="Nombre d'employés")
    nombre_guichets: Optional[int] = Field(None, ge=1, description="Nombre de guichets")
    annee_creation: Optional[int] = Field(None, ge=1900, le=datetime.now().year, description="Année de création")
    
    # Configuration système
    devise: str = Field(default="CDF", min_length=3, max_length=3, description="Devise principale (CDF, USD, EUR)")
    devise_symbol: Optional[str] = Field(None, max_length=10, description="Symbole de la devise")
    langue: str = Field(default="fr", description="Langue d'interface")
    fuseau_horaire: str = Field(default="Africa/Kinshasa", description="Fuseau horaire")
    format_date: str = Field(default="DD/MM/YYYY", description="Format de date")
    format_heure: str = Field(default="24h", description="Format d'heure")
    decimal_places: int = Field(default=2, ge=0, le=6, description="Nombre de décimales")
    
    # Branding
    logo_url: Optional[str] = Field(None, max_length=500, description="URL du logo")
    favicon_url: Optional[str] = Field(None, max_length=500, description="URL du favicon")
    couleur_principale: Optional[str] = Field(None, description="Couleur principale (hex)")
    couleur_secondaire: Optional[str] = Field(None, description="Couleur secondaire (hex)")
    site_web: Optional[str] = Field(None, max_length=200, description="Site web")
    page_facebook: Optional[str] = Field(None, max_length=200, description="Page Facebook")
    compte_twitter: Optional[str] = Field(None, max_length=200, description="Compte Twitter")
    compte_instagram: Optional[str] = Field(None, max_length=200, description="Compte Instagram")
    
    # Configuration métier par défaut
    config: Dict[str, Any] = Field(
        default_factory=lambda: {
            "stock": {
                "alerte_seuil": 10,
                "unite_par_defaut": "unité",
                "gestion_lots": True,
                "date_peremption_obligatoire": False
            },
            "ventes": {
                "tva_par_defaut": 0,
                "arrondi_total": True,
                "imprimer_ticket": True,
                "email_ticket": False
            },
            "securite": {
                "complexite_mdp": "medium",
                "session_timeout": 30,
                "verification_connexion": True
            },
            "notifications": {
                "email_ventes": True,
                "email_stock": True,
                "sms_alertes": False
            }
        },
        description="Configuration métier par défaut"
    )
    
    # Sécurité
    custom_domain: Optional[str] = Field(None, max_length=150, description="Domaine personnalisé")
    ssl_enabled: bool = Field(default=False, description="SSL activé")
    ip_restrictions: Optional[List[str]] = Field(None, description="Liste d'IP autorisées")
    mfa_required: bool = Field(default=False, description="Authentification à deux facteurs requise")
    
    # Limites
    max_users: int = Field(default=3, ge=1, description="Nombre maximum d'utilisateurs")
    max_products: Optional[int] = Field(None, ge=0, description="Nombre maximum de produits")
    max_customers: Optional[int] = Field(None, ge=0, description="Nombre maximum de clients")
    max_storage_mb: int = Field(default=1024, ge=10, description="Stockage maximum en MB")
    max_api_calls_per_day: int = Field(default=1000, ge=10, description="Appels API maximum par jour")
    
    # Métadonnées
    tenants_metadata: Dict[str, Any] = Field(default_factory=dict, description="Métadonnées personnalisées")
    tags: List[str] = Field(default_factory=list, description="Tags de classification")
    notes: Optional[str] = Field(None, description="Notes internes")
    
    model_config = ConfigDict(from_attributes=True)

    @field_validator('email_admin')
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not v or '@' not in v:
            raise ValueError("Email administrateur invalide")
        return v.lower()
    
    @field_validator('telephone_principal')
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not v or len(v) < 8:
            raise ValueError("Numéro de téléphone invalide")
        # Nettoyer le numéro
        return ''.join(filter(str.isdigit, v))
    
    @field_validator('telephone_secondaire', 'telephone_mobile', 'telephone_proprietaire', 'telephone_pharmacien')
    @classmethod
    def validate_optional_phone(cls, v: Optional[str]) -> Optional[str]:
        """Validation des téléphones optionnels"""
        if v is None:
            return v
        if len(v) < 8:
            raise ValueError("Numéro de téléphone invalide")
        return ''.join(filter(str.isdigit, v))
    
    @field_validator('email_proprietaire', 'email_pharmacien')
    @classmethod
    def validate_optional_email(cls, v: Optional[str]) -> Optional[str]:
        """Validation des emails optionnels"""
        if v is not None and '@' not in v:
            raise ValueError('Format d\'email invalide')
        return v.lower() if v else v
    
    @field_validator('numero_agrement')
    @classmethod
    def validate_license_number(cls, v: Optional[str]) -> Optional[str]:
        """Validation du numéro d'agrément"""
        if v and not re.match(r'^[A-Z0-9\-/]+$', v):
            raise ValueError('Format de numéro d\'agrément invalide')
        return v
    
    @field_validator('config')
    @classmethod
    def validate_config_structure(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Validation de la structure de configuration"""
        if not isinstance(v, dict):
            raise ValueError('La configuration doit être un dictionnaire')
        
        # Sections obligatoires
        required_sections = ['stock', 'ventes', 'securite', 'notifications']
        for section in required_sections:
            if section not in v:
                v[section] = {}
        
        # Validation des valeurs de stock
        stock_config = v.get('stock', {})
        if 'alerte_seuil' in stock_config:
            if not isinstance(stock_config['alerte_seuil'], int) or stock_config['alerte_seuil'] < 0:
                raise ValueError('alerte_seuil doit être un entier positif')
        
        # Validation des valeurs de ventes
        ventes_config = v.get('ventes', {})
        if 'tva_par_defaut' in ventes_config:
            tva = ventes_config['tva_par_defaut']
            if not isinstance(tva, (int, float)) or tva < 0 or tva > 100:
                raise ValueError('tva_par_defaut doit être un pourcentage entre 0 et 100')
        
        return v
    
    @field_validator('metadata')
    @classmethod
    def validate_metadata(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Validation des métadonnées"""
        if not isinstance(v, dict):
            raise ValueError('Les métadonnées doivent être un dictionnaire')
        return v
    
    @field_validator('tags')
    @classmethod
    def validate_tags(cls, v: List[str]) -> List[str]:
        """Validation des tags"""
        if not isinstance(v, list):
            raise ValueError('Les tags doivent être une liste')
        # Limiter la longueur des tags
        for tag in v:
            if not isinstance(tag, str) or len(tag) > 50:
                raise ValueError('Chaque tag doit être une chaîne de maximum 50 caractères')
        return v
    
    @field_validator('langue')
    @classmethod
    def validate_langue(cls, v: str) -> str:
        """Validation de la langue"""
        if v not in ['fr', 'en', 'sw']:
            raise ValueError("Langue doit être 'fr', 'en' ou 'sw'")
        return v
    
    @field_validator('couleur_principale', 'couleur_secondaire')
    @classmethod
    def validate_couleur(cls, v: Optional[str]) -> Optional[str]:
        """Validation des couleurs hexadécimales"""
        if v and not re.match(r'^#[0-9A-Fa-f]{6}$', v):
            raise ValueError('Format de couleur invalide. Doit être au format hexadécimal: #RRGGBB')
        return v


# =======================
# SCHÉMAS DE CRÉATION
# =======================
class TenantCreate(TenantBase):
    """Schéma pour la création d'un nouveau tenant"""
    # Champs obligatoires pour la création
    status: TenantStatusEnum = Field(default=TenantStatusEnum.draft, description="Statut initial")
    tenant_code: str = Field(..., min_length=3, max_length=20, description="Code unique de la pharmacie")
    slug: Optional[str] = Field(None, max_length=100, description="Slug pour URLs personnalisées")
    
    # Horaires d'ouverture (format JSON)
    horaires: Dict[str, Any] = Field(
        default_factory=lambda: {
            "lundi": {"ouverture": "08:00", "fermeture": "18:00", "ouvert": True},
            "mardi": {"ouverture": "08:00", "fermeture": "18:00", "ouvert": True},
            "mercredi": {"ouverture": "08:00", "fermeture": "18:00", "ouvert": True},
            "jeudi": {"ouverture": "08:00", "fermeture": "18:00", "ouvert": True},
            "vendredi": {"ouverture": "08:00", "fermeture": "18:00", "ouvert": True},
            "samedi": {"ouverture": "08:00", "fermeture": "13:00", "ouvert": True},
            "dimanche": {"ouverture": None, "fermeture": None, "ouvert": False}
        },
        description="Horaires d'ouverture par jour"
    )
    
    # Information d'abonnement (pour inscription)
    current_plan: Optional[str] = Field(None, description="Plan d'abonnement initial")
    billing_period: Optional[BillingPeriodEnum] = Field(None, description="Période de facturation")
    monthly_rate: Optional[float] = Field(None, ge=0, description="Tarif mensuel")
    annual_rate: Optional[float] = Field(None, ge=0, description="Tarif annuel")
    auto_renew: bool = Field(default=True, description="Renouvellement automatique")
    payment_method: Optional[PaymentMethodEnum] = Field(None, description="Méthode de paiement")
    
    # Période d'essai
    trial_days: int = Field(default=14, ge=0, le=90, description="Nombre de jours d'essai")
    
    @field_validator('tenant_code')
    @classmethod
    def validate_tenant_code_format(cls, v: str) -> str:
        """Validation du format du code tenant"""
        if not re.match(r'^[A-Z0-9\-_]+$', v):
            raise ValueError(
                'Le code tenant doit contenir uniquement des lettres majuscules, '
                'chiffres, tirets et underscores'
            )
        return v
    
    @field_validator('slug')
    @classmethod
    def validate_slug_format(cls, v: Optional[str]) -> Optional[str]:
        """Validation du format du slug"""
        if v is not None:
            if not re.match(r'^[a-z0-9\-]+$', v):
                raise ValueError(
                    'Le slug doit contenir uniquement des lettres minuscules, '
                    'chiffres et tirets'
                )
            if len(v) < 3:
                raise ValueError('Le slug doit contenir au moins 3 caractères')
        return v
    
    @field_validator('horaires')
    @classmethod
    def validate_opening_hours(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Validation des horaires d'ouverture"""
        if not isinstance(v, dict):
            raise ValueError('Les horaires doivent être un dictionnaire')
        
        days = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        for day in days:
            if day not in v:
                v[day] = {"ouverture": None, "fermeture": None, "ouvert": False}
            
            day_config = v[day]
            if not isinstance(day_config, dict):
                raise ValueError(f'Configuration invalide pour {day}')
            
            # Validation des heures
            if day_config.get("ouvert", False):
                for time_key in ["ouverture", "fermeture"]:
                    time_value = day_config.get(time_key)
                    if time_value:
                        if not re.match(r'^([01]?[0-9]|2[0-3]):[0-5][0-9]$', time_value):
                            raise ValueError(f'Format d\'heure invalide pour {day}.{time_key}')
        
        return v
    
    @model_validator(mode='after')
    def validate_contact_emails(self) -> 'TenantCreate':
        """Validation des emails de contact"""
        emails = [self.email_admin]
        if self.email_proprietaire:
            emails.append(self.email_proprietaire)
        if self.email_pharmacien:
            emails.append(self.email_pharmacien)
        
        # Vérifier les doublons
        if len(emails) != len(set(emails)):
            raise ValueError('Les emails doivent être uniques')
        
        return self


class TenantAdminCreate(BaseModel):
    """Schéma pour la création de l'administrateur du tenant"""
    nom_complet: str = Field(..., min_length=2, max_length=150, description="Nom complet")
    email: EmailStr = Field(..., description="Email de l'administrateur")
    password: str = Field(..., min_length=8, max_length=100, description="Mot de passe")
    telephone: Optional[str] = Field(None, description="Téléphone personnel")
    
    @field_validator('password')
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        """Validation de la force du mot de passe"""
        if len(v) < 8:
            raise ValueError('Le mot de passe doit contenir au moins 8 caractères')
        if not any(c.isupper() for c in v):
            raise ValueError('Le mot de passe doit contenir au moins une majuscule')
        if not any(c.islower() for c in v):
            raise ValueError('Le mot de passe doit contenir au moins une minuscule')
        if not any(c.isdigit() for c in v):
            raise ValueError('Le mot de passe doit contenir au moins un chiffre')
        if not any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?`~' for c in v):
            raise ValueError('Le mot de passe doit contenir au moins un caractère spécial')
        return v


class TenantRegistration(BaseModel):
    """Schéma d'inscription complet d'une nouvelle pharmacie"""
    tenant: TenantCreate
    admin: TenantAdminCreate
    
    @model_validator(mode='after')
    def validate_unique_emails(self) -> 'TenantRegistration':
        """Vérifie que l'email admin est différent des emails du tenant"""
        tenant_emails = [
            self.tenant.email_admin,
            self.tenant.email_proprietaire,
            self.tenant.email_pharmacien
        ]
        tenant_emails = [email for email in tenant_emails if email]
        
        if self.admin.email in tenant_emails:
            raise ValueError(
                "L'email de l'administrateur doit être différent des emails du tenant"
            )
        
        return self


# =======================
# SCHÉMAS DE MISE À JOUR
# =======================
class TenantUpdate(BaseModel):
    """Schéma pour mettre à jour un tenant existant"""
    nom_pharmacie: Optional[str] = Field(None, min_length=2, max_length=200)
    nom_commercial: Optional[str] = Field(None, max_length=200)
    slogan: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = Field(None)
    
    # Contact
    email_contact: Optional[EmailStr] = None
    telephone_principal: Optional[str] = Field(None, min_length=9, max_length=20)
    telephone_secondaire: Optional[str] = Field(None, min_length=9, max_length=20)
    telephone_mobile: Optional[str] = Field(None, min_length=9, max_length=20)
    fax: Optional[str] = Field(None, max_length=20)
    
    # Adresse
    adresse_ligne1: Optional[str] = Field(None, max_length=200)
    adresse_ligne2: Optional[str] = Field(None, max_length=200)
    quartier: Optional[str] = Field(None, max_length=100)
    commune: Optional[str] = Field(None, max_length=100)
    ville: Optional[str] = Field(None, max_length=100)
    province: Optional[str] = Field(None, max_length=100)
    pays: Optional[str] = Field(None, max_length=100)
    code_postal: Optional[str] = Field(None, max_length=20)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    
    # Informations légales
    nom_entreprise: Optional[str] = Field(None, max_length=200)
    forme_juridique: Optional[str] = Field(None, max_length=50)
    registre_commerce: Optional[str] = Field(None, max_length=50)
    numero_ifu: Optional[str] = Field(None, max_length=50)
    numero_national: Optional[str] = Field(None, max_length=50)
    numero_agrement: Optional[str] = Field(None, max_length=50)
    date_agrement: Optional[date] = None
    
    # Responsables
    nom_proprietaire: Optional[str] = Field(None, max_length=150)
    prenom_proprietaire: Optional[str] = Field(None, max_length=150)
    email_proprietaire: Optional[EmailStr] = None
    telephone_proprietaire: Optional[str] = Field(None, min_length=9, max_length=20)
    
    nom_pharmacien: Optional[str] = Field(None, max_length=150)
    prenom_pharmacien: Optional[str] = Field(None, max_length=150)
    numero_ordre: Optional[str] = Field(None, max_length=50)
    email_pharmacien: Optional[EmailStr] = None
    telephone_pharmacien: Optional[str] = Field(None, min_length=9, max_length=20)
    
    # Caractéristiques
    type_pharmacie: Optional[PharmacyTypeEnum] = None
    specialite: Optional[str] = Field(None, max_length=100)
    superficie: Optional[float] = Field(None, ge=0)
    nombre_employes: Optional[int] = Field(None, ge=1)
    nombre_guichets: Optional[int] = Field(None, ge=1)
    annee_creation: Optional[int] = Field(None, ge=1900, le=datetime.now().year)
    
    # Horaires
    horaires: Optional[Dict[str, Any]] = None
    
    # Configuration système
    devise: Optional[str] = Field(None, min_length=3, max_length=3)
    devise_symbol: Optional[str] = Field(None, max_length=10)
    langue: Optional[str] = Field(None)
    fuseau_horaire: Optional[str] = None
    format_date: Optional[str] = None
    format_heure: Optional[str] = None
    decimal_places: Optional[int] = Field(None, ge=0, le=6)
    
    # Branding
    logo_url: Optional[str] = Field(None, max_length=500)
    favicon_url: Optional[str] = Field(None, max_length=500)
    couleur_principale: Optional[str] = Field(None)
    couleur_secondaire: Optional[str] = Field(None)
    site_web: Optional[str] = Field(None, max_length=200)
    page_facebook: Optional[str] = Field(None, max_length=200)
    compte_twitter: Optional[str] = Field(None, max_length=200)
    compte_instagram: Optional[str] = Field(None, max_length=200)
    
    # Configuration métier
    config: Optional[Dict[str, Any]] = None
    
    # Sécurité
    custom_domain: Optional[str] = Field(None, max_length=150)
    ssl_enabled: Optional[bool] = None
    ip_restrictions: Optional[List[str]] = None
    mfa_required: Optional[bool] = None
    
    # Limites
    max_users: Optional[int] = Field(None, ge=1)
    max_products: Optional[int] = Field(None, ge=0)
    max_customers: Optional[int] = Field(None, ge=0)
    max_storage_mb: Optional[int] = Field(None, ge=10)
    max_api_calls_per_day: Optional[int] = Field(None, ge=10)
    
    # Métadonnées
    tenants_metadata: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


# =======================
# SCHÉMAS DE RÉPONSE
# =======================
class TenantResponse(BaseModel):
    """Schéma de réponse complet pour un tenant"""
    id: UUID
    tenant_code: str
    slug: Optional[str]
    
    # Informations générales
    nom_pharmacie: str
    nom_commercial: Optional[str]
    slogan: Optional[str]
    description: Optional[str]
    
    # Contact
    email_admin: str
    email_contact: Optional[str]
    telephone_principal: str
    telephone_secondaire: Optional[str]
    telephone_mobile: Optional[str]
    fax: Optional[str]
    
    # Adresse complète
    adresse_ligne1: str
    adresse_ligne2: Optional[str]
    quartier: Optional[str]
    commune: Optional[str]
    ville: str
    province: Optional[str]
    pays: str
    code_postal: Optional[str]
    full_address: Optional[str] = None
    latitude: Optional[float]
    longitude: Optional[float]
    
    # Informations légales
    nom_entreprise: Optional[str]
    forme_juridique: Optional[str]
    registre_commerce: Optional[str]
    numero_ifu: Optional[str]
    numero_national: Optional[str]
    numero_agrement: Optional[str]
    date_agrement: Optional[date]
    
    # Responsables
    nom_proprietaire: str
    prenom_proprietaire: Optional[str]
    owner_full_name: Optional[str] = None
    email_proprietaire: Optional[str]
    telephone_proprietaire: Optional[str]
    
    nom_pharmacien: Optional[str]
    prenom_pharmacien: Optional[str]
    pharmacist_full_name: Optional[str] = None
    numero_ordre: Optional[str]
    email_pharmacien: Optional[str]
    telephone_pharmacien: Optional[str]
    
    # Caractéristiques
    type_pharmacie: str
    specialite: Optional[str]
    superficie: Optional[float]
    nombre_employes: Optional[int]
    nombre_guichets: Optional[int]
    annee_creation: Optional[int]
    
    # Horaires
    horaires: Dict[str, Any]
    
    # Abonnement
    status: str
    current_plan: Optional[str]
    billing_period: Optional[str]
    
    subscription_start_date: Optional[datetime]
    subscription_end_date: Optional[datetime]
    trial_start_date: Optional[datetime]
    trial_end_date: Optional[datetime]
    last_payment_date: Optional[datetime]
    next_payment_date: Optional[datetime]
    
    monthly_rate: Optional[float]
    annual_rate: Optional[float]
    current_rate: Optional[float]
    
    auto_renew: Optional[bool]
    payment_method: Optional[str]
    
    # Statuts
    is_active: bool
    is_trial: bool
    is_suspended: bool
    trial_days_remaining: Optional[int]
    subscription_days_remaining: Optional[int]
    
    # Configuration
    devise: str
    devise_symbol: Optional[str]
    langue: str
    fuseau_horaire: str
    format_date: str
    format_heure: str
    decimal_places: int
    
    # Branding
    logo_url: Optional[str]
    favicon_url: Optional[str]
    couleur_principale: Optional[str]
    couleur_secondaire: Optional[str]
    site_web: Optional[str]
    page_facebook: Optional[str]
    compte_twitter: Optional[str]
    compte_instagram: Optional[str]
    
    # Configuration métier
    config: Dict[str, Any]
    
    # Sécurité
    custom_domain: Optional[str]
    ssl_enabled: bool
    mfa_required: bool
    
    # Limites
    max_users: int
    max_products: Optional[int]
    max_customers: Optional[int]
    max_storage_mb: int
    max_api_calls_per_day: int
    
    # Statistiques
    total_sales: Optional[int] = 0
    total_revenue: Optional[float] = 0.0
    total_customers: Optional[int] = 0
    total_products: Optional[int] = 0
    total_inventory_value: Optional[float] = 0.0
    
    # Métadonnées
    tenants_metadata: Dict[str, Any]
    tags: List[str]
    notes: Optional[str]
    
    # Dates
    created_at: datetime
    updated_at: datetime
    activated_at: Optional[datetime]
    suspended_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    archived_at: Optional[datetime]
    last_login_at: Optional[datetime]
    last_sync_at: Optional[datetime]
    last_backup_at: Optional[datetime]
    
    # Relations (compteurs)
    users_count: Optional[int] = 0
    subscriptions_count: Optional[int] = 0
    products_count: Optional[int] = 0
    
    model_config = ConfigDict(from_attributes=True)


class TenantSummaryResponse(BaseModel):
    """Version allégée pour les listes"""
    id: UUID
    tenant_code: str
    nom_pharmacie: str
    ville: str
    email_admin: str
    telephone_principal: str
    status: str
    current_plan: Optional[str]
    is_active: bool
    is_trial: bool
    created_at: datetime
    last_login_at: Optional[datetime]
    
    model_config = ConfigDict(from_attributes=True)


class TenantRegistrationResponse(BaseModel):
    """Réponse après une inscription réussie"""
    message: str
    tenant: TenantResponse
    admin_token: str = Field(..., description="Token JWT pour l'admin")
    setup_steps: List[str] = Field(
        default_factory=lambda: [
            "1. Tenant créé avec succès",
            "2. Utilisateur admin créé",
            "3. Configuration initiale appliquée",
            "4. Base de données initialisée",
            "5. Token d'accès généré"
        ]
    )
    welcome_message: str = Field(
        default="Bienvenue dans MEDIGEST Pro ! Votre compte a été créé avec succès.",
        description="Message de bienvenue"
    )


# =======================
# SCHÉMAS D'ABONNEMENT
# =======================
class TenantSubscriptionUpdate(BaseModel):
    """Mise à jour de l'abonnement"""
    current_plan: str = Field(..., description="Nouveau plan")
    billing_period: BillingPeriodEnum = Field(..., description="Nouvelle période")
    monthly_rate: Optional[float] = Field(None, ge=0)
    annual_rate: Optional[float] = Field(None, ge=0)
    auto_renew: Optional[bool] = None
    payment_method: Optional[PaymentMethodEnum] = None
    
    @model_validator(mode='after')
    def validate_rates(self) -> 'TenantSubscriptionUpdate':
        """Validation des taux selon la période"""
        if self.billing_period == BillingPeriodEnum.monthly and not self.monthly_rate:
            raise ValueError('monthly_rate est requis pour la facturation mensuelle')
        
        if self.billing_period == BillingPeriodEnum.annual and not self.annual_rate:
            raise ValueError('annual_rate est requis pour la facturation annuelle')
        
        return self


class TenantTrialExtension(BaseModel):
    """Extension de la période d'essai"""
    extension_days: int = Field(..., ge=1, le=90, description="Nombre de jours d'extension")
    reason: Optional[str] = Field(None, max_length=500, description="Raison de l'extension")


class TenantPayment(BaseModel):
    """Enregistrement d'un paiement"""
    amount: float = Field(..., ge=0.01, description="Montant payé")
    payment_method: PaymentMethodEnum = Field(..., description="Méthode de paiement")
    payment_reference: str = Field(..., max_length=100, description="Référence de paiement")
    payment_date: date = Field(default_factory=lambda: date.today(), description="Date de paiement")
    period_start: date = Field(..., description="Début de la période couverte")
    period_end: date = Field(..., description="Fin de la période couverte")
    notes: Optional[str] = Field(None, max_length=500, description="Notes")
    
    @model_validator(mode='after')
    def validate_period(self) -> 'TenantPayment':
        """Validation de la période"""
        if self.period_end <= self.period_start:
            raise ValueError('period_end doit être après period_start')
        return self


# =======================
# SCHÉMAS DE CONFIGURATION
# =======================
class TenantConfigUpdate(BaseModel):
    """Mise à jour de la configuration métier"""
    config: Dict[str, Any]
    
    @field_validator('config')
    @classmethod
    def validate_config_sections(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Validation des sections de configuration"""
        allowed_sections = {
            'stock', 'ventes', 'securite', 'notifications', 
            'comptabilite', 'impression', 'backup', 'api'
        }
        
        for section in v.keys():
            if section not in allowed_sections:
                raise ValueError(f"Section de configuration non autorisée: {section}")
        
        return v


class TenantConfigSectionUpdate(BaseModel):
    """Mise à jour d'une section spécifique de configuration"""
    section: str = Field(..., description="Section à mettre à jour")
    values: Dict[str, Any] = Field(..., description="Nouvelles valeurs")
    
    @field_validator('section')
    @classmethod
    def validate_section_name(cls, v: str) -> str:
        allowed_sections = {
            'stock', 'ventes', 'securite', 'notifications', 
            'comptabilite', 'impression', 'backup', 'api'
        }
        if v not in allowed_sections:
            raise ValueError(f"Section non autorisée: {v}")
        return v


# =======================
# SCHÉMAS ADMINISTRATIFS
# =======================
class TenantSuspensionRequest(BaseModel):
    """Demande de suspension d'un tenant"""
    reason: str = Field(..., min_length=10, max_length=1000, description="Raison de la suspension")
    suspension_until: Optional[date] = Field(None, description="Date de fin de suspension")
    block_access: bool = Field(default=True, description="Bloquer l'accès immédiatement")
    notify_user: bool = Field(default=True, description="Notifier l'utilisateur")
    
    @field_validator('suspension_until')
    @classmethod
    def validate_suspension_date(cls, v: Optional[date]) -> Optional[date]:
        if v and v <= date.today():
            raise ValueError('La date de fin de suspension doit être dans le futur')
        return v


class TenantReactivationRequest(BaseModel):
    """Demande de réactivation d'un tenant"""
    reason: str = Field(..., min_length=10, max_length=1000, description="Raison de la réactivation")
    restore_data: bool = Field(default=True, description="Restaurer les données")
    notify_user: bool = Field(default=True, description="Notifier l'utilisateur")


class TenantStatusUpdate(BaseModel):
    """Mise à jour manuelle du statut"""
    status: TenantStatusEnum = Field(..., description="Nouveau statut")
    reason: str = Field(..., min_length=10, max_length=1000, description="Raison du changement")
    effective_date: Optional[date] = Field(None, description="Date d'effet")
    notes: Optional[str] = Field(None, max_length=2000, description="Notes internes")
    
    @field_validator('effective_date')
    @classmethod
    def validate_effective_date(cls, v: Optional[date]) -> Optional[date]:
        if v and v < date.today():
            raise ValueError('La date d\'effet ne peut pas être dans le passé')
        return v


# =======================
# SCHÉMAS DE STATISTIQUES
# =======================
class TenantStatsResponse(BaseModel):
    """Statistiques d'un tenant"""
    tenant_id: UUID
    tenant_code: str
    nom_pharmacie: str
    status: str
    
    # Compteurs
    total_users: int = 0
    total_products: int = 0
    total_customers: int = 0
    total_suppliers: int = 0
    
    # Activité récente
    sales_today: int = 0
    sales_this_week: int = 0
    sales_this_month: int = 0
    revenue_today: float = 0.0
    revenue_this_week: float = 0.0
    revenue_this_month: float = 0.0
    
    # Stock
    total_inventory_items: int = 0
    total_inventory_value: float = 0.0
    low_stock_items: int = 0
    out_of_stock_items: int = 0
    
    # Finances
    total_debt: float = 0.0
    total_credit: float = 0.0
    profit_margin: float = 0.0
    
    # Abonnement
    current_plan: Optional[str]
    subscription_end_date: Optional[date]
    trial_end_date: Optional[date]
    days_remaining: Optional[int] = 0
    trial_days_remaining: Optional[int] = 0
    
    # Activité
    last_sale_date: Optional[datetime]
    last_login_date: Optional[datetime]
    last_backup_date: Optional[datetime]
    active_days: int = 0
    
    # Performances
    sales_growth_rate: float = 0.0
    customer_growth_rate: float = 0.0
    inventory_turnover: float = 0.0
    
    model_config = ConfigDict(from_attributes=True)


# =======================
# SCHÉMAS DE LISTE
# =======================
class TenantListResponse(BaseModel):
    """Réponse pour la liste des tenants"""
    total: int
    page: int
    page_size: int
    total_pages: int
    tenants: List[TenantSummaryResponse]
    
    model_config = ConfigDict(from_attributes=True)


class TenantSearchCriteria(BaseModel):
    """Critères de recherche de tenants"""
    query: Optional[str] = Field(None, description="Recherche texte")
    status: Optional[List[TenantStatusEnum]] = Field(None, description="Statuts")
    type_pharmacie: Optional[List[PharmacyTypeEnum]] = Field(None, description="Types de pharmacies")
    ville: Optional[str] = Field(None, description="Ville")
    province: Optional[str] = Field(None, description="Province")
    plan: Optional[str] = Field(None, description="Plan d'abonnement")
    
    # Dates
    created_after: Optional[date] = Field(None, description="Créé après")
    created_before: Optional[date] = Field(None, description="Créé avant")
    
    # Tri
    sort_by: str = Field(default="created_at", description="Champ de tri")
    sort_order: str = Field(default="desc", description="Ordre de tri")
    
    # Pagination
    page: int = Field(default=1, ge=1, description="Page")
    page_size: int = Field(default=20, ge=1, le=100, description="Taille de page")
    
    model_config = ConfigDict(from_attributes=True)
    
    @field_validator('sort_order')
    @classmethod
    def validate_sort_order(cls, v: str) -> str:
        if v not in ['asc', 'desc']:
            raise ValueError("sort_order doit être 'asc' ou 'desc'")
        return v


# =======================
# SCHÉMAS D'INVITATION
# =======================
class TenantInvitation(BaseModel):
    """Invitation pour ajouter un nouvel utilisateur"""
    email: EmailStr = Field(..., description="Email de l'invité")
    nom_complet: str = Field(..., min_length=2, max_length=150, description="Nom complet")
    role: str = Field(default="pharmacien", description="Rôle de l'utilisateur")
    permissions: Optional[Dict[str, bool]] = Field(None, description="Permissions spécifiques")
    expires_in_days: int = Field(default=7, ge=1, le=30, description="Jours avant expiration")
    
    @field_validator('role')
    @classmethod
    def validate_role(cls, v: str) -> str:
        allowed_roles = ['admin', 'pharmacien', 'preparateur', 'caissier', 'vendeur', 'comptable', 'gestionnaire']
        if v not in allowed_roles:
            raise ValueError(f"Rôle non autorisé. Doit être l'un des suivants: {', '.join(allowed_roles)}")
        return v


class TenantInvitationResponse(BaseModel):
    """Réponse après création d'une invitation"""
    invitation_id: UUID
    email: str
    invitation_code: str
    expires_at: datetime
    invitation_url: Optional[str] = None
    message: str = "Invitation créée avec succès"


# =======================
# SCHÉMAS D'EXPORT
# =======================
class TenantExportRequest(BaseModel):
    """Demande d'export des données"""
    include_data: List[str] = Field(
        default_factory=lambda: ["profile", "products", "customers", "sales", "inventory"],
        description="Données à inclure"
    )
    format: str = Field(default="json", description="Format d'export")
    compress: bool = Field(default=False, description="Compresser les données")
    
    @field_validator('format')
    @classmethod
    def validate_format(cls, v: str) -> str:
        if v not in ['json', 'csv', 'excel']:
            raise ValueError("Format doit être 'json', 'csv' ou 'excel'")
        return v
    
    @field_validator('include_data')
    @classmethod
    def validate_include_data(cls, v: List[str]) -> List[str]:
        allowed_data = ["profile", "products", "customers", "suppliers", "sales", 
                       "purchases", "inventory", "debts", "payments", "users", "settings"]
        for data_type in v:
            if data_type not in allowed_data:
                raise ValueError(f"Type de données non autorisé: {data_type}")
        return v


# =======================
# SCHÉMAS DE NOTIFICATION
# =======================
class TenantNotificationSettings(BaseModel):
    """Paramètres de notification"""
    email_sales: bool = Field(default=True, description="Notifications par email pour les ventes")
    email_stock: bool = Field(default=True, description="Notifications par email pour le stock")
    email_finance: bool = Field(default=True, description="Notifications par email pour les finances")
    sms_alerts: bool = Field(default=False, description="Alertes SMS")
    push_notifications: bool = Field(default=True, description="Notifications push")
    daily_summary: bool = Field(default=True, description="Résumé quotidien")
    weekly_report: bool = Field(default=True, description="Rapport hebdomadaire")
    monthly_report: bool = Field(default=True, description="Rapport mensuel")
    
    model_config = ConfigDict(from_attributes=True)
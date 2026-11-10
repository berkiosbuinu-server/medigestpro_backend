from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any, Union
from datetime import datetime
import re
from uuid import UUID


# ============================================
# SCHÉMAS DE CONFIGURATION - HEURES DE SERVICE
# ============================================

class DaysOffConfig(BaseModel):
    """Configuration des jours de service"""
    monday: bool = True
    tuesday: bool = True
    wednesday: bool = True
    thursday: bool = True
    friday: bool = True
    saturday: bool = True
    sunday: bool = False
    
    model_config = ConfigDict(from_attributes=True)


class WorkingHoursConfig(BaseModel):
    """
    Configuration des heures de service
    Les heures sont stockées en heure locale de la pharmacie (timezone spécifiée)
    """
    enabled: bool = True
    startTime: str = Field(
        default="08:00", 
        pattern=r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$",
        description="Heure de début en format HH:MM (heure locale pharmacie)"
    )
    endTime: str = Field(
        default="20:00", 
        pattern=r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$",
        description="Heure de fin en format HH:MM (heure locale pharmacie)"
    )
    overtimeEndTime: Optional[str] = Field(
        None, 
        pattern=r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$",
        description="Heure limite supplémentaire en format HH:MM (heure locale pharmacie)"
    )
    timezone: str = Field(
        default="Africa/Kinshasa",
        description="Fuseau horaire de la pharmacie (ex: Africa/Kinshasa, Africa/Lubumbashi, Europe/Paris)"
    )
    daysOff: DaysOffConfig = Field(
        default_factory=DaysOffConfig,
        description="Configuration des jours de service"
    )
    
    model_config = ConfigDict(from_attributes=True)
    
    @field_validator('endTime')
    @classmethod
    def validate_end_time(cls, v, info):
        """Vérifie que l'heure de fin est après l'heure de début"""
        if 'startTime' in info.data and v < info.data['startTime']:
            raise ValueError("L'heure de fin doit être après l'heure de début")
        return v
    
    @field_validator('overtimeEndTime')
    @classmethod
    def validate_overtime(cls, v, info):
        """Vérifie que l'heure supplémentaire est après l'heure de fin"""
        if v and 'endTime' in info.data and v < info.data['endTime']:
            raise ValueError("L'heure supplémentaire doit être après l'heure de fin")
        return v
    
    @field_validator('timezone')
    @classmethod
    def validate_timezone(cls, v):
        """Valide que le fuseau horaire est supporté"""
        supported_zones = [
            "Africa/Kinshasa",      # UTC+1
            "Africa/Lubumbashi",    # UTC+2
            "Africa/Johannesburg",  # UTC+2
            "Africa/Lagos",         # UTC+1
            "Europe/Paris",         # UTC+1/UTC+2 (DST)
            "UTC"
        ]
        if v not in supported_zones:
            raise ValueError(f"Fuseau horaire non supporté. Utilisez l'un de: {', '.join(supported_zones)}")
        return v


# ============================================
# SCHÉMAS DE CONFIGURATION - DEVISES
# ============================================

class CurrencyConfig(BaseModel):
    """Configuration d'une devise"""
    code: str = Field(..., min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    symbol: str = Field(..., min_length=1, max_length=5)
    isActive: bool = True
    exchangeRate: float = Field(..., gt=0, description="Taux de change par rapport à la devise primaire")
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# SCHÉMAS DE CONFIGURATION - MARGES ET PRIX
# ============================================

class MarginConfig(BaseModel):
    """Configuration des marges bénéficiaires"""
    defaultMargin: float = Field(..., ge=0, le=100, description="Marge par défaut en pourcentage")
    minMargin: float = Field(..., ge=0, le=100, description="Marge minimale autorisée")
    maxMargin: float = Field(..., ge=0, le=100, description="Marge maximale autorisée")
    
    model_config = ConfigDict(from_attributes=True)
    
    @field_validator('maxMargin')
    @classmethod
    def validate_margins(cls, v, info):
        """Vérifie la cohérence des marges"""
        if 'minMargin' in info.data and v < info.data['minMargin']:
            raise ValueError("La marge maximale doit être supérieure à la marge minimale")
        return v


class AutomaticPricingConfig(BaseModel):
    """Configuration du calcul automatique des prix"""
    enabled: bool = False
    method: str = Field(
        ...,
        pattern=r"^(percentage|coefficient|margin)$",
        description="Méthode de calcul: percentage, coefficient, margin"
    )
    value: float = Field(..., gt=0, description="Valeur à appliquer selon la méthode choisie")
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# SCHÉMAS DE CONFIGURATION - SUCCURSALES
# ============================================

class BranchItem(BaseModel):
    """Informations d'une succursale"""
    id: str
    name: str
    address: str
    phone: str
    email: str
    manager: Optional[str] = None
    created_at: datetime
    is_active: bool = True
    
    model_config = ConfigDict(from_attributes=True)


class BranchConfig(BaseModel):
    """Configuration des succursales"""
    maxBranches: int = Field(..., ge=0, description="Nombre maximum de succursales autorisé")
    currentBranches: int = Field(..., ge=0, description="Nombre actuel de succursales")
    branches: List[BranchItem] = Field(default_factory=list, description="Liste des succursales")
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# SCHÉMAS DE CONFIGURATION - INFORMATIONS PHARMACIE
# ============================================

class PharmacyInfoConfig(BaseModel):
    """Informations de base de la pharmacie"""
    name: str = Field(..., description="Nom de la pharmacie")
    address: str = Field(..., description="Adresse complète")
    phone: str = Field(..., description="Numéro de téléphone")
    email: EmailStr = Field(..., description="Email de contact")
    licenseNumber: str = Field(..., description="Numéro de licence")
    logo: Optional[str] = Field(None, description="URL du logo")
    logoUrl: Optional[str] = Field(None, description="URL du logo (alias)")
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# SCHÉMAS DE CONFIGURATION - THÈME
# ============================================

class ThemeConfig(BaseModel):
    """Configuration du thème de l'application"""
    theme: str = Field(..., pattern=r"^(light|dark|system)$", description="Thème: light, dark ou system")
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# SCHÉMAS DE CONFIGURATION - NOUVEAUX CHAMPS
# ============================================

class SalesTypeConfig(BaseModel):
    """Type de vente autorisé"""
    type: str = Field(..., pattern=r"^(wholesale|retail|both)$", description="Type de vente: wholesale, retail ou both")
    
    model_config = ConfigDict(from_attributes=True)


class ExpiredProductsConfig(BaseModel):
    """Configuration des produits périmés"""
    allowSale: bool = Field(False, description="Autoriser la vente de produits périmés")
    
    model_config = ConfigDict(from_attributes=True)


class OvertimeConfig(BaseModel):
    """Configuration des heures supplémentaires"""
    enabled: bool = Field(False, description="Activer les heures supplémentaires")
    endTime: str = Field(
        "22:00", 
        pattern=r"^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$",
        description="Heure de fin des heures supplémentaires"
    )
    
    model_config = ConfigDict(from_attributes=True)


class ProfitabilityConfig(BaseModel):
    """Configuration du calcul de rentabilité"""
    enabled: bool = Field(False, description="Activer le calcul automatique du prix de vente")
    rate: float = Field(30.0, ge=0, le=500, description="Taux de rentabilité en %")
    
    model_config = ConfigDict(from_attributes=True)


class InvoiceConfig(BaseModel):
    """Configuration de la facturation"""
    autoPrint: bool = Field(False, description="Impression automatique après vente")
    autoSave: bool = Field(True, description="Sauvegarde automatique des factures")
    fontSize: int = Field(12, ge=8, le=24, description="Taille de police pour la facture (px)")
    
    model_config = ConfigDict(from_attributes=True)


class ReportConfig(BaseModel):
    """Configuration des rapports"""
    defaultFontSize: int = Field(12, ge=8, le=24, description="Taille de police par défaut pour les rapports (px)")
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# SCHÉMA PRINCIPAL DE CONFIGURATION PHARMACIE
# ============================================

class PharmacyConfig(BaseModel):
    """Configuration complète d'une pharmacie"""
    pharmacyInfo: PharmacyInfoConfig
    currencies: List[CurrencyConfig] = Field(..., min_length=1)
    primaryCurrency: str = Field(..., description="Code de la devise primaire")
    taxRate: float = Field(..., ge=0, le=100, description="Taux de TVA en pourcentage")
    lowStockThreshold: int = Field(..., ge=0, description="Seuil d'alerte stock bas")
    expiryWarningDays: int = Field(..., ge=0, description="Nombre de jours avant expiration pour alerte")
    allowNegativeStock: bool = Field(False, description="Autoriser la vente en stock négatif")
    workingHours: WorkingHoursConfig
    productReturnDays: int = Field(..., ge=0, description="Délai de retour produit en jours")
    marginConfig: MarginConfig
    automaticPricing: AutomaticPricingConfig
    theme: str = Field(..., pattern=r"^(light|dark|system)$", description="Thème de l'application")
    initialCapital: float = Field(..., ge=0, description="Capital initial en USD")
    branchConfig: BranchConfig
    createdAt: datetime
    updatedAt: datetime
    
    # Nouveaux champs
    salesType: SalesTypeConfig = Field(default_factory=SalesTypeConfig)
    expiredProducts: ExpiredProductsConfig = Field(default_factory=ExpiredProductsConfig)
    overtime: OvertimeConfig = Field(default_factory=OvertimeConfig)
    sellByExchangeRate: bool = Field(True, description="Vente selon le taux de change")
    profitability: ProfitabilityConfig = Field(default_factory=ProfitabilityConfig)
    invoice: InvoiceConfig = Field(default_factory=InvoiceConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)
    
    model_config = ConfigDict(from_attributes=True)
    
    @field_validator('primaryCurrency')
    @classmethod
    def validate_primary_currency(cls, v, info):
        """Vérifie que la devise primaire existe dans la liste"""
        if 'currencies' in info.data:
            currencies = info.data['currencies']
            if not any(c.code == v for c in currencies):
                raise ValueError(f"La devise primaire '{v}' n'existe pas dans la liste des devises")
        return v


# ============================================
# SCHÉMAS DE CRÉATION/MISE À JOUR PHARMACIE
# ============================================

class PharmacyBase(BaseModel):
    """Champs de base pour une pharmacie"""
    name: str = Field(..., description="Nom de la pharmacie")
    license_number: str = Field(..., min_length=5, description="Numéro de licence")
    address: str = Field(..., description="Adresse")
    city: str = Field("Kinshasa", description="Ville")
    country: str = Field("CD", min_length=2, max_length=2, description="Code pays ISO 3166-1 alpha-2")
    phone: Optional[str] = Field(None, description="Téléphone")
    email: Optional[EmailStr] = Field(None, description="Email")
    is_active: bool = Field(True, description="Statut actif/inactif")
    opening_hours: Optional[Dict[str, str]] = Field(None, description="Horaires d'ouverture (format libre)")
    pharmacist_in_charge: Optional[str] = Field(None, description="Pharmacien responsable")
    pharmacist_license: Optional[str] = Field(None, description="Licence du pharmacien")
    
    model_config = ConfigDict(from_attributes=True)
    
    @field_validator('license_number')
    @classmethod
    def validate_license_number(cls, v):
        """Valide le format du numéro de licence"""
        if not v or len(v) < 5:
            raise ValueError("Le numéro de licence doit contenir au moins 5 caractères")
        if not re.match(r'^[A-Z0-9-]+$', v):
            raise ValueError("Le numéro de licence ne peut contenir que des lettres majuscules, chiffres et tirets")
        return v
    
    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v):
        """Valide le format du téléphone"""
        if v and not re.match(r'^\+?[0-9\s-]{8,}$', v):
            raise ValueError("Format de téléphone invalide. Utilisez +243XXXXXXXXX")
        return v
    
    @field_validator('country')
    @classmethod
    def validate_country(cls, v):
        """Valide le code pays"""
        if v and len(v) != 2:
            raise ValueError("Le code pays doit être sur 2 caractères (ISO 3166-1 alpha-2)")
        return v.upper()


class PharmacyCreate(PharmacyBase):
    """Schéma pour la création d'une pharmacie"""
    tenant_id: str = Field(..., description="ID du tenant (UUID)")
    
    # Configuration optionnelle à la création
    config: Optional[PharmacyConfig] = None


class PharmacyUpdate(BaseModel):
    """Schéma pour la mise à jour d'une pharmacie"""
    name: Optional[str] = None
    license_number: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None
    opening_hours: Optional[Dict[str, str]] = None
    pharmacist_in_charge: Optional[str] = None
    pharmacist_license: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# SCHÉMAS DE MISE À JOUR DE CONFIGURATION
# ============================================

class PharmacyConfigUpdate(BaseModel):
    """Schéma pour la mise à jour partielle de la configuration"""
    pharmacyInfo: Optional[PharmacyInfoConfig] = None
    currencies: Optional[List[CurrencyConfig]] = None
    primaryCurrency: Optional[str] = None
    taxRate: Optional[float] = Field(None, ge=0, le=100)
    lowStockThreshold: Optional[int] = Field(None, ge=0)
    currencyMode: Optional[str] = Field(None, description="Mode de devise: cdf_only, usd_only, both")
    expiryWarningDays: Optional[int] = Field(None, ge=0)
    allowNegativeStock: Optional[bool] = None
    workingHours: Optional[WorkingHoursConfig] = None
    productReturnDays: Optional[int] = Field(None, ge=0)
    marginConfig: Optional[MarginConfig] = None
    automaticPricing: Optional[AutomaticPricingConfig] = None
    theme: Optional[str] = Field(None, pattern=r"^(light|dark|system)$")
    initialCapital: Optional[float] = Field(None, ge=0)
    branchConfig: Optional[BranchConfig] = None
    
    # Nouveaux champs
    salesType: Optional[Union[str, Dict[str, str]]] = None
    expiredProducts: Optional[ExpiredProductsConfig] = None
    overtime: Optional[OvertimeConfig] = None
    sellByExchangeRate: Optional[bool] = None
    profitability: Optional[ProfitabilityConfig] = None
    invoice: Optional[InvoiceConfig] = None
    report: Optional[ReportConfig] = None
    
    # Champs pour rétrocompatibilité
    require_prescription: Optional[bool] = None
    enable_expiry_alerts: Optional[bool] = None
    low_stock_threshold: Optional[int] = None
    enable_barcode: Optional[bool] = None
    tax_rate: Optional[float] = None
    currency: Optional[str] = None
    language: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)

    @field_validator('salesType')
    @classmethod
    def validate_sales_type(cls, v):
        """Accepte soit une chaîne, soit un objet avec 'type'"""
        if isinstance(v, dict):
            return v.get("type", "both")
        return v



# ============================================
# SCHÉMAS DE RÉPONSE
# ============================================

class PharmacyInDB(PharmacyBase):
    """Pharmacie telle que stockée en base"""
    id: str = Field(..., description="ID unique de la pharmacie (UUID)")
    tenant_id: str = Field(..., description="ID du tenant (UUID)")
    config: Dict[str, Any] = Field(default_factory=dict, description="Configuration JSON")
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            UUID: str,
            datetime: lambda v: v.isoformat()
        }
    )


class PharmacyResponse(PharmacyInDB):
    """Réponse API pour une pharmacie"""
    
    @property
    def display_name(self) -> str:
        """Nom d'affichage unifié"""
        return self.name or "Pharmacie sans nom"
    
    def model_dump(self, *args, **kwargs):
        """Surcharge pour garantir que les UUIDs sont des strings"""
        data = super().model_dump(*args, **kwargs)
        
        # Conversion explicite des UUIDs
        for field in ['id', 'tenant_id']:
            if field in data and data[field] and not isinstance(data[field], str):
                data[field] = str(data[field])
        
        # Conversion des datetime
        for field in ['created_at', 'updated_at']:
            if field in data and data[field] and isinstance(data[field], datetime):
                data[field] = data[field].isoformat()
        
        return data


class PharmacyConfigResponse(BaseModel):
    """Réponse API pour la configuration"""
    pharmacy_id: str
    config: Dict[str, Any]
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)
    
    def model_dump(self, *args, **kwargs):
        data = super().model_dump(*args, **kwargs)
        if 'updated_at' in data and isinstance(data['updated_at'], datetime):
            data['updated_at'] = data['updated_at'].isoformat()
        return data


# ============================================
# SCHÉMAS DE SERVICE ET STATUT
# ============================================

class ServiceStatusResponse(BaseModel):
    """Statut du service basé sur les heures configurées"""
    in_service: bool
    restrictions_enabled: bool
    current_time_utc: str
    current_time_local: str
    timezone: str
    current_day: str
    is_working_day: bool
    is_within_hours: bool
    working_hours: Dict[str, Optional[str]]
    message: str
    next_service_time: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class PharmacyLimitsResponse(BaseModel):
    """Limites de la pharmacie selon l'abonnement"""
    tenant_id: str
    tenant_name: str
    current_plan: str
    limits: Dict[str, Any]
    current_pharmacies_count: int
    max_pharmacies_allowed: int
    remaining_pharmacies: int
    can_create_more: bool
    max_branches_per_pharmacy: int
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# SCHÉMAS POUR LES SUCCURSALES
# ============================================

class BranchCreate(BaseModel):
    """Création d'une succursale"""
    name: str
    address: str
    phone: str
    email: EmailStr
    manager: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class BranchCreateResponse(BaseModel):
    """Réponse après création d'une succursale"""
    message: str
    branch: Dict[str, Any]
    remaining: int
    current_branches: int
    max_allowed: int
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# SCHÉMAS POUR LES UTILISATEURS EN LIGNE
# ============================================

class OnlineUserResponse(BaseModel):
    """Utilisateur en ligne"""
    id: str
    nom_complet: Optional[str] = None
    email: str
    role: str
    last_login: Optional[str] = None
    login_duration: str
    status: str = "online"
    
    model_config = ConfigDict(from_attributes=True)


class OnlineUsersResponse(BaseModel):
    """Liste des utilisateurs en ligne"""
    pharmacy_id: str
    pharmacy_name: str
    online_count: int
    users: List[OnlineUserResponse]
    timestamp: str
    
    model_config = ConfigDict(from_attributes=True)

# ============================================
# SCHÉMA SALES CONFIG (Configuration de vente)
# ============================================

class SalesConfig(BaseModel):
    """
    Configuration des paramètres de vente
    Utilisé pour les routes de configuration des ventes
    """
    salesType: str = Field(
        "both",
        pattern=r"^(wholesale|retail|both)$",
        description="Type de vente autorisé: wholesale (gros), retail (détail), both (les deux)"
    )
    calcul_auto_prix: bool = Field(
        True,
        description="Activer le calcul automatique des prix de vente"
    )
    marge_par_defaut: float = Field(
        25.0,
        ge=0,
        le=500,
        description="Marge par défaut en pourcentage pour le calcul automatique"
    )
    taux_tva: float = Field(
        16.0,
        ge=0,
        le=100,
        description="Taux de TVA par défaut en pourcentage"
    )
    lock_stock_modification: bool = Field(
        False,
        description="Verrouiller la modification du stock après validation"
    )
    
    model_config = ConfigDict(from_attributes=True)
    
    @field_validator('salesType')
    @classmethod
    def validate_sales_type(cls, v):
        """Valide le type de vente"""
        allowed = ["wholesale", "retail", "both"]
        if v not in allowed:
            raise ValueError(f"salesType doit être l'un de: {', '.join(allowed)}")
        return v


# ============================================
# EXPORT POUR COMPATIBILITÉ
# ============================================

# Ajouter SalesConfig à l'export si nécessaire
__all__ = [
    # ... autres exports existants ...
    "SalesConfig",
]


# ============================================
# ALIAS POUR COMPATIBILITÉ
# ============================================

PricingConfig = AutomaticPricingConfig  # Alias pour compatibilité
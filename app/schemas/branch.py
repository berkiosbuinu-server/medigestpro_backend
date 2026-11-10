# app/schemas/branch.py
from pydantic import BaseModel, Field, ConfigDict, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID


# ==================== BRANCH SCHEMAS ====================

class BranchBase(BaseModel):
    """Schéma de base pour une branche/succursale"""
    name: str = Field(..., min_length=1, max_length=255, description="Nom de la succursale")
    code: Optional[str] = Field(None, max_length=50, description="Code unique de la succursale")
    address: str = Field(..., description="Adresse complète")
    city: str = Field(..., max_length=100, description="Ville")
    country: str = Field(default="RDC", max_length=100, description="Pays")
    phone: Optional[str] = Field(None, max_length=50, description="Numéro de téléphone")
    email: Optional[str] = Field(None, max_length=255, description="Adresse email")
    
    # Coordonnées GPS
    latitude: Optional[float] = Field(None, ge=-90, le=90, description="Latitude")
    longitude: Optional[float] = Field(None, ge=-180, le=180, description="Longitude")
    
    # Responsable
    manager_id: Optional[UUID] = Field(None, description="ID du responsable")
    manager_name: Optional[str] = Field(None, max_length=255, description="Nom du responsable")
    
    # Horaires d'ouverture
    opening_hours: Optional[Dict[str, str]] = Field(
        default={
            "monday": "08:00-20:00",
            "tuesday": "08:00-20:00",
            "wednesday": "08:00-20:00",
            "thursday": "08:00-20:00",
            "friday": "08:00-20:00",
            "saturday": "09:00-18:00",
            "sunday": "closed"
        },
        description="Horaires d'ouverture par jour"
    )
    
    # Configuration spécifique
    config: Optional[Dict[str, Any]] = Field(
        default={
            "lowStockThreshold": 10,
            "expiryWarningDays": 90,
            "allowNegativeStock": False,
            "enableBatchTracking": True,
            "workingHours": {
                "enabled": True,
                "startTime": "08:00",
                "endTime": "20:00"
            }
        },
        description="Configuration spécifique à la succursale"
    )
    
    is_active: bool = Field(default=True, description="Statut actif/inactif")
    is_main_branch: bool = Field(default=False, description="Branche principale")


class BranchCreate(BranchBase):
    """Schéma pour la création d'une branche"""
    
    @validator('code', pre=True, always=True)
    def validate_code(cls, v, values):
        """Valide et génère un code si nécessaire"""
        if v is None:
            name = values.get('name', 'BRANCH')
            # Générer un code à partir du nom
            import re
            code = re.sub(r'[^A-Z0-9]', '', name.upper())[:10]
            return code or 'BRANCH'
        return v.upper()
    
    @validator('email')
    def validate_email(cls, v):
        """Valide le format de l'email"""
        if v is not None:
            import re
            if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
                raise ValueError('Format d\'email invalide')
        return v
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Succursale Centre-Ville",
                "code": "CV001",
                "address": "15 Avenue du Commerce",
                "city": "Kinshasa",
                "country": "RDC",
                "phone": "+243 812 345 678",
                "email": "centrevill@pharmacie.com",
                "latitude": -4.3219,
                "longitude": 15.3191,
                "manager_name": "Jean Mukendi",
                "is_main_branch": False
            }
        }
    )


class BranchUpdate(BaseModel):
    """Schéma pour la mise à jour d'une branche"""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    code: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    country: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    manager_id: Optional[UUID] = None
    manager_name: Optional[str] = Field(None, max_length=255)
    opening_hours: Optional[Dict[str, str]] = None
    config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None
    is_main_branch: Optional[bool] = None
    
    @validator('code')
    def validate_code(cls, v):
        """Valide le format du code"""
        if v is not None:
            return v.upper()
        return v
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Succursale Centre-Ville (Rénové)",
                "phone": "+243 812 345 679",
                "is_active": True
            }
        }
    )


class BranchResponse(BranchBase):
    """Schéma pour la réponse d'une branche"""
    id: UUID
    tenant_id: UUID
    parent_pharmacy_id: UUID
    created_at: datetime
    updated_at: datetime
    created_by: Optional[UUID] = None
    
    # Informations additionnelles
    pharmacy_name: Optional[str] = Field(None, description="Nom de la pharmacie parente")
    products_count: Optional[int] = Field(0, description="Nombre de produits dans cette branche")
    sales_count: Optional[int] = Field(0, description="Nombre de ventes")
    customers_count: Optional[int] = Field(0, description="Nombre de clients")
    
    model_config = ConfigDict(from_attributes=True)
    
    @classmethod
    def from_orm_with_counts(cls, branch, **kwargs):
        """Crée une réponse avec les compteurs"""
        data = cls.model_validate(branch)
        data.pharmacy_name = kwargs.get('pharmacy_name')
        data.products_count = kwargs.get('products_count', 0)
        data.sales_count = kwargs.get('sales_count', 0)
        data.customers_count = kwargs.get('customers_count', 0)
        return data


class BranchListResponse(BaseModel):
    """Schéma pour la liste des branches"""
    items: List[BranchResponse]
    total: int
    page: int
    size: int
    pages: int
    
    model_config = ConfigDict(from_attributes=True)


# ==================== BRANCH CONFIGURATION SCHEMAS ====================

class BranchWorkingHoursConfig(BaseModel):
    """Configuration des heures d'ouverture d'une branche"""
    enabled: bool = Field(default=True, description="Activer les restrictions horaires")
    startTime: str = Field(default="08:00", pattern=r'^([0-1][0-9]|2[0-3]):[0-5][0-9]$', description="Heure d'ouverture")
    endTime: str = Field(default="20:00", pattern=r'^([0-1][0-9]|2[0-3]):[0-5][0-9]$', description="Heure de fermeture")
    daysOff: Dict[str, bool] = Field(
        default={
            "monday": True,
            "tuesday": True,
            "wednesday": True,
            "thursday": True,
            "friday": True,
            "saturday": True,
            "sunday": False
        },
        description="Jours d'ouverture (True = ouvert)"
    )
    timezone: str = Field(default="Africa/Kinshasa", description="Fuseau horaire")


class BranchSpecificConfig(BaseModel):
    """Configuration spécifique à une branche"""
    lowStockThreshold: int = Field(default=10, ge=0, description="Seuil d'alerte stock bas")
    expiryWarningDays: int = Field(default=90, ge=0, description="Jours d'alerte avant expiration")
    allowNegativeStock: bool = Field(default=False, description="Autoriser stock négatif")
    enableBatchTracking: bool = Field(default=True, description="Activer le suivi par lot")
    workingHours: BranchWorkingHoursConfig = Field(default_factory=BranchWorkingHoursConfig)
    autoPricingEnabled: bool = Field(default=False, description="Prix automatique")
    defaultMargin: float = Field(default=25, ge=0, le=100, description="Marge par défaut")


class BranchConfigUpdate(BaseModel):
    """Mise à jour de la configuration d'une branche"""
    lowStockThreshold: Optional[int] = Field(None, ge=0)
    expiryWarningDays: Optional[int] = Field(None, ge=0)
    allowNegativeStock: Optional[bool] = None
    enableBatchTracking: Optional[bool] = None
    workingHours: Optional[BranchWorkingHoursConfig] = None
    autoPricingEnabled: Optional[bool] = None
    defaultMargin: Optional[float] = Field(None, ge=0, le=100)


# ==================== BRANCH SERVICE STATUS ====================

class BranchServiceStatus(BaseModel):
    """Statut de service d'une branche"""
    branch_id: UUID
    branch_name: str
    in_service: bool
    restrictions_enabled: bool
    current_time_local: str
    timezone: str
    current_day: str
    is_working_day: bool
    is_within_hours: bool
    working_hours: Dict[str, Any]
    message: str
    next_service_time: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


# ==================== BRANCH STATISTICS ====================

class BranchStatistics(BaseModel):
    """Statistiques d'une branche"""
    branch_id: UUID
    branch_name: str
    products_total: int
    products_low_stock: int
    products_expiring_soon: int
    products_out_of_stock: int
    sales_today: int
    sales_today_amount: float
    sales_this_week: int
    sales_this_week_amount: float
    sales_this_month: int
    sales_this_month_amount: float
    customers_total: int
    customers_active: int
    employees_count: int
    last_sale_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)


# ==================== BRANCH FILTERS ====================

class BranchFilters(BaseModel):
    """Filtres pour la recherche de branches"""
    search: Optional[str] = Field(None, description="Recherche par nom, code, ville")
    city: Optional[str] = Field(None, description="Filtrer par ville")
    country: Optional[str] = Field(None, description="Filtrer par pays")
    is_active: Optional[bool] = Field(None, description="Filtrer par statut")
    is_main_branch: Optional[bool] = Field(None, description="Filtrer par branche principale")
    has_manager: Optional[bool] = Field(None, description="Avec responsable assigné")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "search": "Centre",
                "city": "Kinshasa",
                "is_active": True
            }
        }
    )


# ==================== UTILITY FUNCTIONS ====================

def generate_branch_code(pharmacy_name: str, index: int) -> str:
    """
    Génère un code unique pour une branche
    """
    import re
    prefix = re.sub(r'[^A-Z0-9]', '', pharmacy_name.upper())[:3]
    return f"{prefix}{index:03d}"


def validate_opening_hours(opening_hours: Dict[str, str]) -> bool:
    """
    Valide le format des horaires d'ouverture
    """
    import re
    time_pattern = re.compile(r'^([0-1][0-9]|2[0-3]):[0-5][0-9]-([0-1][0-9]|2[0-3]):[0-5][0-9]$|^closed$')
    
    for day, hours in opening_hours.items():
        if not time_pattern.match(hours):
            return False
    return True
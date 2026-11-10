# app/schemas/customer.py
from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from enum import Enum
from uuid import UUID


class CustomerType(str, Enum):
    """Types de clients"""
    PARTICULIER = "particulier"
    PROFESSIONNEL = "professionnel"
    ASSUREUR = "assureur"
    ETAT = "etat"
    HOPITAL = "hopital"
    CLINIQUE = "clinique"


class CustomerCategory(str, Enum):
    """Catégories de clients"""
    STANDARD = "standard"
    PREMIUM = "premium"
    VIP = "vip"


class CustomerBase(BaseModel):
    """Base schema pour les clients"""
    nom: str = Field(..., min_length=2, max_length=100, description="Nom du client")
    prenom: Optional[str] = Field(None, max_length=100, description="Prénom du client")
    telephone: str = Field(..., max_length=20, description="Numéro de téléphone")
    email: Optional[EmailStr] = Field(None, description="Adresse email")
    adresse: Optional[str] = Field(None, description="Adresse postale")
    ville: Optional[str] = Field(None, max_length=100, description="Ville")
    code_postal: Optional[str] = Field(None, max_length=20, description="Code postal")
    pays: Optional[str] = Field("RDC", max_length=100, description="Pays")
    
    type_client: Optional[CustomerType] = Field(CustomerType.PARTICULIER, description="Type de client")
    category: Optional[CustomerCategory] = Field(CustomerCategory.STANDARD, description="Catégorie client")
    
    # Informations légales
    entreprise: Optional[str] = Field(None, max_length=100, description="Nom de l'entreprise")
    num_contribuable: Optional[str] = Field(None, max_length=50, description="Numéro de contribuable")
    rccm: Optional[str] = Field(None, max_length=50, description="Numéro RCCM")
    id_nat: Optional[str] = Field(None, max_length=50, description="Identifiant national")
    
    # Informations médicales
    birth_date: Optional[date] = Field(None, description="Date de naissance")
    blood_type: Optional[str] = Field(None, max_length=5, description="Groupe sanguin")
    allergies: Optional[str] = Field(None, description="Allergies")
    medical_notes: Optional[str] = Field(None, description="Notes médicales")
    
    # Assurance
    insurance_provider: Optional[str] = Field(None, max_length=255, description="Assureur")
    insurance_number: Optional[str] = Field(None, max_length=100, description="Numéro d'assurance")
    
    # Crédit
    credit_limit: Optional[float] = Field(0.0, ge=0, description="Limite de crédit")
    eligible_credit: Optional[bool] = Field(False, description="Éligible au crédit")
    
    # Notes et préférences
    notes: Optional[str] = Field(None, description="Notes internes")
    preferences: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Préférences")

    @validator("telephone")
    def validate_phone(cls, v):
        # Nettoyer le numéro
        v = ''.join(c for c in v if c.isdigit() or c == '+')
        
        if not v:
            raise ValueError("Numéro de téléphone requis")
        
        # Validation basique
        digits = ''.join(c for c in v if c.isdigit())
        if len(digits) < 9 or len(digits) > 15:
            raise ValueError("Numéro de téléphone invalide")
        
        return v


class CustomerCreate(CustomerBase):
    """Schema pour la création d'un client"""
    pass


class CustomerUpdate(BaseModel):
    """Schema pour la mise à jour d'un client"""
    nom: Optional[str] = Field(None, min_length=2, max_length=100)
    prenom: Optional[str] = Field(None, max_length=100)
    telephone: Optional[str] = Field(None, max_length=20)
    email: Optional[EmailStr] = None
    adresse: Optional[str] = None
    ville: Optional[str] = Field(None, max_length=100)
    code_postal: Optional[str] = Field(None, max_length=20)
    pays: Optional[str] = Field(None, max_length=100)
    type_client: Optional[CustomerType] = None
    category: Optional[CustomerCategory] = None
    entreprise: Optional[str] = Field(None, max_length=100)
    num_contribuable: Optional[str] = Field(None, max_length=50)
    rccm: Optional[str] = Field(None, max_length=50)
    id_nat: Optional[str] = Field(None, max_length=50)
    birth_date: Optional[date] = None
    blood_type: Optional[str] = Field(None, max_length=5)
    allergies: Optional[str] = None
    medical_notes: Optional[str] = None
    insurance_provider: Optional[str] = Field(None, max_length=255)
    insurance_number: Optional[str] = Field(None, max_length=100)
    credit_limit: Optional[float] = Field(None, ge=0)
    eligible_credit: Optional[bool] = None
    notes: Optional[str] = None
    preferences: Optional[Dict[str, Any]] = None
    blacklisted: Optional[bool] = None
    blacklist_reason: Optional[str] = None
    is_active: Optional[bool] = None


class CustomerInDB(CustomerBase):
    """Schema pour un client retourné par l'API"""
    id: UUID
    tenant_id: UUID
    pharmacy_id: Optional[UUID]
    branch_id: Optional[UUID]
    
    credit_score: Optional[int]
    dette_actuelle: float
    total_achats: float
    nombre_achats: int
    moyenne_achat: float
    loyalty_points: int
    
    date_inscription: Optional[datetime]
    dernier_achat: Optional[datetime]
    date_dernier_paiement: Optional[datetime]
    last_visit: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    created_by: Optional[UUID]
    
    is_active: bool
    blacklisted: bool
    blacklist_reason: Optional[str]
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    
    # Propriétés calculées
    full_name: str
    credit_available: float
    credit_utilization: float
    credit_status: str
    days_since_last_purchase: Optional[int]
    
    class Config:
        from_attributes = True


class CustomerStats(BaseModel):
    """Statistiques détaillées d'un client"""
    customer_id: UUID
    full_name: str
    total_achats: float
    nombre_achats: int
    moyenne_achat: float
    credit_limit: float
    dette_actuelle: float
    credit_available: float
    credit_score: Optional[int]
    credit_utilization: float
    credit_status: str
    loyalty_points: int
    category: str
    days_since_last_purchase: Optional[int]
    last_payment_date: Optional[datetime]
    days_since_last_payment: Optional[int]
    eligible_credit: bool
    blacklisted: bool
    total_orders: Optional[int] = 0
    total_spent: Optional[float] = 0


class CustomerSearchResult(BaseModel):
    """Résultat de recherche de client"""
    id: UUID
    full_name: str
    nom: str
    prenom: Optional[str]
    telephone: str
    email: Optional[str]
    entreprise: Optional[str]
    type_client: str
    category: str
    dette_actuelle: float
    credit_available: float
    loyalty_points: int


class CustomerDebtInfo(BaseModel):
    """Informations de dette d'un client"""
    customer_id: UUID
    full_name: str
    credit_limit: float
    dette_actuelle: float
    credit_available: float
    credit_utilization: float
    eligible_credit: bool
    last_payment_date: Optional[datetime]
    risk_level: str  # low, medium, high
    debts_history: List[Dict[str, Any]] = []
    total_paid: float = 0
    pending_debts_count: int = 0


class CustomerSummary(BaseModel):
    """Résumé des clients"""
    total_customers: int
    active_customers: int
    customers_with_credit: int
    blacklisted_customers: int
    total_debt: float
    total_sales: float
    total_loyalty_points: int
    customers_by_type: List[Dict[str, Any]]
    customers_by_category: List[Dict[str, Any]]
    top_customers: List[Dict[str, Any]]


class CustomerLoyaltyInfo(BaseModel):
    """Informations de fidélité"""
    customer_id: UUID
    full_name: str
    loyalty_points: int
    category: str
    points_to_next_tier: int
    next_tier: Optional[str]
    total_orders: int
    total_spent: float
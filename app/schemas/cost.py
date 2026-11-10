# app/schemas/cost.py
from pydantic import BaseModel, Field, validator, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from uuid import UUID
from enum import Enum

class CostCategoryEnum(str, Enum):
    SALARY = "salary"
    RENT = "rent"
    UTILITIES = "utilities"
    MAINTENANCE = "maintenance"
    SUPPLIES = "supplies"
    MARKETING = "marketing"
    SOFTWARE = "software"
    INSURANCE = "insurance"
    TRANSPORT = "transport"
    TRAINING = "training"
    CONSULTING = "consulting"
    TAXES = "taxes"
    OTHER = "other"

class CostFrequencyEnum(str, Enum):
    ONE_TIME = "one_time"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"

class PaymentMethodEnum(str, Enum):
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    MOBILE_MONEY = "mobile_money"
    CHECK = "check"
    CREDIT_CARD = "credit_card"

class PeriodTypeEnum(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"

class CostBase(BaseModel):
    """Schéma de base pour un coût"""
    category: CostCategoryEnum
    subcategory: Optional[str] = None
    amount: float = Field(..., gt=0)
    tax_amount: float = Field(0.0, ge=0)
    description: Optional[str] = None
    payment_date: date = Field(default_factory=date.today)
    payment_method: PaymentMethodEnum = PaymentMethodEnum.CASH
    is_paid: bool = True
    
    # Facturation
    invoice_number: Optional[str] = None
    supplier_id: Optional[UUID] = None
    
    # Récurrence
    is_recurring: bool = False
    frequency: CostFrequencyEnum = CostFrequencyEnum.ONE_TIME
    recurring_until: Optional[date] = None
    
    # Budget
    budget_id: Optional[UUID] = None
    
    # Métadonnées
    notes: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

class CostCreate(CostBase):
    """Création d'un coût"""
    @validator('recurring_until')
    def validate_recurring_until(cls, v, values):
        if values.get('is_recurring') and not v:
            raise ValueError('Date de fin requise pour les coûts récurrents')
        return v
    
    @validator('payment_date')
    def validate_payment_date(cls, v):
        if v > date.today():
            raise ValueError('La date de paiement ne peut pas être dans le futur')
        return v

class CostUpdate(BaseModel):
    """Mise à jour d'un coût"""
    amount: Optional[float] = Field(None, gt=0)
    tax_amount: Optional[float] = Field(None, ge=0)
    description: Optional[str] = None
    payment_date: Optional[date] = None
    payment_method: Optional[PaymentMethodEnum] = None
    is_paid: Optional[bool] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None

class CostInDB(CostBase):
    """Coût tel que stocké en base"""
    id: UUID
    tenant_id: UUID
    total_amount: float
    currency: str
    exchange_rate: float = 1.0
    
    # Responsables
    created_by: UUID
    approved_by: Optional[UUID] = None
    
    # Documents
    document_url: Optional[str] = None
    
    # Timestamps
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class CostAllocationBase(BaseModel):
    """Schéma de base pour une allocation de coût"""
    cost_id: UUID
    department_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    allocation_percentage: float = Field(..., ge=0, le=100)
    notes: Optional[str] = None

class CostAllocationCreate(CostAllocationBase):
    """Création d'une allocation de coût"""
    @model_validator(mode='after')
    def validate_allocation(cls, values):
        department_id = values.get('department_id')
        project_id = values.get('project_id')
        
        if not department_id and not project_id:
            raise ValueError('Au moins un département ou projet doit être spécifié')
        
        return values

class BudgetBase(BaseModel):
    """Schéma de base pour un budget"""
    name: str
    description: Optional[str] = None
    category: CostCategoryEnum
    period_type: PeriodTypeEnum
    start_date: date
    end_date: date
    allocated_amount: float = Field(..., gt=0)
    warning_threshold: float = Field(80.0, ge=0, le=100)
    critical_threshold: float = Field(95.0, ge=0, le=100)

class BudgetCreate(BudgetBase):
    """Création d'un budget"""
    @validator('end_date')
    def validate_end_date(cls, v, values):
        if 'start_date' in values and v <= values['start_date']:
            raise ValueError('La date de fin doit être après la date de début')
        return v
    
    @validator('critical_threshold')
    def validate_thresholds(cls, v, values):
        if 'warning_threshold' in values and v <= values['warning_threshold']:
            raise ValueError('Le seuil critique doit être supérieur au seuil d\'alerte')
        return v

class BudgetInDB(BudgetBase):
    """Budget tel que stocké en base"""
    id: UUID
    tenant_id: UUID
    spent_amount: float = 0.0
    remaining_amount: float = 0.0
    is_active: bool = True
    owner_id: UUID
    
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class SupplierBase(BaseModel):
    """Schéma de base pour un fournisseur"""
    name: str
    company_name: Optional[str] = None
    tax_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account: Optional[str] = None
    payment_terms: Optional[str] = None
    categories: List[str] = Field(default_factory=list)

class SupplierCreate(SupplierBase):
    """Création d'un fournisseur"""
    pass

class SupplierInDB(SupplierBase):
    """Fournisseur tel que stocké en base"""
    id: UUID
    tenant_id: UUID
    rating: float = 0.0
    is_active: bool = True
    
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class CostSummary(BaseModel):
    """Résumé des coûts"""
    period: str
    total_costs: float
    by_category: Dict[str, float]
    by_month: Dict[str, float]
    average_monthly: float
    top_costs: List[Dict[str, Any]]
    budget_variance: Dict[str, float]

class CostAnalytics(BaseModel):
    """Analyses des coûts"""
    monthly_trend: List[Dict[str, Any]]
    category_distribution: List[Dict[str, Any]]
    supplier_analysis: List[Dict[str, Any]]
    variance_analysis: Dict[str, float]
    recommendations: List[str]
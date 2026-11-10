# app/schemas/finance.py
from pydantic import BaseModel, Field, validator, root_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from uuid import UUID
from enum import Enum
from decimal import Decimal

class PeriodType(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"

class TransactionType(str, Enum):
    SALE = "sale"
    PURCHASE = "purchase"
    EXPENSE = "expense"
    INCOME = "income"
    TRANSFER = "transfer"
    ADJUSTMENT = "adjustment"

class CapitalType(str, Enum):
    INITIAL = "initial"
    ADDITIONAL = "additional"
    WITHDRAWAL = "withdrawal"
    REINVESTMENT = "reinvestment"

class ExpenseType(str, Enum):
    SALARY = "salary"
    RENT = "rent"
    UTILITIES = "utilities"
    SUPPLIES = "supplies"
    MARKETING = "marketing"
    MAINTENANCE = "maintenance"
    OTHER = "other"

# ==============================================
# SCHEMAS PERIODES FINANCIERES
# ==============================================

class FinancialPeriodBase(BaseModel):
    """Schéma de base pour une période financière"""
    period_type: PeriodType
    period_start: date
    period_end: date
    period_name: str = Field(..., max_length=100)
    notes: Optional[str] = None

class FinancialPeriodCreate(FinancialPeriodBase):
    """Création d'une période financière"""
    @validator('period_end')
    def validate_period_range(cls, v, values):
        period_start = values.get('period_start')
        if period_start and v < period_start:
            raise ValueError('La date de fin doit être postérieure à la date de début')
        return v

class FinancialPeriodUpdate(BaseModel):
    """Mise à jour d'une période financière"""
    notes: Optional[str] = None
    is_closed: Optional[bool] = None

class FinancialPeriodInDB(FinancialPeriodBase):
    """Période financière telle que stockée en base"""
    id: UUID
    tenant_id: UUID
    
    # Chiffre d'affaires
    total_sales: Decimal
    total_cost: Decimal
    gross_profit: Decimal
    gross_margin: float
    
    # Dépenses
    total_expenses: Decimal
    net_profit: Decimal
    net_margin: float
    
    # Statut
    is_closed: bool
    closed_by: Optional[UUID] = None
    closed_at: Optional[datetime] = None
    
    # Timestamps
    created_at: datetime
    
    class Config:
        from_attributes = True

# ==============================================
# SCHEMAS TRANSACTIONS FINANCIERES
# ==============================================

class FinancialTransactionBase(BaseModel):
    """Schéma de base pour une transaction financière"""
    transaction_date: date
    transaction_type: TransactionType
    reference: Optional[str] = Field(None, max_length=100)
    amount: Decimal = Field(..., gt=0)
    tax_amount: Decimal = Field(0, ge=0)
    category: Optional[str] = Field(None, max_length=100)
    subcategory: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None

class FinancialTransactionCreate(FinancialTransactionBase):
    """Création d'une transaction financière"""
    @property
    def total_amount(self) -> Decimal:
        return self.amount + self.tax_amount

class FinancialTransactionUpdate(BaseModel):
    """Mise à jour d'une transaction financière"""
    category: Optional[str] = None
    subcategory: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    is_reconciled: Optional[bool] = None

class FinancialTransactionInDB(FinancialTransactionBase):
    """Transaction financière telle que stockée en base"""
    id: UUID
    tenant_id: UUID
    period_id: Optional[UUID] = None
    total_amount: Decimal
    is_reconciled: bool
    reconciled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

# ==============================================
# SCHEMAS CAPITAL
# ==============================================

class CapitalBase(BaseModel):
    """Schéma de base pour une opération sur le capital"""
    capital_type: CapitalType
    capital_date: date
    amount: Decimal = Field(..., gt=0)
    description: Optional[str] = Field(None, max_length=500)
    reference: Optional[str] = Field(None, max_length=100)
    source: Optional[str] = Field(None, max_length=200)
    destination: Optional[str] = Field(None, max_length=200)

class CapitalCreate(CapitalBase):
    """Création d'une opération sur le capital"""

class CapitalUpdate(BaseModel):
    """Mise à jour d'une opération sur le capital"""
    status: Optional[str] = Field(None, pattern="^(pending|approved|completed|cancelled)$")
    description: Optional[str] = None
    notes: Optional[str] = None

class CapitalInDB(CapitalBase):
    """Opération sur le capital telle que stockée en base"""
    id: UUID
    tenant_id: UUID
    status: str
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

# ==============================================
# SCHEMAS DEPENSES
# ==============================================

class ExpenseBase(BaseModel):
    """Schéma de base pour une dépense"""
    expense_date: date
    expense_type: ExpenseType
    amount: Decimal = Field(..., gt=0)
    tax_amount: Decimal = Field(0, ge=0)
    supplier: Optional[str] = Field(None, max_length=200)
    payee: Optional[str] = Field(None, max_length=200)
    payment_method: Optional[str] = Field(None, max_length=30)
    payment_reference: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    invoice_number: Optional[str] = Field(None, max_length=100)
    invoice_date: Optional[date] = None
    notes: Optional[str] = None

class ExpenseCreate(ExpenseBase):
    """Création d'une dépense"""
    @property
    def total_amount(self) -> Decimal:
        return self.amount + self.tax_amount

class ExpenseUpdate(BaseModel):
    """Mise à jour d'une dépense"""
    description: Optional[str] = None
    notes: Optional[str] = None
    payment_reference: Optional[str] = None

class ExpenseInDB(ExpenseBase):
    """Dépense telle que stockée en base"""
    id: UUID
    tenant_id: UUID
    transaction_id: Optional[UUID] = None
    total_amount: Decimal
    is_recurring: bool = False
    recurrence_interval: Optional[str] = None
    next_due_date: Optional[date] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

# ==============================================
# SCHEMAS ANALYSE FINANCIERE
# ==============================================

class FinancialAnalysisRequest(BaseModel):
    """Requête pour une analyse financière"""
    start_date: date
    end_date: date
    period_type: PeriodType = PeriodType.MONTHLY
    compare_with_previous: bool = True
    include_breakdown: bool = True

class FinancialKPIs(BaseModel):
    """Indicateurs clés de performance financière"""
    period: str
    total_sales: Decimal
    total_expenses: Decimal
    gross_profit: Decimal
    gross_margin: float
    net_profit: Decimal
    net_margin: float
    
    # Croissance
    sales_growth: Optional[float] = None
    profit_growth: Optional[float] = None
    
    # Ratios
    expense_ratio: float  # Dépenses / CA
    profitability_ratio: float  # Profit net / CA
    stock_turnover: Optional[float] = None  # Rotation des stocks
    
    # Alertes
    alerts: List[str] = Field(default_factory=list)

class MonthlyAnalysis(BaseModel):
    """Analyse mensuelle"""
    month: str
    year: int
    total_sales: Decimal
    total_expenses: Decimal
    net_profit: Decimal
    sales_growth: Optional[float] = None
    best_day: Optional[Dict[str, Any]] = None
    worst_day: Optional[Dict[str, Any]] = None
    average_cart: Decimal
    active_days: int

class AnnualAnalysis(BaseModel):
    """Analyse annuelle"""
    year: int
    total_sales: Decimal
    total_expenses: Decimal
    net_profit: Decimal
    yearly_growth: Optional[float] = None
    best_month: Optional[Dict[str, Any]] = None
    worst_month: Optional[Dict[str, Any]] = None
    quarterly_breakdown: Dict[str, Decimal]
    monthly_trend: List[Decimal]

class StockValuation(BaseModel):
    """Évaluation du stock"""
    total_purchase_value: Decimal
    total_selling_value: Decimal
    potential_profit: Decimal
    average_margin: float
    category_breakdown: Dict[str, Dict[str, Any]]
    expiry_analysis: Dict[str, int]  # Produits expirés, à expirer, etc.
    low_stock_alerts: List[Dict[str, Any]]

class CapitalAnalysis(BaseModel):
    """Analyse du capital"""
    initial_capital: Decimal
    additional_investments: Decimal
    withdrawals: Decimal
    current_capital: Decimal
    capital_growth: float
    return_on_investment: float
    capital_history: List[Dict[str, Any]]

class FinancialDashboard(BaseModel):
    """Tableau de bord financier"""
    # KPIs du jour
    today_sales: Decimal
    today_expenses: Decimal
    today_profit: Decimal
    
    # KPIs du mois
    month_to_date_sales: Decimal
    month_to_date_expenses: Decimal
    month_to_date_profit: Decimal
    monthly_target: Optional[Decimal] = None
    target_achievement: Optional[float] = None
    
    # KPIs de l'année
    year_to_date_sales: Decimal
    year_to_date_expenses: Decimal
    year_to_date_profit: Decimal
    
    # Valeurs
    stock_value: Decimal
    total_capital: Decimal
    cash_balance: Decimal
    
    # Alertes
    alerts: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)

class ExportRequest(BaseModel):
    """Requête d'export"""
    format: str = Field(..., pattern="^(excel|pdf|csv)$")
    analysis_type: str = Field(..., pattern="^(monthly|annual|stock|capital)$")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    include_charts: bool = True
# app/schemas/expense.py
from pydantic import BaseModel, Field, validator
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List
from uuid import UUID
from enum import Enum

# =====================================
# ENUMS
# =====================================
class ExpenseType(str, Enum):
    SALAIRE = "salaire"
    LOYER = "loyer"
    ELECTRICITE = "electricite"
    EAU = "eau"
    INTERNET = "internet"
    TELEPHONE = "telephone"
    FOURNITURES = "fournitures"
    MARKETING = "marketing"
    TRANSPORT = "transport"
    MAINTENANCE = "maintenance"
    LOGICIEL = "logiciel"
    ASSURANCE = "assurance"
    FRAIS_BANCAIRES = "frais_bancaires"
    IMPOTS = "impots"
    DIVERSE = "diverse"

class PaymentMethod(str, Enum):
    CASH = "cash"
    MOBILE_MONEY = "mobile_money"
    VIREMENT = "virement"
    CHEQUE = "cheque"
    CARTE = "carte"

class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

# =====================================
# BASE SCHEMA
# =====================================
class ExpenseBase(BaseModel):
    branch_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    expense_date: date
    expense_type: ExpenseType
    amount: Decimal = Field(..., gt=0, decimal_places=2)
    tax_amount: Decimal = Field(default=0, ge=0, decimal_places=2)
    total_amount: Optional[Decimal] = None
    supplier: Optional[str] = Field(None, max_length=200)
    payee: Optional[str] = Field(None, max_length=200)
    payment_method: Optional[PaymentMethod] = None
    payment_reference: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None
    invoice_number: Optional[str] = Field(None, max_length=100)
    invoice_date: Optional[date] = None
    is_recurring: bool = False
    recurrence_interval: Optional[str] = Field(None, max_length=30)
    next_due_date: Optional[date] = None
    cost_center: Optional[str] = Field(None, max_length=100)
    project_code: Optional[str] = Field(None, max_length=50)
    
    @validator('total_amount', pre=True, always=True)
    def calculate_total_amount(cls, v, values):
        if v is not None:
            return v
        amount = values.get('amount', 0)
        tax = values.get('tax_amount', 0)
        return amount + tax
    
    @validator('expense_date')
    def validate_expense_date(cls, v):
        if v > date.today():
            raise ValueError("La date de dépense ne peut pas être dans le futur")
        return v

# =====================================
# CREATE SCHEMA
# =====================================
class ExpenseCreate(ExpenseBase):
    pass

# =====================================
# UPDATE SCHEMA
# =====================================
class ExpenseUpdate(BaseModel):
    branch_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    expense_date: Optional[date] = None
    expense_type: Optional[ExpenseType] = None
    amount: Optional[Decimal] = Field(None, gt=0, decimal_places=2)
    tax_amount: Optional[Decimal] = Field(None, ge=0, decimal_places=2)
    total_amount: Optional[Decimal] = Field(None, decimal_places=2)
    supplier: Optional[str] = Field(None, max_length=200)
    payee: Optional[str] = Field(None, max_length=200)
    payment_method: Optional[PaymentMethod] = None
    payment_reference: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None
    invoice_number: Optional[str] = Field(None, max_length=100)
    invoice_date: Optional[date] = None
    is_recurring: Optional[bool] = None
    recurrence_interval: Optional[str] = Field(None, max_length=30)
    next_due_date: Optional[date] = None
    cost_center: Optional[str] = Field(None, max_length=100)
    project_code: Optional[str] = Field(None, max_length=50)
    
    @validator('total_amount', pre=True, always=True)
    def calculate_total_amount(cls, v, values):
        if v is not None:
            return v
        amount = values.get('amount')
        tax = values.get('tax_amount')
        if amount is not None and tax is not None:
            return amount + tax
        return v

# =====================================
# APPROVAL SCHEMA
# =====================================
class ExpenseApprove(BaseModel):
    approved: bool = Field(..., description="True pour approuver, False pour rejeter")
    rejection_reason: Optional[str] = Field(None, max_length=500, description="Raison du rejet (requis si approved=False)")

# =====================================
# RESPONSE SCHEMA
# =====================================
class ExpenseResponse(ExpenseBase):
    id: UUID
    tenant_id: UUID
    approval_status: ApprovalStatus
    approved_by: Optional[UUID] = None
    rejection_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    # Relations (optionnelles)
    branch_name: Optional[str] = None
    user_name: Optional[str] = None
    approver_name: Optional[str] = None
    
    class Config:
        from_attributes = True

# =====================================
# LISTE AVEC PAGINATION
# =====================================
class ExpenseListResponse(BaseModel):
    items: List[ExpenseResponse]
    total: int
    page: int
    per_page: int
    total_pages: int

# =====================================
# RAPPORTS
# =====================================
class ExpenseByBranchResponse(BaseModel):
    branch_id: str
    branch_name: str
    total_expenses: float
    expense_count: int
    average_expense: float
    percentage_of_total: Optional[float] = None

class ExpenseByUserResponse(BaseModel):
    user_id: str
    username: str
    email: str
    total_expenses: float
    expense_count: int
    average_expense: float
    percentage_of_total: Optional[float] = None

class ExpenseSummaryResponse(BaseModel):
    total_expenses: float
    total_count: int
    average_expense: float
    by_category: dict
    by_status: dict
    period_start: date
    period_end: date

# =====================================
# FILTRES
# =====================================
class ExpenseFilters(BaseModel):
    branch_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    expense_type: Optional[ExpenseType] = None
    approval_status: Optional[ApprovalStatus] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    search: Optional[str] = Field(None, description="Recherche dans description, supplier, invoice_number")
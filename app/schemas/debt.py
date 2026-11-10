# app/schemas/debt.py
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from uuid import UUID
from enum import Enum

class DebtStatus(str, Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    PAID = "paid"
    CANCELLED = "cancelled"

class PaymentMethod(str, Enum):
    CASH = "cash"
    MOBILE_MONEY = "mobile_money"
    BANK_TRANSFER = "bank_transfer"
    CHECK = "check"

class DebtBase(BaseModel):
    customer_id: UUID  
    total_amount: float = Field(..., gt=0)
    due_date: date
    description: Optional[str] = None
    terms: Optional[str] = None

class DebtCreate(DebtBase):
    @validator('due_date')
    def validate_due_date(cls, v):
        if v < date.today():
            raise ValueError('La date d\'échéance ne peut pas être dans le passé')
        return v

class DebtUpdate(BaseModel):
    status: Optional[DebtStatus] = None
    description: Optional[str] = None
    terms: Optional[str] = None

class DebtInDB(DebtBase):
    id: UUID
    tenant_id: UUID
    debt_number: str
    remaining_amount: float
    total_paid: float
    status: DebtStatus
    is_overdue: bool
    customer_name: str  # Changé: client_name -> customer_name
    
    # Dates
    paid_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class DebtPaymentCreate(BaseModel):
    amount: float = Field(..., gt=0)
    payment_method: PaymentMethod
    payment_date: date = Field(default_factory=date.today)
    reference: Optional[str] = None
    notes: Optional[str] = None

class DebtPaymentInDB(BaseModel):
    id: UUID
    debt_id: UUID
    amount: float
    payment_method: str
    payment_date: date
    reference: Optional[str]
    notes: Optional[str]
    received_by: UUID
    
    created_at: datetime
    
    class Config:
        from_attributes = True

class DebtSummary(BaseModel):
    total_amount: float
    total_received: float
    total_overdue: float
    total_customers: int  # Changé: total_clients -> total_customers
    status_summary: Dict[str, Dict[str, Any]]

class DebtAnalytics(BaseModel):
    recent_debts: List[DebtInDB]
    oldest_debts: List[DebtInDB]
    top_debtors: List[Dict[str, Any]]
    payment_methods: List[Dict[str, Any]]
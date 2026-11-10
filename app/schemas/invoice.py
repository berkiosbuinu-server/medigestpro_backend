# app/schemas/invoice.py
from pydantic import BaseModel, Field
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from uuid import UUID
from enum import Enum


class InvoiceStatusEnum(str, Enum):
    DRAFT = "draft"
    SENT = "sent"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class InvoiceTypeEnum(str, Enum):
    SUBSCRIPTION = "subscription"
    ONE_TIME = "one_time"
    RENEWAL = "renewal"


class InvoicePaymentResponse(BaseModel):
    id: UUID
    invoice_id: UUID
    amount: float
    payment_method: Optional[str] = None
    payment_reference: Optional[str] = None
    payment_date: datetime
    status: str
    notes: Optional[str] = None
    created_at: datetime


class InvoiceResponse(BaseModel):
    id: UUID
    invoice_number: str
    invoice_type: str
    pharmacy_id: UUID
    pharmacy_name: Optional[str] = None
    tenant_id: UUID
    period_start: datetime
    period_end: datetime
    subtotal: float = 0
    tax_rate: float = 0
    tax_amount: float = 0
    discount_amount: float = 0
    total_amount: float = 0
    currency: str = "EUR"
    status: str
    issue_date: datetime
    due_date: datetime
    paid_at: Optional[datetime] = None
    description: Optional[str] = None
    subscription_plan: Optional[str] = None
    billing_cycle: Optional[str] = None
    payment_method: Optional[str] = None
    payment_reference: Optional[str] = None
    total_paid: float = 0
    remaining_amount: float = 0
    is_overdue: bool = False
    days_overdue: int = 0
    created_at: datetime
    updated_at: datetime


class InvoiceDetailResponse(InvoiceResponse):
    invoice_metadata: Optional[Dict[str, Any]] = None
    payments: List[InvoicePaymentResponse] = []


class InvoiceListResponse(BaseModel):
    items: List[InvoiceResponse]
    total: int
    page: int
    size: int
    has_more: bool
    page_size: int


class InvoiceCreate(BaseModel):
    pharmacy_id: UUID
    invoice_type: InvoiceTypeEnum = InvoiceTypeEnum.SUBSCRIPTION
    period_start: datetime
    period_end: datetime
    subtotal: float = 0
    tax_rate: float = 0
    tax_amount: float = 0
    discount_amount: float = 0
    total_amount: float
    currency: str = "EUR"
    due_date: datetime
    description: Optional[str] = None
    subscription_plan: Optional[str] = None
    billing_cycle: Optional[str] = None
    invoice_metadata: Optional[Dict[str, Any]] = None


class InvoiceUpdate(BaseModel):
    status: Optional[InvoiceStatusEnum] = None
    payment_method: Optional[str] = None
    payment_reference: Optional[str] = None
    paid_at: Optional[datetime] = None
    description: Optional[str] = None
    invoice_metadata: Optional[Dict[str, Any]] = None


class InvoiceFilter(BaseModel):
    pharmacy_id: Optional[UUID] = None
    status: Optional[InvoiceStatusEnum] = None
    invoice_type: Optional[InvoiceTypeEnum] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    due_start_date: Optional[date] = None
    due_end_date: Optional[date] = None
    search: Optional[str] = None


class InvoiceStatsResponse(BaseModel):
    total_invoices: int
    total_amount: float
    paid: Dict[str, Any]
    pending: Dict[str, Any]
    overdue: Dict[str, Any]
    by_pharmacy: Optional[List[Dict[str, Any]]] = None


class InvoicePaymentCreate(BaseModel):
    invoice_id: UUID
    amount: float
    payment_method: str = "cash"
    payment_reference: Optional[str] = None
    payment_date: Optional[datetime] = None
    notes: Optional[str] = None
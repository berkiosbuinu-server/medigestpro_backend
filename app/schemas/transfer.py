# app/schemas/transfer.py
from typing import List, Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field

from app.models.transfert import TransferStatus, TransferType, TransferPriority


class TransferItemBase(BaseModel):
    product_id: UUID
    requested_quantity: int = Field(..., ge=1)
    unit_price: float = Field(..., ge=0)
    batch_number: Optional[str] = None
    expiry_date: Optional[datetime] = None
    notes: Optional[str] = None


class TransferItemCreate(TransferItemBase):
    pass


class TransferItemUpdate(BaseModel):
    approved_quantity: Optional[int] = Field(None, ge=0)
    transferred_quantity: Optional[int] = Field(None, ge=0)
    received_quantity: Optional[int] = Field(None, ge=0)
    notes: Optional[str] = None


class TransferItemInDB(TransferItemBase):
    id: UUID
    transfer_id: UUID
    product_code: Optional[str]
    product_name: str
    approved_quantity: Optional[int]
    transferred_quantity: int
    received_quantity: int
    total_price: float
    status: TransferStatus
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class TransferBase(BaseModel):
    source_pharmacy_id: UUID
    destination_pharmacy_id: UUID
    transfer_type: TransferType = TransferType.INTERNAL
    priority: TransferPriority = TransferPriority.MEDIUM
    reason: Optional[str] = None
    notes: Optional[str] = None
    expected_delivery_date: Optional[datetime] = None
    shipping_cost: float = 0.0


class TransferCreate(TransferBase):
    items: List[TransferItemCreate]


class TransferUpdate(BaseModel):
    priority: Optional[TransferPriority] = None
    notes: Optional[str] = None
    expected_delivery_date: Optional[datetime] = None
    shipping_cost: Optional[float] = None


class TransferApprove(BaseModel):
    notes: Optional[str] = None


class TransferShip(BaseModel):
    tracking_number: Optional[str] = None
    notes: Optional[str] = None


class TransferReceive(BaseModel):
    items: List[TransferItemUpdate]
    notes: Optional[str] = None


class TransferCancel(BaseModel):
    reason: str


class TransferInDB(TransferBase):
    id: UUID
    tenant_id: UUID
    transfer_number: str
    status: TransferStatus
    requested_date: datetime
    approved_date: Optional[datetime]
    prepared_date: Optional[datetime]
    shipped_date: Optional[datetime]
    completed_date: Optional[datetime]
    cancelled_date: Optional[datetime]
    tracking_number: Optional[str]
    total_items: int
    total_quantity_requested: int
    total_quantity_transferred: int
    total_quantity_received: int
    total_value: float
    is_urgent: bool
    is_completed: bool
    has_discrepancy: bool
    created_at: datetime
    updated_at: datetime
    
    # Relations
    source_pharmacy: Optional[dict]
    destination_pharmacy: Optional[dict]
    requested_by: Optional[dict]
    approved_by: Optional[dict]
    prepared_by: Optional[dict]
    shipped_by: Optional[dict]
    received_by: Optional[dict]
    cancelled_by: Optional[dict]
    items: List[TransferItemInDB] = []
    
    class Config:
        from_attributes = True


class TransferListResponse(BaseModel):
    transfers: List[TransferInDB]
    total: int
    skip: int
    limit: int


class TransferStatistics(BaseModel):
    pending_incoming: int = 0
    pending_outgoing: int = 0
    in_transit_incoming: int = 0
    in_transit_outgoing: int = 0
    completed_this_month: int = 0
    total_value_transferred: List = []
    total_value_transferred_sum: float = 0.0
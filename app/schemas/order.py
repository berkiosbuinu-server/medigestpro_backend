# app/schemas/order.py

from pydantic import BaseModel, Field, validator
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum


class OrderStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class OrderItemBase(BaseModel):
    """Modèle de base pour un article de commande"""
    product_id: Optional[str] = None
    name: str
    quantity: int = Field(gt=0)
    price: float = Field(ge=0)
    total: float = Field(ge=0)
    
    @validator('total')
    def validate_total(cls, v, values):
        """Valide que le total correspond à quantité * prix"""
        if 'quantity' in values and 'price' in values:
            expected_total = values['quantity'] * values['price']
            if abs(v - expected_total) > 0.01:  # Tolérance pour les floats
                raise ValueError(f"Le total {v} ne correspond pas à quantité * prix ({expected_total})")
        return v


class OrderBase(BaseModel):
    """Modèle de base pour une commande"""
    customer_id: Optional[str] = None
    customer_name: str
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_address: Optional[Dict[str, Any]] = None
    items: List[OrderItemBase]
    subtotal: float = Field(ge=0)
    tax_amount: float = Field(ge=0, default=0)
    shipping_amount: float = Field(ge=0, default=0)
    discount_amount: float = Field(ge=0, default=0)
    total_amount: float = Field(ge=0)
    status: OrderStatus = OrderStatus.PENDING
    payment_status: PaymentStatus = PaymentStatus.PENDING
    payment_method: Optional[str] = None
    shipping_method: Optional[str] = None
    notes: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None
    
    @validator('total_amount')
    def validate_total_amount(cls, v, values):
        """Valide que le total correspond à la somme des composants"""
        subtotal = values.get('subtotal', 0)
        tax = values.get('tax_amount', 0)
        shipping = values.get('shipping_amount', 0)
        discount = values.get('discount_amount', 0)
        expected_total = subtotal + tax + shipping - discount
        if abs(v - expected_total) > 0.01:
            raise ValueError(f"Le total {v} ne correspond pas à la somme des composants ({expected_total})")
        return v


class OrderCreate(OrderBase):
    """Modèle pour la création d'une commande"""
    order_number: Optional[str] = None
    
    @validator('order_number', always=True)
    def generate_order_number(cls, v):
        """Génère un numéro de commande si non fourni"""
        if v is None:
            from datetime import datetime
            return f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{id(v) % 10000:04d}"
        return v


class OrderUpdate(BaseModel):
    """Modèle pour la mise à jour d'une commande"""
    status: Optional[OrderStatus] = None
    payment_status: Optional[PaymentStatus] = None
    payment_method: Optional[str] = None
    payment_id: Optional[str] = None
    paid_at: Optional[datetime] = None
    shipping_method: Optional[str] = None
    tracking_number: Optional[str] = None
    tracking_url: Optional[str] = None
    shipped_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    notes: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None


class OrderResponse(OrderBase):
    """Modèle de réponse pour une commande"""
    id: str
    tenant_id: str
    order_number: str
    payment_id: Optional[str] = None
    paid_at: Optional[datetime] = None
    tracking_number: Optional[str] = None
    tracking_url: Optional[str] = None
    shipped_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class OrderListResponse(BaseModel):
    """Modèle de réponse pour une liste de commandes"""
    items: List[OrderResponse]
    total: int
    page: int
    size: int
    pages: int


class OrderStatusUpdate(BaseModel):
    """Modèle pour la mise à jour du statut"""
    status: OrderStatus
    notes: Optional[str] = None


class OrderPaymentUpdate(BaseModel):
    """Modèle pour la mise à jour du paiement"""
    payment_status: PaymentStatus
    payment_id: Optional[str] = None
    paid_at: Optional[datetime] = None
    notes: Optional[str] = None
# app/models/order.py

from sqlalchemy import Column, String, Float, Integer, DateTime, ForeignKey, JSON, Enum as SQLEnum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
import uuid

from app.db.base import Base


class OrderStatus(str, enum.Enum):
    """Statuts possibles pour une commande"""
    PENDING = "pending"
    PROCESSING = "processing"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentStatus(str, enum.Enum):
    """Statuts de paiement"""
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class Order(Base):
    """Modèle de commande"""
    __tablename__ = "orders"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Informations client
    customer_id = Column(String(36), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True)
    customer_name = Column(String(255), nullable=False)
    customer_email = Column(String(255), nullable=True)
    customer_phone = Column(String(50), nullable=True)
    customer_address = Column(JSON, nullable=True)  # Adresse complète en JSON
    
    # Détails de la commande
    order_number = Column(String(50), unique=True, nullable=False, index=True)
    items = Column(JSON, nullable=False, default=list)  # Liste des produits: [{"product_id": "", "name": "", "quantity": 0, "price": 0, "total": 0}]
    subtotal = Column(Float, nullable=False, default=0.0)
    tax_amount = Column(Float, nullable=False, default=0.0)
    shipping_amount = Column(Float, nullable=False, default=0.0)
    discount_amount = Column(Float, nullable=False, default=0.0)
    total_amount = Column(Float, nullable=False, default=0.0)
    
    # Statuts
    status = Column(SQLEnum(OrderStatus), nullable=False, default=OrderStatus.PENDING)
    payment_status = Column(SQLEnum(PaymentStatus), nullable=False, default=PaymentStatus.PENDING)
    
    # Paiement
    payment_method = Column(String(50), nullable=True)  # card, cash, bank_transfer, etc.
    payment_id = Column(String(255), nullable=True)  # ID de transaction externe
    paid_at = Column(DateTime, nullable=True)
    
    # Livraison
    shipping_method = Column(String(100), nullable=True)
    tracking_number = Column(String(255), nullable=True)
    tracking_url = Column(String(500), nullable=True)
    shipped_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    
    # Métadonnées
    notes = Column(JSON, nullable=True, default=list)  # Notes sur la commande
    order_metadata = Column(JSON, nullable=True)  # Métadonnées additionnelles
    
    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)  # Soft delete
    
    # Relations
    customer = relationship("Customer", back_populates="orders")
    tenant = relationship("Tenant", back_populates="orders")
    
    def to_dict(self) -> dict:
        """Convertit l'objet en dictionnaire"""
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "customer_id": self.customer_id,
            "customer_name": self.customer_name,
            "customer_email": self.customer_email,
            "customer_phone": self.customer_phone,
            "customer_address": self.customer_address,
            "order_number": self.order_number,
            "items": self.items,
            "subtotal": self.subtotal,
            "tax_amount": self.tax_amount,
            "shipping_amount": self.shipping_amount,
            "discount_amount": self.discount_amount,
            "total_amount": self.total_amount,
            "status": self.status.value if self.status else None,
            "payment_status": self.payment_status.value if self.payment_status else None,
            "payment_method": self.payment_method,
            "payment_id": self.payment_id,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "shipping_method": self.shipping_method,
            "tracking_number": self.tracking_number,
            "tracking_url": self.tracking_url,
            "shipped_at": self.shipped_at.isoformat() if self.shipped_at else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "notes": self.notes,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
    
    def __repr__(self):
        return f"<Order {self.order_number}>"
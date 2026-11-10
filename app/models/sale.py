# app/models/sale.py
import uuid
import sqlalchemy as sa
from datetime import datetime
from decimal import Decimal
from sqlalchemy.orm import relationship, validates
from sqlalchemy import (
    Column, String, Integer, Boolean,
    DateTime, ForeignKey, Text, Date, Index, DECIMAL
)
from sqlalchemy.dialects.postgresql import UUID, JSON
from app.models.refund import Refund

from app.db.base import Base


class Sale(Base):
    __tablename__ = "sales"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
    

    reference = Column(String(50), unique=True, nullable=False, index=True)

    # CORRECTION: Un seul customer_id, utilisant customers.id
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True)
    customer_name = Column(String(100), nullable=False, default="Client Générique")
    customer_phone = Column(String(20), nullable=True)
    
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True)

    # CORRECTION: Changé seller_id à created_by pour correspondre au modèle User
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    seller_name = Column(String(100), nullable=False)

    # Champs pour l'annulation
    cancelled_at = Column(DateTime, nullable=True)
    cancelled_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    cancel_reason = Column(Text, nullable=True)
    
    # Ajout pour validation des ventes
    validated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    payment_method = Column(
        String(20), 
        nullable=False, 
        default="cash",
        comment="cash, mobile_money, card, check, bank_transfer, credit"
    )
    reference_payment = Column(String(100), nullable=True)
    payment_date = Column(DateTime, nullable=True)

    status = Column(
        String(20), 
        nullable=False, 
        default="completed",
        comment="draft, pending, completed, cancelled, refunded"
    )

    is_credit = Column(Boolean, default=False)
    credit_due_date = Column(Date, nullable=True)
    guarantee_deposit = Column(DECIMAL(15, 2), default=0.0)
    guarantor_name = Column(String(100), nullable=True)
    guarantor_phone = Column(String(20), nullable=True)

    subtotal = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    total_discount = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    total_tva = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    total_amount = Column(DECIMAL(15, 2), nullable=False, default=0.0)

    global_discount = Column(DECIMAL(10, 2), default=0.0)

    invoice_number = Column(String(50), nullable=True, unique=True)
    invoice_path = Column(String(500), nullable=True)
    receipt_path = Column(String(500), nullable=True)

    notes = Column(Text, nullable=True)
    sale_data = Column(JSON, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    validated_at = Column(DateTime, nullable=True)

    # =======================
    # Relations
    # =======================
    pharmacy = relationship("Pharmacy", back_populates="sales")
    tenant = relationship("Tenant")
    # CORRECTION: Une seule relation customer
    customer = relationship("Customer", back_populates="sales", foreign_keys=[customer_id])
    creator = relationship("User", foreign_keys=[created_by], backref="sales_created")
    validator = relationship("User", foreign_keys=[validated_by], backref="sales_validated")
    items = relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="sale", cascade="all, delete-orphan")
    refunds = relationship("Refund", back_populates="sale", cascade="all, delete-orphan")
    branch = relationship("Branch", back_populates="sales")
    
    # Relation avec DebtPayment
    debt_payments = relationship(
        "DebtPayment", 
        back_populates="sale", 
        foreign_keys="[DebtPayment.sale_id]",
        cascade="all, delete-orphan"
    )
    
    debts = relationship("Debt", back_populates="sale", foreign_keys="[Debt.sale_id]")
    financial_transaction = relationship(
        "FinancialTransaction",
        back_populates="sale",
        uselist=False,
    )
    # Les retours survivent à la suppression de la vente (sale_id -> NULL) :
    # une vente retournée est supprimée, seul le retour est conservé.
    returns = relationship("Return", back_populates="sale", foreign_keys="Return.sale_id")
    
    credit_allocations = relationship("SaleCreditAllocation", back_populates="sale")

    # CORRECTION: Index mis à jour pour utiliser customer_id 
    __table_args__ = (
        Index("ix_sales_tenant_created", "tenant_id", "created_at"),
        Index("ix_sales_tenant_customer", "tenant_id", "customer_id"),  
        Index("ix_sales_tenant_status", "tenant_id", "status"),
        Index("ix_sales_tenant_credit", "tenant_id", "is_credit"),
        Index("ix_sales_tenant_payment", "tenant_id", "payment_method"),
        Index("ix_sales_created_by", "tenant_id", "created_by"),
        Index("ix_sales_pharmacy", "pharmacy_id", "created_at"),
    )

    # =======================
    # Validations
    # =======================
    @validates('total_amount', 'subtotal', 'total_discount', 'total_tva')
    def validate_amounts(self, key, value):
        """Valide que les montants ne sont pas négatifs"""
        if value < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return value

    # =======================
    # Propriétés
    # =======================
    @property
    def amount_paid(self):
        """Montant total payé"""
        return float(sum(payment.amount for payment in self.payments if payment.status == "completed"))

    @property
    def amount_due(self):
        """Montant restant à payer"""
        return float(self.total_amount - self.amount_paid)

    @property
    def is_paid(self):
        """Vérifie si la vente est entièrement payée"""
        return self.amount_due <= 0.01  # Tolérance de 0.01 pour les arrondis

    @property
    def credit_status(self):
        """Statut du crédit"""
        if not self.is_credit:
            return "not_credit"
        if self.is_paid:
            return "paid"
        if self.credit_due_date and datetime.now().date() > self.credit_due_date:
            return "overdue"
        return "pending"

    @property
    def days_overdue(self):
        """Nombre de jours de retard (si crédit)"""
        if not self.is_credit or not self.credit_due_date or self.is_paid:
            return 0
        today = datetime.now().date()
        if today > self.credit_due_date:
            return (today - self.credit_due_date).days
        return 0

    # =======================
    # Méthodes
    # =======================
    def calculate_totals(self):
        """Recalcule les totaux à partir des items"""
        self.subtotal = sum(item.subtotal for item in self.items)
        self.total_discount = sum(item.discount_amount for item in self.items) + self.global_discount
        self.total_tva = sum(item.tva_amount for item in self.items)
        self.total_amount = self.subtotal - self.total_discount + self.total_tva
        return self

    def validate_sale(self, user_id):
        """Valide la vente"""
        self.validated_by = user_id
        self.validated_at = datetime.utcnow()
        self.status = "completed"
        return self

    def cancel_sale(self, reason=None):
        """Annule la vente"""
        self.status = "cancelled"
        if reason:
            self.notes = f"{self.notes or ''}\nAnnulé: {reason}"
        return self

    def __repr__(self):
        return f"<Sale {self.reference} | {self.total_amount} | {self.status}>"


class SaleItem(Base):
    __tablename__ = "sale_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)

    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    product_code = Column(String(50), nullable=False)
    product_name = Column(String(200), nullable=False)

    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(DECIMAL(15, 2), nullable=False)

    discount_percent = Column(DECIMAL(5, 2), default=0.0)
    tva_rate = Column(DECIMAL(5, 2), default=0.0)

    subtotal = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    discount_amount = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    tva_amount = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    total = Column(DECIMAL(15, 2), nullable=False, default=0.0)

    batch_number = Column(String(50), nullable=True)
    expiry_date = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # =======================
    # Relations
    # =======================
    sale = relationship("Sale", back_populates="items", foreign_keys=[sale_id])
    tenant = relationship("Tenant")
    product = relationship("Product", foreign_keys=[product_id])
    credit_allocations = relationship("SaleCreditAllocation", back_populates="sale_item")

    __table_args__ = (
        Index("ix_sale_items_product", "tenant_id", "product_id"),
        Index("ix_sale_items_sale", "tenant_id", "sale_id"),
        Index("ix_sale_items_batch", "tenant_id", "batch_number"),
        Index("ix_sale_items_pharmacy", "pharmacy_id", "product_id"),
    )

    # =======================
    # Validations
    # =======================
    @validates('quantity')
    def validate_quantity(self, key, value):
        """Valide que la quantité est positive"""
        if value <= 0:
            raise ValueError("La quantité doit être positive")
        return value

    @validates('unit_price')
    def validate_unit_price(self, key, value):
        """Valide que le prix unitaire n'est pas négatif"""
        if value < 0:
            raise ValueError("Le prix unitaire ne peut pas être négatif")
        return value

    # =======================
    # Méthodes
    # =======================
    def calculate_totals(self):
        """Calcule les totaux de l'item"""
        self.subtotal = self.unit_price * self.quantity
        self.discount_amount = self.subtotal * (self.discount_percent / 100)
        self.tva_amount = (self.subtotal - self.discount_amount) * (self.tva_rate / 100)
        self.total = self.subtotal - self.discount_amount + self.tva_amount
        return self

    def update_stock_quantity(self):
        """Met à jour la quantité en stock si le produit a une relation stock"""
        if self.product and hasattr(self.product, 'stock_items'):
            # Cherche le stock correspondant au batch
            for stock in self.product.stock_items:
                if stock.batch_number == self.batch_number:
                    stock.quantity -= self.quantity
                    break

    def __repr__(self):
        return f"<SaleItem {self.product_code} x{self.quantity} = {self.total}>"
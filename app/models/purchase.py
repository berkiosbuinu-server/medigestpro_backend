# app/models/purchase.py
import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy.orm import relationship, validates
from sqlalchemy import (
    Column, String, Integer, Boolean,
    DateTime, ForeignKey, Text, Date, Index, DECIMAL
)
from sqlalchemy.dialects.postgresql import UUID, JSON

from app.db.base import Base


class Purchase(Base):
    """
    Modèle pour les achats de produits
    """
    __tablename__ = "purchases"
    __table_args__ = {'extend_existing': True} 

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False, index=True)

    reference = Column(String(50), unique=True, nullable=False, index=True)
    invoice_number = Column(String(100), nullable=True, index=True)

    # Fournisseur
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    supplier_name = Column(String(200), nullable=False)
    supplier_invoice = Column(String(100), nullable=True)

    # Dates
    purchase_date = Column(Date, nullable=False, default=datetime.utcnow().date, index=True)
    delivery_date = Column(Date, nullable=True)
    payment_due_date = Column(Date, nullable=True)

    # Statut
    status = Column(
        String(20),
        nullable=False,
        default="draft",
        comment="draft, ordered, received, partial, completed, cancelled"
    )

    # Informations de paiement
    payment_method = Column(String(50), nullable=True, default="bank_transfer")
    payment_status = Column(
        String(20),
        nullable=False,
        default="pending",
        comment="pending, partial, paid, overdue"
    )

    # Totaux
    subtotal = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    discount_amount = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    shipping_cost = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    tax_amount = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    total_amount = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    amount_paid = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    amount_due = Column(DECIMAL(15, 2), nullable=False, default=0.0)

    # Métadonnées
    notes = Column(Text, nullable=True)
    payment_notes = Column(Text, nullable=True)
    delivery_notes = Column(Text, nullable=True)

    # Responsables
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    received_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Dates de suivi
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    ordered_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # =======================
    # Relations
    # =======================
    tenant = relationship("Tenant")
    supplier = relationship("Supplier", back_populates="purchases", overlaps="purchases")
    creator = relationship("User", foreign_keys=[created_by])
    receiver = relationship("User", foreign_keys=[received_by])
    pharmacy = relationship("Pharmacy", back_populates="purchases")
    
    items = relationship("PurchaseItem", back_populates="purchase", cascade="all, delete-orphan")
    payments = relationship("PurchasePayment", back_populates="purchase", cascade="all, delete-orphan")
    credit = relationship("PurchaseCredit", back_populates="purchase", uselist=False)
    is_credit_purchase = Column(Boolean, default=False, comment="Achat effectué à crédit")
    credit_config_id = Column(UUID(as_uuid=True), ForeignKey("supplier_credit_configs.id"), nullable=True)

    __table_args__ = (
        Index("ix_purchases_tenant_status", "tenant_id", "status"),
        Index("ix_purchases_tenant_supplier", "tenant_id", "supplier_id"),
        Index("ix_purchases_tenant_date", "tenant_id", "purchase_date"),
        Index("ix_purchases_tenant_payment", "tenant_id", "payment_status"),
    )

    # =======================
    # Validations
    # =======================
    @validates('subtotal', 'discount_amount', 'shipping_cost', 'tax_amount', 'total_amount')
    def validate_amounts(self, key, value):
        """Valide que les montants ne sont pas négatifs"""
        if value < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return value

    # =======================
    # Propriétés
    # =======================
    @property
    def is_paid(self) -> bool:
        """Vérifie si l'achat est entièrement payé"""
        return self.amount_due <= 0.01

    @property
    def days_overdue(self) -> int:
        """Nombre de jours de retard de paiement"""
        if not self.payment_due_date or self.is_paid:
            return 0
        
        today = datetime.utcnow().date()
        if today > self.payment_due_date:
            return (today - self.payment_due_date).days
        return 0

    @property
    def payment_status_detail(self) -> str:
        """Statut détaillé du paiement"""
        if self.is_paid:
            return "paid"
        elif self.amount_paid > 0:
            return "partial"
        elif self.days_overdue > 0:
            return "overdue"
        else:
            return "pending"

    @property
    def item_count(self) -> int:
        """Nombre total d'articles"""
        return sum(item.quantity for item in self.items)

    @property
    def received_item_count(self) -> int:
        """Nombre d'articles reçus"""
        return sum(item.quantity_received for item in self.items)

    @property
    def receipt_percentage(self) -> float:
        """Pourcentage de réception"""
        if self.item_count == 0:
            return 0.0
        return (self.received_item_count / self.item_count) * 100

    # =======================
    # Méthodes
    # =======================
    def calculate_totals(self):
        """Recalcule les totaux à partir des items"""
        self.subtotal = sum(item.subtotal for item in self.items)
        self.discount_amount = sum(item.discount_amount for item in self.items)
        self.tax_amount = sum(item.tax_amount for item in self.items)
        
        # Calculer le total
        self.total_amount = (
            self.subtotal 
            - self.discount_amount 
            + self.shipping_cost 
            + self.tax_amount
        )
        
        # Mettre à jour le montant dû
        self.amount_due = self.total_amount - self.amount_paid
        
        return self

    def mark_as_received(self, user_id: UUID, partial: bool = False):
        """Marque l'achat comme reçu"""
        self.received_by = user_id
        self.received_at = datetime.utcnow()
        
        if partial:
            self.status = "partial"
        else:
            self.status = "completed"
        
        # Mettre à jour les stocks pour chaque item
        for item in self.items:
            if item.quantity_received > 0:
                item.update_stock()
        
        return self

    def add_payment(self, amount: Decimal, method: str, notes: str = None):
        """Ajoute un paiement à l'achat"""
        self.amount_paid += amount
        self.amount_due = self.total_amount - self.amount_paid
        
        if self.is_paid:
            self.payment_status = "paid"
            self.paid_at = datetime.utcnow()
        elif self.amount_paid > 0:
            self.payment_status = "partial"
        
        return self

    def __repr__(self):
        return f"<Purchase {self.reference} | {self.total_amount} | {self.status}>"


class PurchaseItem(Base):
    """
    Articles d'un achat
    """
    __tablename__ = "purchase_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_id = Column(UUID(as_uuid=True), ForeignKey("purchases.id"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    product_code = Column(String(50), nullable=False)
    product_name = Column(String(200), nullable=False)

    # Quantités
    quantity_ordered = Column(DECIMAL(15, 3), nullable=False, default=0.0)
    quantity_received = Column(DECIMAL(15, 3), nullable=False, default=0.0)
    quantity_pending = Column(DECIMAL(15, 3), nullable=False, default=0.0)

    # Prix
    unit_price = Column(DECIMAL(15, 4), nullable=False, default=0.0)
    discount_percent = Column(DECIMAL(5, 2), nullable=False, default=0.0)

    # Calculés
    subtotal = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    discount_amount = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    tax_percent = Column(DECIMAL(5, 2), nullable=False, default=0.0)
    tax_amount = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    total = Column(DECIMAL(15, 2), nullable=False, default=0.0)

    # Batch et péremption
    batch_number = Column(String(100), nullable=True)
    expiry_date = Column(Date, nullable=True)

    # Statut
    status = Column(
        String(20),
        nullable=False,
        default="pending",
        comment="pending, ordered, partial, received, cancelled"
    )

    # Localisation
    location = Column(String(100), nullable=True)

    # Notes
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # =======================
    # Relations
    # =======================
    purchase = relationship("Purchase", back_populates="items")
    tenant = relationship("Tenant")
    product = relationship("Product")

    __table_args__ = (
        Index("ix_purchase_items_product", "tenant_id", "product_id"),
        Index("ix_purchase_items_purchase", "tenant_id", "purchase_id"),
        Index("ix_purchase_items_batch", "tenant_id", "batch_number"),
    )

    # =======================
    # Validations
    # =======================
    @validates('quantity_ordered', 'quantity_received')
    def validate_quantity(self, key, value):
        """Valide que la quantité est positive"""
        if value < 0:
            raise ValueError(f"{key} doit être positive")
        return value

    @validates('unit_price')
    def validate_unit_price(self, key, value):
        """Valide que le prix unitaire n'est pas négatif"""
        if value < 0:
            raise ValueError("Le prix unitaire ne peut pas être négatif")
        return value

    # =======================
    # Propriétés
    # =======================
    @property
    def is_complete(self) -> bool:
        """Vérifie si l'article est entièrement reçu"""
        return self.quantity_received >= self.quantity_ordered

    @property
    def receipt_percentage(self) -> float:
        """Pourcentage de réception"""
        if self.quantity_ordered == 0:
            return 0.0
        return (float(self.quantity_received) / float(self.quantity_ordered)) * 100

    # =======================
    # Méthodes
    # =======================
    def calculate_totals(self):
        """Calcule les totaux de l'article"""
        self.subtotal = self.unit_price * self.quantity_ordered
        self.discount_amount = self.subtotal * (self.discount_percent / 100)
        self.tax_amount = (self.subtotal - self.discount_amount) * (self.tax_percent / 100)
        self.total = self.subtotal - self.discount_amount + self.tax_amount
        return self

    def update_stock(self):
        """Met à jour le stock du produit"""
        if self.product and self.quantity_received > 0:
            # Augmenter le stock du produit
            self.product.quantity += self.quantity_received
            
            # Créer un mouvement de stock
            from app.models.stock_movement import StockMovement
            movement = StockMovement(
                tenant_id=self.tenant_id,
                product_id=self.product_id,
                quantity_before=self.product.quantity - self.quantity_received,
                quantity_after=self.product.quantity,
                quantity_change=self.quantity_received,
                unit_price=self.unit_price,
                movement_type="purchase",
                reference=self.purchase.reference if self.purchase else None,
                batch_number=self.batch_number,
                reason=f"Achat {self.purchase.reference}" if self.purchase else "Réception d'achat",
                created_by=self.purchase.created_by if self.purchase else None
            )
            
            # Note: L'ajout au stock existant dépend de votre logique métier
            return movement
        
        return None

    def receive_quantity(self, quantity: Decimal, user_id: UUID = None):
        """Reçoit une quantité de l'article"""
        if quantity > self.quantity_pending:
            raise ValueError("Quantité à recevoir supérieure à la quantité en attente")
        
        self.quantity_received += quantity
        self.quantity_pending = self.quantity_ordered - self.quantity_received
        
        if self.is_complete:
            self.status = "received"
        elif self.quantity_received > 0:
            self.status = "partial"
        
        # Mettre à jour le stock
        self.update_stock()
        
        return self

    def __repr__(self):
        return f"<PurchaseItem {self.product_code} x{self.quantity_ordered} = {self.total}>"


class PurchasePayment(Base):
    """
    Paiements pour un achat
    """
    __tablename__ = "purchase_payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_id = Column(UUID(as_uuid=True), ForeignKey("purchases.id"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    # Montant
    amount = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    payment_method = Column(String(50), nullable=False, default="bank_transfer")

    # Références
    reference = Column(String(100), nullable=True)
    transaction_id = Column(String(100), nullable=True)
    bank_account = Column(String(100), nullable=True)

    # Dates
    payment_date = Column(Date, nullable=False, default=datetime.utcnow().date, index=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)

    # Statut
    status = Column(
        String(20),
        nullable=False,
        default="pending",
        comment="pending, processing, completed, failed, cancelled"
    )

    # Notes
    notes = Column(Text, nullable=True)

    # Responsable
    recorded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # =======================
    # Relations
    # =======================
    purchase = relationship("Purchase", back_populates="payments")
    tenant = relationship("Tenant")
    recorder = relationship("User", foreign_keys=[recorded_by])

    __table_args__ = (
        Index("ix_purchase_payments_tenant_date", "tenant_id", "payment_date"),
        Index("ix_purchase_payments_tenant_status", "tenant_id", "status"),
    )

    # =======================
    # Méthodes
    # =======================
    def mark_as_completed(self):
        """Marque le paiement comme complété"""
        self.status = "completed"
        return self

    def __repr__(self):
        return f"<PurchasePayment {self.amount} for {self.purchase_id}>"
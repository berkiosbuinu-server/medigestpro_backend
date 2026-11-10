# app/models/stock_adjustment.py
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, DateTime, ForeignKey, Text, Index, DECIMAL, Boolean
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class StockAdjustment(Base):
    """
    Modèle pour les ajustements de stock
    """
    __tablename__ = "stock_adjustments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Organisation
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)

    # Numéro d'ajustement
    adjustment_number = Column(String(50), nullable=False, unique=True, index=True)

    # Type d'ajustement
    adjustment_type = Column(
        String(50),
        nullable=False,
        index=True,
        comment="manual, auto, inventory, expiry, damage, loss, return"
    )

    # Raison de l'ajustement
    reason = Column(String(200), nullable=False)
    notes = Column(Text, nullable=True)

    # Quantités totales
    total_quantity_change = Column(DECIMAL(15, 3), nullable=False, default=0)
    total_value_change = Column(DECIMAL(15, 2), nullable=False, default=0)

    # Statut
    status = Column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
        comment="pending, approved, rejected, cancelled"
    )

    # Approbation
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approval_notes = Column(Text, nullable=True)

    # Création
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Liens avec les inventaires physiques
    inventory_count_id = Column(UUID(as_uuid=True), ForeignKey("inventory_counts.id"), nullable=True)
    inventory_count_item_id = Column(UUID(as_uuid=True), ForeignKey("inventory_count_items.id"), nullable=True)

    # =======================
    # Relations
    # =======================
    tenant = relationship("Tenant")
    pharmacy = relationship("Pharmacy", foreign_keys=[pharmacy_id])
    branch = relationship("Branch", foreign_keys=[branch_id])
    creator = relationship("User", foreign_keys=[created_by])
    approver = relationship("User", foreign_keys=[approved_by])
    
    # Relation avec les items d'ajustement
    items = relationship("StockAdjustmentItem", back_populates="adjustment", cascade="all, delete-orphan")
    
    # Relation avec les inventaires
    inventory_count = relationship("InventoryCount", foreign_keys=[inventory_count_id])
    inventory_count_item = relationship("InventoryCountItem", foreign_keys=[inventory_count_item_id])

    __table_args__ = (
        Index("ix_stock_adjustments_tenant_status", "tenant_id", "status"),
        Index("ix_stock_adjustments_pharmacy_date", "pharmacy_id", "created_at"),
        Index("ix_stock_adjustments_type", "adjustment_type", "status"),
    )

    def generate_adjustment_number(self):
        """Génère un numéro d'ajustement unique"""
        from datetime import datetime
        date_str = datetime.utcnow().strftime('%Y%m%d')
        return f"ADJ-{date_str}-{str(self.id)[:8]}"

    def approve(self, user_id: UUID, notes: str = None):
        """Approuve l'ajustement"""
        self.status = "approved"
        self.approved_by = user_id
        self.approved_at = datetime.utcnow()
        if notes:
            self.approval_notes = notes

    def reject(self, user_id: UUID, reason: str):
        """Rejette l'ajustement"""
        self.status = "rejected"
        self.approved_by = user_id
        self.approved_at = datetime.utcnow()
        self.approval_notes = reason

    def cancel(self, user_id: UUID, reason: str = None):
        """Annule l'ajustement"""
        self.status = "cancelled"
        self.approved_by = user_id
        self.approved_at = datetime.utcnow()
        if reason:
            self.approval_notes = reason

    def calculate_totals(self):
        """Calcule les totaux à partir des items"""
        self.total_quantity_change = sum(item.quantity_change for item in self.items)
        self.total_value_change = sum(item.value_change for item in self.items)
        return self

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "pharmacy_id": str(self.pharmacy_id),
            "pharmacy_name": getattr(self.pharmacy, "name", None),
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "adjustment_number": self.adjustment_number,
            "adjustment_type": self.adjustment_type,
            "reason": self.reason,
            "notes": self.notes,
            "total_quantity_change": float(self.total_quantity_change or 0),
            "total_value_change": float(self.total_value_change or 0),
            "status": self.status,
            "created_by": str(self.created_by),
            "created_by_name": getattr(self.creator, "nom_complet", getattr(self.creator, "email", None)),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "approved_by": str(self.approved_by) if self.approved_by else None,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "approval_notes": self.approval_notes,
            "items": [item.to_dict() for item in self.items] if self.items else [],
        }

    def __repr__(self):
        return f"<StockAdjustment {self.adjustment_number} - {self.status}>"


class StockAdjustmentItem(Base):
    """
    Items d'un ajustement de stock
    """
    __tablename__ = "stock_adjustment_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    adjustment_id = Column(UUID(as_uuid=True), ForeignKey("stock_adjustments.id"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False)

    # Produit
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    product_stock_id = Column(UUID(as_uuid=True), ForeignKey("product_stocks.id"), nullable=True)

    # Quantités
    old_quantity = Column(DECIMAL(15, 3), nullable=False, default=0)
    new_quantity = Column(DECIMAL(15, 3), nullable=False, default=0)
    quantity_change = Column(DECIMAL(15, 3), nullable=False, default=0)

    # Prix et valeur
    unit_price = Column(DECIMAL(15, 2), nullable=True)
    old_value = Column(DECIMAL(15, 2), nullable=False, default=0)
    new_value = Column(DECIMAL(15, 2), nullable=False, default=0)
    value_change = Column(DECIMAL(15, 2), nullable=False, default=0)

    # Informations supplémentaires
    batch_number = Column(String(100), nullable=True)
    expiry_date = Column(DateTime, nullable=True)
    location = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)

    # Liens avec les mouvements de stock
    stock_movement_id = Column(UUID(as_uuid=True), ForeignKey("stock_movements.id"), nullable=True)

    # =======================
    # Relations
    # =======================
    adjustment = relationship("StockAdjustment", back_populates="items")
    tenant = relationship("Tenant")
    pharmacy = relationship("Pharmacy")
    product = relationship("Product")
    product_stock = relationship("ProductStock")
    stock_movement = relationship("StockMovement", foreign_keys=[stock_movement_id])

    __table_args__ = (
        Index("ix_stock_adjustment_items_adjustment", "adjustment_id"),
        Index("ix_stock_adjustment_items_product", "product_id", "adjustment_id"),
    )

    def calculate_values(self):
        """Calcule les valeurs de l'item"""
        if self.unit_price:
            self.old_value = self.old_quantity * self.unit_price
            self.new_value = self.new_quantity * self.unit_price
            self.value_change = self.new_value - self.old_value
        return self

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "product_id": str(self.product_id),
            "product_name": getattr(self.product, "name", None),
            "product_code": getattr(self.product, "code", None),
            "old_quantity": float(self.old_quantity or 0),
            "new_quantity": float(self.new_quantity or 0),
            "quantity_change": float(self.quantity_change or 0),
            "unit_price": float(self.unit_price) if self.unit_price else None,
            "old_value": float(self.old_value or 0),
            "new_value": float(self.new_value or 0),
            "value_change": float(self.value_change or 0),
            "batch_number": self.batch_number,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "location": self.location,
            "notes": self.notes,
            "stock_movement_id": str(self.stock_movement_id) if self.stock_movement_id else None,
        }

    def __repr__(self):
        return f"<StockAdjustmentItem {self.product_id} change: {self.quantity_change}>"
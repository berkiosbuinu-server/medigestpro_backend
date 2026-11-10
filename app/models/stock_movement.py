# app/models/stock_movement.py
import uuid
from datetime import datetime, date
from sqlalchemy import (
    Column, String, Integer, DateTime, ForeignKey, Text, Date, Index, DECIMAL, Boolean
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.models.stock_adjustment import StockAdjustment, StockAdjustmentItem

from app.db.base import Base


class StockMovement(Base):
    """
    Modèle pour suivre les mouvements de stock
    Supporte la multi-pharmacie et multi-branche
    """
    __tablename__ = "stock_movements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Organisation
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)

    # Produit
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    product_stock_id = Column(UUID(as_uuid=True), ForeignKey("product_stocks.id"), nullable=True)

    # Quantités
    quantity_before = Column(DECIMAL(15, 3), nullable=False, default=0)
    quantity_after = Column(DECIMAL(15, 3), nullable=False, default=0)
    quantity_change = Column(DECIMAL(15, 3), nullable=False, default=0)

    # Prix
    purchase_price = Column(DECIMAL(15, 2), nullable=True, comment="Prix d'achat")
    selling_price = Column(DECIMAL(15, 2), nullable=True, comment="Prix de vente")
    unit_price = Column(DECIMAL(15, 2), nullable=True)
    total_price = Column(DECIMAL(15, 2), nullable=True)

    # Type de mouvement
    movement_type = Column(
        String(50),
        nullable=False,
        index=True,
        comment="initial, purchase, sale, adjustment, return, transfer_in, transfer_out, expiry, correction"
    )
    
    # Direction du mouvement (in/out)
    direction = Column(
        String(10),
        nullable=True,
        comment="in, out, adjustment"
    )

    # Références
    reference = Column(String(100), nullable=True, index=True)
    document_number = Column(String(100), nullable=True)
    batch_number = Column(String(100), nullable=True)
    
    # Emplacements
    location_from = Column(String(100), nullable=True)
    location_to = Column(String(100), nullable=True)
    
    # Transfert entre pharmacies
    from_pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=True)
    to_pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=True)
    transfer_status = Column(
        String(20),
        nullable=True,
        default="pending",
        comment="pending, in_transit, completed, cancelled"
    )

    # Raison et notes
    reason = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)

    # Liens avec les autres modules
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=True)
    sale_item_id = Column(UUID(as_uuid=True), ForeignKey("sale_items.id"), nullable=True)
    purchase_id = Column(UUID(as_uuid=True), ForeignKey("purchases.id"), nullable=True)
    purchase_item_id = Column(UUID(as_uuid=True), ForeignKey("purchase_items.id"), nullable=True)
    adjustment_id = Column(UUID(as_uuid=True), ForeignKey("stock_adjustments.id"), nullable=True)
    adjustment_item_id = Column(UUID(as_uuid=True), ForeignKey("stock_adjustment_items.id"), nullable=True)

    # Validation
    validated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    validated_at = Column(DateTime, nullable=True)
    is_validated = Column(Boolean, default=False)

    # Utilisateur responsable
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    # Dates
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    movement_date = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    expiration_date = Column(Date, nullable=True)

    # =======================
    # Relations
    # =======================
    tenant = relationship("Tenant")
    pharmacy = relationship("Pharmacy", foreign_keys=[pharmacy_id])
    branch = relationship("Branch", foreign_keys=[branch_id])
    product = relationship("Product", back_populates="stock_movements")
    product_stock = relationship("ProductStock", back_populates="stock_movements")
    user = relationship("User", foreign_keys=[created_by])
    validator = relationship("User", foreign_keys=[validated_by])
    adjustment = relationship("StockAdjustment", foreign_keys=[adjustment_id])
    adjustment_item = relationship("StockAdjustmentItem", foreign_keys=[adjustment_item_id])
    
    # Relations pour les transferts
    from_pharmacy = relationship("Pharmacy", foreign_keys=[from_pharmacy_id])
    to_pharmacy = relationship("Pharmacy", foreign_keys=[to_pharmacy_id])
    
    # Relations avec ventes et achats
    sale = relationship("Sale", foreign_keys=[sale_id])
    sale_item = relationship("SaleItem", foreign_keys=[sale_item_id])
    purchase = relationship("Purchase", foreign_keys=[purchase_id])
    purchase_item = relationship("PurchaseItem", foreign_keys=[purchase_item_id])

    __table_args__ = (
        Index("ix_stock_movements_tenant_date", "tenant_id", "created_at"),
        Index("ix_stock_movements_pharmacy_date", "pharmacy_id", "created_at"),
        Index("ix_stock_movements_branch_date", "branch_id", "created_at"),
        Index("ix_stock_movements_product_date", "product_id", "created_at"),
        Index("ix_stock_movements_type_date", "movement_type", "created_at"),
        Index("ix_stock_movements_reference", "reference"),
        Index("ix_stock_movements_transfer", "from_pharmacy_id", "to_pharmacy_id"),
        Index("ix_stock_movements_sale", "sale_id"),
        Index("ix_stock_movements_purchase", "purchase_id"),
    )

    @property
    def quantity_change_absolute(self) -> float:
        """Valeur absolue du changement de quantité"""
        return abs(float(self.quantity_change or 0))

    @property
    def is_incoming(self) -> bool:
        """Vérifie si c'est un mouvement entrant"""
        return self.quantity_change > 0

    @property
    def is_outgoing(self) -> bool:
        """Vérifie si c'est un mouvement sortant"""
        return self.quantity_change < 0

    @property
    def total_value(self) -> float:
        """Valeur totale du mouvement"""
        if self.unit_price and self.quantity_change:
            return float(self.unit_price * abs(self.quantity_change))
        return 0.0

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "pharmacy_id": str(self.pharmacy_id),
            "pharmacy_name": getattr(self.pharmacy, "name", None),
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "branch_name": getattr(self.branch, "name", None),
            "product_id": str(self.product_id),
            "product_name": getattr(self.product, "name", None),
            "product_code": getattr(self.product, "code", None),
            "product_stock_id": str(self.product_stock_id) if self.product_stock_id else None,
            "quantity_before": float(self.quantity_before or 0),
            "quantity_after": float(self.quantity_after or 0),
            "quantity_change": float(self.quantity_change or 0),
            "quantity_change_absolute": self.quantity_change_absolute,
            "purchase_price": float(self.purchase_price) if self.purchase_price is not None else None,
            "selling_price": float(self.selling_price) if self.selling_price is not None else None,
            "unit_price": float(self.unit_price) if self.unit_price is not None else None,
            "total_price": float(self.total_price) if self.total_price is not None else None,
            "total_value": self.total_value,
            "movement_type": self.movement_type,
            "direction": self.direction,
            "reference": self.reference,
            "document_number": self.document_number,
            "batch_number": self.batch_number,
            "location_from": self.location_from,
            "location_to": self.location_to,
            "from_pharmacy_id": str(self.from_pharmacy_id) if self.from_pharmacy_id else None,
            "to_pharmacy_id": str(self.to_pharmacy_id) if self.to_pharmacy_id else None,
            "transfer_status": self.transfer_status,
            "reason": self.reason,
            "notes": self.notes,
            "sale_id": str(self.sale_id) if self.sale_id else None,
            "sale_reference": getattr(self.sale, "reference", None),
            "purchase_id": str(self.purchase_id) if self.purchase_id else None,
            "created_by": str(self.created_by),
            "created_by_name": getattr(self.user, "nom_complet", getattr(self.user, "email", None)),
            "validated_by": str(self.validated_by) if self.validated_by else None,
            "is_validated": self.is_validated,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "movement_date": self.movement_date.isoformat() if self.movement_date else None,
            "expiration_date": self.expiration_date.isoformat() if self.expiration_date else None,
        }

    def __repr__(self):
        direction = "+" if self.quantity_change > 0 else str(self.quantity_change)
        return f"<StockMovement {self.movement_type} {direction} for {self.product_id} at {self.pharmacy_id}>"


class InventoryCount(Base):
    """
    Modèle pour les inventaires physiques
    """
    __tablename__ = "inventory_counts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)

    count_number = Column(String(50), nullable=False, unique=True, index=True)

    # IMPORTANT : ne mets PAS datetime.utcnow().date (ça s’évalue au chargement du module)
    count_date = Column(Date, nullable=False, default=date.today, index=True)

    location = Column(String(100), nullable=True)

    total_products = Column(Integer, nullable=False, default=0)
    counted_products = Column(Integer, nullable=False, default=0)
    discrepancies = Column(Integer, nullable=False, default=0)

    theoretical_value = Column(DECIMAL(15, 2), nullable=False, default=0)
    actual_value = Column(DECIMAL(15, 2), nullable=False, default=0)
    difference_value = Column(DECIMAL(15, 2), nullable=False, default=0)

    status = Column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
        comment="pending, in_progress, completed, validated, cancelled"
    )

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    validated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    validated_at = Column(DateTime, nullable=True)

    notes = Column(Text, nullable=True)

    # Relations
    tenant = relationship("Tenant")
    pharmacy = relationship("Pharmacy", foreign_keys=[pharmacy_id])
    branch = relationship("Branch", foreign_keys=[branch_id])
    creator = relationship("User", foreign_keys=[created_by])
    validator = relationship("User", foreign_keys=[validated_by])

    __table_args__ = (
        Index("ix_inventory_counts_tenant_status", "tenant_id", "status"),
        Index("ix_inventory_counts_tenant_date", "tenant_id", "count_date"),
        Index("ix_inventory_counts_pharmacy", "pharmacy_id", "status"),
    )

    @property
    def progress_percentage(self) -> float:
        if not self.total_products:
            return 0.0
        return (self.counted_products / self.total_products) * 100.0

    @property
    def difference_percentage(self) -> float:
        if not self.theoretical_value:
            return 0.0
        return (float(self.difference_value or 0) / float(self.theoretical_value)) * 100.0

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "pharmacy_id": str(self.pharmacy_id),
            "pharmacy_name": getattr(self.pharmacy, "name", None),
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "count_number": self.count_number,
            "count_date": self.count_date.isoformat() if self.count_date else None,
            "location": self.location,
            "total_products": self.total_products,
            "counted_products": self.counted_products,
            "discrepancies": self.discrepancies,
            "theoretical_value": float(self.theoretical_value or 0),
            "actual_value": float(self.actual_value or 0),
            "difference_value": float(self.difference_value or 0),
            "difference_percentage": self.difference_percentage,
            "progress_percentage": self.progress_percentage,
            "status": self.status,
            "created_by": str(self.created_by),
            "validated_by": str(self.validated_by) if self.validated_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "validated_at": self.validated_at.isoformat() if self.validated_at else None,
            "notes": self.notes,
        }

    def __repr__(self):
        return f"<InventoryCount {self.count_number} - {self.status}>"


class InventoryCountItem(Base):
    """
    Articles d'un inventaire physique
    """
    __tablename__ = "inventory_count_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inventory_count_id = Column(UUID(as_uuid=True), ForeignKey("inventory_counts.id"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)

    theoretical_quantity = Column(DECIMAL(15, 3), nullable=False, default=0)
    actual_quantity = Column(DECIMAL(15, 3), nullable=False, default=0)
    quantity_difference = Column(DECIMAL(15, 3), nullable=False, default=0)

    unit_price = Column(DECIMAL(15, 2), nullable=False, default=0)
    theoretical_value = Column(DECIMAL(15, 2), nullable=False, default=0)
    actual_value = Column(DECIMAL(15, 2), nullable=False, default=0)
    value_difference = Column(DECIMAL(15, 2), nullable=False, default=0)

    batch_number = Column(String(100), nullable=True)
    location = Column(String(100), nullable=True)

    status = Column(String(20), nullable=False, default="pending", comment="pending, counted, validated")
    comments = Column(Text, nullable=True)

    counted_at = Column(DateTime, nullable=True)
    validated_at = Column(DateTime, nullable=True)

    inventory_count = relationship("InventoryCount", backref="items")
    product = relationship("Product")

    __table_args__ = (
        Index("ix_inventory_items_product", "product_id", "inventory_count_id"),
    )

    @property
    def has_discrepancy(self) -> bool:
        return float(self.quantity_difference or 0) != 0.0

    @property
    def discrepancy_percentage(self) -> float:
        tq = float(self.theoretical_quantity or 0)
        dq = float(self.quantity_difference or 0)
        aq = float(self.actual_quantity or 0)
        if tq == 0:
            return 100.0 if aq > 0 else 0.0
        return (abs(dq) / tq) * 100.0

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "product_id": str(self.product_id),
            "product_code": getattr(self.product, "code", None),
            "product_name": getattr(self.product, "name", None),
            "theoretical_quantity": float(self.theoretical_quantity or 0),
            "actual_quantity": float(self.actual_quantity or 0),
            "quantity_difference": float(self.quantity_difference or 0),
            "unit_price": float(self.unit_price or 0),
            "theoretical_value": float(self.theoretical_value or 0),
            "actual_value": float(self.actual_value or 0),
            "value_difference": float(self.value_difference or 0),
            "batch_number": self.batch_number,
            "location": self.location,
            "status": self.status,
            "has_discrepancy": self.has_discrepancy,
            "discrepancy_percentage": self.discrepancy_percentage,
            "comments": self.comments,
            "counted_at": self.counted_at.isoformat() if self.counted_at else None,
            "validated_at": self.validated_at.isoformat() if self.validated_at else None,
        }

    def __repr__(self):
        return f"<InventoryCountItem {self.product_id} diff: {self.quantity_difference}>"
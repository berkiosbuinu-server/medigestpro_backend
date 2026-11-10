# app/models/return_product.py
"""
Modèle pour la gestion des retours produits
Support complet pour la synchronisation offline/online
"""

from __future__ import annotations

import uuid
import sqlalchemy as sa
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, Optional, List
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, validates
from enum import Enum

from app.db.base import Base
import logging

logger = logging.getLogger(__name__)


class ReturnStatus(str, Enum):
    """Statut d'un retour produit"""
    PENDING = "pending"          # En attente de traitement
    APPROVED = "approved"        # Approuvé
    REJECTED = "rejected"        # Rejeté
    PROCESSED = "processed"      # Traité (stock réintégré)
    CANCELLED = "cancelled"      # Annulé


class ReturnReason(str, Enum):
    """Raison du retour"""
    EXPIRED = "expired"                  # Produit expiré
    DAMAGED = "damaged"                  # Produit endommagé
    DEFECTIVE = "defective"              # Produit défectueux
    WRONG_PRODUCT = "wrong_product"      # Mauvais produit livré
    WRONG_QUANTITY = "wrong_quantity"    # Mauvaise quantité
    CUSTOMER_RETURN = "customer_return"  # Retour client
    QUALITY_ISSUE = "quality_issue"      # Problème de qualité
    RECALL = "recall"                    # Rappel produit
    OTHER = "other"                      # Autre


class ReturnType(str, Enum):
    """Type de retour"""
    CUSTOMER = "customer"        # Retour client (vente)
    SUPPLIER = "supplier"        # Retour fournisseur (achat)
    INTERNAL = "internal"        # Retour interne (entre branches)
    DAMAGE = "damage"            # Produit endommagé
    EXPIRY = "expiry"            # Produit expiré


class Return(Base):
    """
    Modèle pour la gestion des retours produits.
    Supporte les retours clients, fournisseurs et internes.
    """
    __tablename__ = "returns"

    # =====================================
    # IDENTIFIANT UNIQUE
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)

    # =====================================
    # NUMÉRO DE RÉFÉRENCE
    # =====================================
    return_number = Column(String(50), nullable=False, unique=True, index=True)
    reference = Column(String(100), nullable=True, comment="Référence externe")

    # =====================================
    # TYPE ET STATUT
    # =====================================
    return_type = Column(
        SQLEnum(ReturnType, name="returntype_enum", create_type=True),
        nullable=False,
        default=ReturnType.CUSTOMER
    )
    status = Column(
        SQLEnum(ReturnStatus, name="returnstatus_enum", create_type=True),
        nullable=False,
        default=ReturnStatus.PENDING
    )
    reason = Column(
        SQLEnum(ReturnReason, name="returnreason_enum", create_type=True),
        nullable=False,
        default=ReturnReason.OTHER
    )

    # =====================================
    # LIENS AVEC LES VENTES/ACHATS
    # =====================================
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=True, index=True)
    purchase_id = Column(UUID(as_uuid=True), ForeignKey("purchases.id"), nullable=True, index=True)
    invoice_number = Column(String(100), nullable=True)
    
    # =====================================
    # INFORMATIONS CLIENT/FOURNISSEUR
    # =====================================
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True, index=True)
    customer_name = Column(String(200), nullable=True)
    customer_phone = Column(String(50), nullable=True)
    customer_email = Column(String(200), nullable=True)
    
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True, index=True)
    supplier_name = Column(String(200), nullable=True)

    # =====================================
    # INFORMATIONS RETOUR
    # =====================================
    return_date = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    requested_date = Column(DateTime, nullable=True)
    approved_date = Column(DateTime, nullable=True)
    processed_date = Column(DateTime, nullable=True)
    
    # =====================================
    # MONTANTS
    # =====================================
    subtotal = Column(Numeric(12, 2), nullable=False, default=0.00)
    tax_amount = Column(Numeric(12, 2), nullable=False, default=0.00)
    total_amount = Column(Numeric(12, 2), nullable=False, default=0.00)
    
    # Frais de restockage
    restocking_fee = Column(Numeric(12, 2), nullable=False, default=0.00)
    restocking_fee_percent = Column(Numeric(5, 2), nullable=True)
    
    # Montant remboursé
    refund_amount = Column(Numeric(12, 2), nullable=False, default=0.00)
    refund_method = Column(String(50), nullable=True, comment="cash, bank_transfer, mobile_money, credit_note")
    refund_date = Column(DateTime, nullable=True)
    
    # Note de crédit
    credit_note_number = Column(String(50), nullable=True)
    credit_note_issued = Column(Boolean, nullable=False, default=False)

    # =====================================
    # STOCK
    # =====================================
    stock_restored = Column(Boolean, nullable=False, default=False)
    stock_restored_date = Column(DateTime, nullable=True)
    stock_movement_id = Column(UUID(as_uuid=True), nullable=True)

    # =====================================
    # APPROBATION
    # =====================================
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_by_name = Column(String(200), nullable=True)
    approval_notes = Column(Text, nullable=True)
    
    rejection_reason = Column(Text, nullable=True)
    rejected_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    rejected_date = Column(DateTime, nullable=True)

    # =====================================
    # MÉTADONNÉES
    # =====================================
    notes = Column(Text, nullable=True)
    internal_notes = Column(Text, nullable=True)
    meta_data = Column(JSONB, nullable=False, default=dict)
    
    # =====================================
    # SYNC
    # =====================================
    is_synced = Column(Boolean, nullable=False, default=False)
    synced_at = Column(DateTime, nullable=True)
    sync_version = Column(Integer, nullable=False, default=1)
    
    # =====================================
    # FLAGS
    # =====================================
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", back_populates="returns")
    pharmacy = relationship("Pharmacy", back_populates="returns")
    branch = relationship("Branch", back_populates="returns")
    
    sale = relationship("Sale", back_populates="returns")
    
    items = relationship(
        "ReturnItem",
        back_populates="return_obj",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    
    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index("ix_returns_return_number", "return_number"),
        Index("ix_returns_tenant_branch", "tenant_id", "branch_id"),
        Index("ix_returns_tenant_status", "tenant_id", "status"),
        Index("ix_returns_tenant_type", "tenant_id", "return_type"),
        Index("ix_returns_sale_id", "sale_id"),
        Index("ix_returns_customer_id", "customer_id"),
        Index("ix_returns_supplier_id", "supplier_id"),
        Index("ix_returns_return_date", "return_date"),
        Index("ix_returns_status_date", "status", "return_date"),
        Index("ix_returns_sync_status", "is_synced", "sync_version"),
    )

    # =====================================
    # VALIDATIONS
    # =====================================
    @validates("subtotal", "tax_amount", "total_amount", "restocking_fee", "refund_amount")
    def validate_amounts(self, key, value):
        if value is None:
            return Decimal("0")
        dec = Decimal(str(value))
        if dec < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return dec

    @validates("restocking_fee_percent")
    def validate_fee_percent(self, key, value):
        if value is None:
            return None
        dec = Decimal(str(value))
        if dec < 0 or dec > 100:
            raise ValueError("restocking_fee_percent doit être entre 0 et 100")
        return dec

    @validates("return_date")
    def validate_return_date(self, key, value):
        if value is None:
            return datetime.utcnow()
        return value

    # =====================================
    # PROPRIÉTÉS CALCULÉES
    # =====================================
    @hybrid_property
    def total_items(self) -> int:
        return sum(item.quantity for item in self.items) if self.items else 0

    @hybrid_property
    def total_refundable(self) -> float:
        """Montant total remboursable avant frais"""
        return float(self.total_amount or 0)

    @hybrid_property
    def net_refund(self) -> float:
        """Montant remboursé net après frais"""
        return float((self.total_amount or 0) - (self.restocking_fee or 0))

    @hybrid_property
    def is_fully_processed(self) -> bool:
        return self.status in [ReturnStatus.PROCESSED, ReturnStatus.CANCELLED]

    @hybrid_property
    def days_pending(self) -> Optional[int]:
        if self.status != ReturnStatus.PENDING:
            return None
        return (datetime.utcnow() - self.return_date).days

    @hybrid_property
    def processing_time_days(self) -> Optional[int]:
        if not self.processed_date or not self.return_date:
            return None
        return (self.processed_date - self.return_date).days

    # =====================================
    # MÉTHODES
    # =====================================
    def ensure_meta_data(self) -> None:
        if self.meta_data is None or not isinstance(self.meta_data, dict):
            self.meta_data = {}

    def calculate_totals(self) -> None:
        """Recalcule les totaux à partir des items"""
        if not self.items:
            self.subtotal = Decimal("0")
            self.tax_amount = Decimal("0")
            self.total_amount = Decimal("0")
            return
        
        subtotal = Decimal("0")
        tax_amount = Decimal("0")
        
        for item in self.items:
            subtotal += item.subtotal or Decimal("0")
            tax_amount += item.tax_amount or Decimal("0")
        
        self.subtotal = subtotal
        self.tax_amount = tax_amount
        self.total_amount = subtotal + tax_amount

    def calculate_restocking_fee(self) -> None:
        """Calcule les frais de restockage si un pourcentage est défini"""
        if self.restocking_fee_percent and self.restocking_fee_percent > 0:
            self.restocking_fee = (self.total_amount * self.restocking_fee_percent) / Decimal("100")

    def calculate_refund_amount(self) -> None:
        """Calcule le montant à rembourser"""
        self.refund_amount = self.total_amount - self.restocking_fee

    def approve(self, approved_by_id: UUID, notes: Optional[str] = None) -> None:
        """Approuve le retour"""
        if self.status != ReturnStatus.PENDING:
            raise ValueError(f"Impossible d'approuver un retour avec le statut {self.status}")
        
        self.status = ReturnStatus.APPROVED
        self.approved_by = approved_by_id
        self.approval_notes = notes
        self.approved_date = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def reject(self, rejected_by_id: UUID, reason: str) -> None:
        """Rejette le retour"""
        if self.status != ReturnStatus.PENDING:
            raise ValueError(f"Impossible de rejeter un retour avec le statut {self.status}")
        
        self.status = ReturnStatus.REJECTED
        self.rejected_by = rejected_by_id
        self.rejection_reason = reason
        self.rejected_date = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def process(self, processed_by_id: UUID, restore_stock: bool = True) -> None:
        """Traite le retour et restaure le stock si demandé"""
        if self.status not in [ReturnStatus.APPROVED, ReturnStatus.PENDING]:
            raise ValueError(f"Impossible de traiter un retour avec le statut {self.status}")
        
        self.status = ReturnStatus.PROCESSED
        self.processed_date = datetime.utcnow()
        
        if restore_stock:
            self.stock_restored = True
            self.stock_restored_date = datetime.utcnow()
        
        self.updated_at = datetime.utcnow()

    def cancel(self, cancelled_by_id: UUID, reason: Optional[str] = None) -> None:
        """Annule le retour"""
        if self.status in [ReturnStatus.PROCESSED, ReturnStatus.CANCELLED]:
            raise ValueError(f"Impossible d'annuler un retour déjà {self.status}")
        
        self.status = ReturnStatus.CANCELLED
        self.rejection_reason = reason
        self.updated_at = datetime.utcnow()

    def mark_synced(self) -> None:
        """Marque le retour comme synchronisé"""
        self.is_synced = True
        self.synced_at = datetime.utcnow()
        self.sync_version += 1
        self.updated_at = datetime.utcnow()

    def to_dict(self, include_items: bool = True) -> Dict[str, Any]:
        """Convertit le retour en dictionnaire"""
        self.ensure_meta_data()
        
        data = {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "pharmacy_id": str(self.pharmacy_id) if self.pharmacy_id else None,
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "return_number": self.return_number,
            "reference": self.reference,
            "return_type": self.return_type.value if self.return_type else None,
            "status": self.status.value if self.status else None,
            "reason": self.reason.value if self.reason else None,
            "sale_id": str(self.sale_id) if self.sale_id else None,
            "purchase_id": str(self.purchase_id) if self.purchase_id else None,
            "invoice_number": self.invoice_number,
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "customer_name": self.customer_name,
            "customer_phone": self.customer_phone,
            "customer_email": self.customer_email,
            "supplier_id": str(self.supplier_id) if self.supplier_id else None,
            "supplier_name": self.supplier_name,
            "return_date": self.return_date.isoformat() if self.return_date else None,
            "requested_date": self.requested_date.isoformat() if self.requested_date else None,
            "approved_date": self.approved_date.isoformat() if self.approved_date else None,
            "processed_date": self.processed_date.isoformat() if self.processed_date else None,
            "subtotal": float(self.subtotal),
            "tax_amount": float(self.tax_amount),
            "total_amount": float(self.total_amount),
            "restocking_fee": float(self.restocking_fee),
            "restocking_fee_percent": float(self.restocking_fee_percent) if self.restocking_fee_percent else None,
            "refund_amount": float(self.refund_amount),
            "refund_method": self.refund_method,
            "refund_date": self.refund_date.isoformat() if self.refund_date else None,
            "credit_note_number": self.credit_note_number,
            "credit_note_issued": self.credit_note_issued,
            "stock_restored": self.stock_restored,
            "stock_restored_date": self.stock_restored_date.isoformat() if self.stock_restored_date else None,
            "stock_movement_id": str(self.stock_movement_id) if self.stock_movement_id else None,
            "approved_by": str(self.approved_by) if self.approved_by else None,
            "approved_by_name": self.approved_by_name,
            "approval_notes": self.approval_notes,
            "rejection_reason": self.rejection_reason,
            "notes": self.notes,
            "internal_notes": self.internal_notes,
            "meta_data": self.meta_data,
            "is_synced": self.is_synced,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
            "sync_version": self.sync_version,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "created_by": str(self.created_by) if self.created_by else None,
            "total_items": self.total_items,
            "net_refund": self.net_refund,
            "days_pending": self.days_pending,
            "processing_time_days": self.processing_time_days,
        }
        
        if include_items and self.items:
            data["items"] = [item.to_dict() for item in self.items]
        
        return data

    def __repr__(self) -> str:
        return f"<Return {self.return_number} - {self.status.value}>"


class ReturnItem(Base):
    """
    Article d'un retour produit
    """
    __tablename__ = "return_items"

    # =====================================
    # IDENTIFIANT UNIQUE
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    return_id = Column(UUID(as_uuid=True), ForeignKey("returns.id"), nullable=False, index=True)

    # =====================================
    # PRODUIT
    # =====================================
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    product_code = Column(String(100), nullable=True)
    product_name = Column(String(200), nullable=False)
    product_barcode = Column(String(100), nullable=True)
    
    # Lot et péremption
    batch_number = Column(String(100), nullable=True)
    expiry_date = Column(Date, nullable=True)
    
    # =====================================
    # QUANTITÉS
    # =====================================
    quantity = Column(Integer, nullable=False, default=1)
    quantity_restored = Column(Integer, nullable=False, default=0)
    quantity_damaged = Column(Integer, nullable=False, default=0)
    
    # =====================================
    # PRIX
    # =====================================
    unit_price = Column(Numeric(12, 2), nullable=False, default=0.00)
    original_unit_price = Column(Numeric(12, 2), nullable=True, comment="Prix original au moment de la vente")
    
    discount_percent = Column(Numeric(5, 2), nullable=False, default=0.00)
    discount_amount = Column(Numeric(12, 2), nullable=False, default=0.00)
    
    tva_rate = Column(Numeric(5, 2), nullable=False, default=0.00)
    tva_amount = Column(Numeric(12, 2), nullable=False, default=0.00)
    
    subtotal = Column(Numeric(12, 2), nullable=False, default=0.00)
    total = Column(Numeric(12, 2), nullable=False, default=0.00)

    # =====================================
    # RAISON SPÉCIFIQUE
    # =====================================
    reason = Column(
        SQLEnum(ReturnReason, name="returnreason_enum"),
        nullable=True
    )
    reason_description = Column(Text, nullable=True)
    
    # =====================================
    # CONDITION
    # =====================================
    condition = Column(String(50), nullable=True, comment="new, opened, damaged, expired")
    condition_notes = Column(Text, nullable=True)

    # =====================================
    # LIEN AVEC L'ITEM DE VENTE ORIGINAL
    # =====================================
    sale_item_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    stock_movement_id = Column(UUID(as_uuid=True), nullable=True)

    # =====================================
    # MÉTADONNÉES
    # =====================================
    meta_data = Column(JSONB, nullable=False, default=dict)

    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # =====================================
    # RELATIONS
    # =====================================
    return_obj = relationship("Return", back_populates="items")
    product = relationship("Product", back_populates="return_items")

    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index("ix_return_items_return_id", "return_id"),
        Index("ix_return_items_product_id", "product_id"),
        Index("ix_return_items_sale_item_id", "sale_item_id"),
        Index("ix_return_items_batch", "batch_number"),
    )

    # =====================================
    # VALIDATIONS
    # =====================================
    @validates("quantity", "quantity_restored", "quantity_damaged")
    def validate_quantities(self, key, value):
        if value is None:
            return 0
        if value < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return int(value)

    @validates("unit_price", "original_unit_price", "discount_percent", "discount_amount", "tva_rate", "tva_amount", "subtotal", "total")
    def validate_amounts(self, key, value):
        if value is None:
            return Decimal("0")
        dec = Decimal(str(value))
        if dec < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return dec

    # =====================================
    # PROPRIÉTÉS CALCULÉES
    # =====================================
    @hybrid_property
    def quantity_restorable(self) -> int:
        """Quantité qui peut encore être restaurée"""
        return max(0, self.quantity - self.quantity_restored - self.quantity_damaged)

    @hybrid_property
    def is_fully_restored(self) -> bool:
        return self.quantity_restored >= self.quantity

    @hybrid_property
    def refund_value(self) -> float:
        """Valeur remboursable pour cet item"""
        return float(self.total or 0)

    # =====================================
    # MÉTHODES
    # =====================================
    def calculate_totals(self) -> None:
        """Recalcule les totaux de l'item"""
        quantity_dec = Decimal(str(self.quantity))
        unit_price_dec = self.unit_price or Decimal("0")
        
        self.subtotal = quantity_dec * unit_price_dec
        
        discount_dec = self.discount_percent or Decimal("0")
        if discount_dec > 0:
            self.discount_amount = self.subtotal * (discount_dec / Decimal("100"))
        else:
            self.discount_amount = Decimal("0")
        
        after_discount = self.subtotal - self.discount_amount
        
        tva_dec = self.tva_rate or Decimal("0")
        if tva_dec > 0:
            self.tva_amount = after_discount * (tva_dec / Decimal("100"))
        else:
            self.tva_amount = Decimal("0")
        
        self.total = after_discount + self.tva_amount

    def restore_quantity(self, quantity: int) -> int:
        """Restaure une partie de la quantité"""
        restorable = self.quantity_restorable
        to_restore = min(quantity, restorable)
        
        self.quantity_restored += to_restore
        self.updated_at = datetime.utcnow()
        
        return to_restore

    def mark_damaged(self, quantity: int) -> int:
        """Marque une partie comme endommagée"""
        available = self.quantity - self.quantity_restored - self.quantity_damaged
        to_damage = min(quantity, available)
        
        self.quantity_damaged += to_damage
        self.updated_at = datetime.utcnow()
        
        return to_damage

    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'item en dictionnaire"""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "return_id": str(self.return_id) if self.return_id else None,
            "product_id": str(self.product_id) if self.product_id else None,
            "product_code": self.product_code,
            "product_name": self.product_name,
            "product_barcode": self.product_barcode,
            "batch_number": self.batch_number,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "quantity": self.quantity,
            "quantity_restored": self.quantity_restored,
            "quantity_damaged": self.quantity_damaged,
            "quantity_restorable": self.quantity_restorable,
            "is_fully_restored": self.is_fully_restored,
            "unit_price": float(self.unit_price),
            "original_unit_price": float(self.original_unit_price) if self.original_unit_price else None,
            "discount_percent": float(self.discount_percent),
            "discount_amount": float(self.discount_amount),
            "tva_rate": float(self.tva_rate),
            "tva_amount": float(self.tva_amount),
            "subtotal": float(self.subtotal),
            "total": float(self.total),
            "refund_value": self.refund_value,
            "reason": self.reason.value if self.reason else None,
            "reason_description": self.reason_description,
            "condition": self.condition,
            "condition_notes": self.condition_notes,
            "sale_item_id": str(self.sale_item_id) if self.sale_item_id else None,
            "stock_movement_id": str(self.stock_movement_id) if self.stock_movement_id else None,
            "meta_data": self.meta_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<ReturnItem {self.product_name} x{self.quantity}>"


# =====================================
# IMPORT POUR LES RELATIONS
# =====================================
# Ces imports sont placés ici pour éviter les imports circulaires
# Ils doivent être décommentés après la création des modèles associés

# from app.models.sale import Sale
# Sale.returns = relationship("Return", back_populates="sale")

# from app.models.product import Product
# Product.return_items = relationship("ReturnItem", back_populates="product")
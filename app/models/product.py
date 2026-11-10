# app/models/product.py
from __future__ import annotations

import uuid
import sqlalchemy as sa
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import (
    Boolean,
    Column,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, validates

from app.db.base import Base
import logging

logger = logging.getLogger(__name__)


def _as_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return default


class Product(Base):
    """
    Produit principal du stock.
    Compatible avec les routes stock.py et le modèle Tenant.
    """
    __tablename__ = "products"

    # =====================================
    # IDENTIFIANT UNIQUE
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
    branch_id = sa.Column(sa.UUID, sa.ForeignKey("branches.id"), nullable=True)
    stock_version = Column(Integer, default=1, nullable=False)
    last_sync_at = Column(DateTime, nullable=True)
    synced_quantity = Column(Integer, nullable=False, default=0, 
                            comment="Dernière quantité synchronisée connue")
    pending_quantity_change = Column(Integer, nullable=False, default=0,
                                    comment="Changement de stock en attente de synchro")
    
    # =====================================
    # IDENTIFICATION DU PRODUIT
    # =====================================
    code = Column(String(50), nullable=True, index=True, comment="Code interne")
    barcode = Column(String(100), nullable=True, index=True, comment="Code-barres")
    name = Column(String(200), nullable=False, index=True)
    commercial_name = Column(String(200), nullable=True)

    # =====================================
    # DESCRIPTION ET COMPOSITION
    # =====================================
    description = Column(Text, nullable=True)
    active_ingredient = Column(String(200), nullable=True)
    dosage = Column(String(100), nullable=True)
    galenic_form = Column(String(100), nullable=True, comment="Comprimé, sirop, injectable, etc.")
    laboratory = Column(String(200), nullable=True)
    dci = Column(String(200), nullable=True, comment="Dénomination Commune Internationale")

    # =====================================
    # CLASSIFICATION
    # =====================================
    category = Column(String(100), nullable=True, index=True)
    subcategory = Column(String(100), nullable=True)
    therapeutic_class = Column(String(200), nullable=True)
    product_type = Column(
        String(30),
        nullable=False,
        default="medicament",
        comment="medicament, parapharmacie, materiel, autre",
    )

    # =====================================
    # GESTION DU STOCK
    # =====================================
    quantity = Column(Integer, nullable=False, default=0)
    available_quantity = Column(Integer, nullable=False, default=0)
    reserved_quantity = Column(Integer, nullable=False, default=0)
    unit = Column(String(50), nullable=False, default="unité")

    alert_threshold = Column(Integer, nullable=False, default=10)
    minimum_stock = Column(Integer, nullable=False, default=5)
    maximum_stock = Column(Integer, nullable=True)

    location = Column(String(100), nullable=True, comment="Emplacement physique")

    # =====================================
    # PRIX ET FINANCES
    # =====================================
    purchase_price = Column(Numeric(12, 2), nullable=False, default=0.00)
    selling_price = Column(Numeric(12, 2), nullable=False, default=0.00)
    wholesale_price = Column(Numeric(12, 2), nullable=True)
    selling_price_retail = Column(Numeric(12, 2), nullable=True, comment="Prix de vente au détail")
    selling_price_wholesale = Column(Numeric(12, 2), nullable=True, comment="Prix de vente en gros")

    tva_rate = Column(Numeric(5, 2), nullable=False, default=0.00, comment="Taux TVA en %")
    has_tva = Column(Boolean, nullable=False, default=False)

    margin_amount = Column(
        Numeric(12, 2),
        Computed("selling_price - purchase_price", persisted=True),
    )

    margin_rate = Column(
        Numeric(8, 2),
        Computed(
            "CASE WHEN purchase_price > 0 THEN ((selling_price - purchase_price) / purchase_price) * 100 ELSE 0 END",
            persisted=True,
        ),
    )

    # =====================================
    # PÉREMPTION ET LOTS
    # =====================================
    expiry_date = Column(Date, nullable=True, index=True)
    batch_number = Column(String(100), nullable=True)
    authorization_number = Column(String(100), nullable=True)

    # =====================================
    # RÉGLEMENTATION
    # =====================================
    packaging = Column(String(100), nullable=True)
    prescription_required = Column(Boolean, nullable=False, default=False)
    regulatory_class = Column(String(50), nullable=True, comment="Classe réglementaire: A, B, C, etc.")

    # =====================================
    # FOURNISSEURS
    # =====================================
    main_supplier = Column(String(200), nullable=True)
    supplier_code = Column(String(100), nullable=True)
    supplier_price = Column(Numeric(12, 2), nullable=True)
    credit_items = relationship("ProductCreditItem", back_populates="product")
    ownership_status = Column(String(30), default="fully_owned", 
                         comment="Statut de propriété: fully_owned, credit, partial_credit, consignment")
    has_credit_portion = Column(Boolean, default=False, comment="Le produit a-t-il une partie à crédit?")

    # =====================================
    # MÉTADONNÉES ET MÉDIAS
    # =====================================
    image_url = Column(String(500), nullable=True)
    leaflet_url = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    meta_data = Column(JSONB, nullable=False, default=dict, comment="Métadonnées JSON supplémentaires")

    # =====================================
    # STATUT ET FLAGS
    # =====================================
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_available = Column(Boolean, nullable=False, default=True, index=True)
    is_discounted = Column(Boolean, nullable=False, default=False)

    stock_status = Column(
        String(20),
        nullable=False,
        default="normal",
        comment="normal, low_stock, out_of_stock, over_stock",
    )
    expiry_status = Column(
        String(20),
        nullable=False,
        default="unknown",
        comment="ok, warning, critical, expired, unknown",
    )

    # =====================================
    # STATISTIQUES
    # =====================================
    total_sold = Column(Integer, nullable=False, default=0)
    total_purchased = Column(Integer, nullable=False, default=0)
    last_sale_date = Column(DateTime, nullable=True)
    last_purchase_date = Column(DateTime, nullable=True)
    last_adjustment_date = Column(DateTime, nullable=True)

    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)

    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", back_populates="products")
    pharmacy = relationship("Pharmacy", back_populates="products")
    branch = relationship("Branch", back_populates="products")

    product_stocks = relationship(
        "ProductStock",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    stock_movements = relationship(
        "StockMovement",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    return_items = relationship("ReturnItem", back_populates="product", cascade="all, delete-orphan")
    
    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index("ix_products_name", "name"),
        Index("ix_products_code", "code"),
        Index("ix_products_barcode", "barcode"),
        Index("ix_products_category", "category"),
        Index("ix_products_expiry_date", "expiry_date"),
        Index("ix_products_stock_status", "stock_status"),
        Index("ix_products_expiry_status", "expiry_status"),
        Index("ix_products_tenant_active", "tenant_id", "is_active"),
        Index("ix_products_tenant_name", "tenant_id", "name"),
        Index("ix_products_tenant_barcode", "tenant_id", "barcode"),
        Index("ix_products_tenant_code_unique_soft", "tenant_id", "code"),
    )

    # =====================================
    # VALIDATIONS
    # =====================================
    @validates("quantity", "available_quantity", "reserved_quantity", "alert_threshold", "minimum_stock")
    def validate_non_negative_int(self, key, value):
        if value is None:
            return 0
        if value < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return int(value)

    @validates("maximum_stock")
    def validate_maximum_stock(self, key, value):
        if value is not None and value < 0:
            raise ValueError("maximum_stock ne peut pas être négatif")
        return value

    @validates("purchase_price", "selling_price", "wholesale_price", "supplier_price", "tva_rate")
    def validate_numeric_non_negative(self, key, value):
        if value is None:
            return value
        dec = _as_decimal(value)
        if dec < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return dec

    @validates("expiry_date")
    def validate_expiry_date(self, key, value):
        return value

    @validates("name")
    def validate_name(self, key, value):
        if not value or not str(value).strip():
            raise ValueError("Le nom du produit est obligatoire")
        return str(value).strip()

    @validates("code", "barcode", "commercial_name", "category", "subcategory", "main_supplier")
    def strip_strings(self, key, value):
        if value is None:
            return value
        value = str(value).strip()
        return value or None
    
    def update_stock_with_version(self, new_quantity: int, expected_version: int = None) -> bool:
        """
        Met à jour le stock avec vérification de version.
        Retourne True si mise à jour réussie, False si conflit.
        """
        if expected_version is not None and self.stock_version != expected_version:
            logger.warning(
                f"Conflit de version pour {self.name}: "
                f"attendue={expected_version}, actuelle={self.stock_version}"
            )
            return False
        
        self.quantity = new_quantity
        self.stock_version += 1
        self.last_sync_at = datetime.utcnow()
        self.refresh_statuses()
        return True
    
    def apply_pending_changes(self):
        """Applique les changements de stock en attente"""
        if self.pending_quantity_change != 0:
            self.quantity += self.pending_quantity_change
            self.pending_quantity_change = 0
            self.stock_version += 1
            self.last_sync_at = datetime.utcnow()
            self.refresh_statuses()

    # =====================================
    # PROPRIÉTÉS CALCULÉES
    # =====================================
    @hybrid_property
    def purchase_value(self) -> float:
        return float((_as_decimal(self.purchase_price) * Decimal(self.quantity or 0)))

    @hybrid_property
    def selling_value(self) -> float:
        return float((_as_decimal(self.selling_price) * Decimal(self.quantity or 0)))

    @hybrid_property
    def total_margin(self) -> float:
        margin = _as_decimal(self.margin_amount)
        return float(margin * Decimal(self.quantity or 0))

    @hybrid_property
    def days_until_expiry(self) -> Optional[int]:
        if not self.expiry_date:
            return None
        return (self.expiry_date - date.today()).days

    @hybrid_property
    def is_expired(self) -> bool:
        return bool(self.expiry_date and self.expiry_date < date.today())

    @hybrid_property
    def is_expiring_soon(self) -> bool:
        days = self.days_until_expiry
        return days is not None and 0 <= days <= 30

    @hybrid_property
    def is_critical_expiry(self) -> bool:
        days = self.days_until_expiry
        return days is not None and 0 <= days <= 7

    @hybrid_property
    def has_low_stock(self) -> bool:
        return (self.quantity or 0) > 0 and (self.quantity or 0) <= (self.alert_threshold or 0)

    @hybrid_property
    def is_out_of_stock(self) -> bool:
        return (self.quantity or 0) <= 0

    @hybrid_property
    def is_over_stock(self) -> bool:
        return self.maximum_stock is not None and (self.quantity or 0) > self.maximum_stock

    # =====================================
    # MÉTHODES
    # =====================================
    def ensure_meta_data(self) -> None:
        if self.meta_data is None or not isinstance(self.meta_data, dict):
            self.meta_data = {}

    def sync_quantities(self) -> None:
        """
        Garantit la cohérence entre quantity / reserved / available.
        """
        self.quantity = max(0, int(self.quantity or 0))
        self.reserved_quantity = max(0, int(self.reserved_quantity or 0))

        if self.reserved_quantity > self.quantity:
            self.reserved_quantity = self.quantity

        self.available_quantity = max(0, self.quantity - self.reserved_quantity)

    def update_stock_status(self) -> None:
        self.sync_quantities()

        if self.is_out_of_stock:
            self.stock_status = "out_of_stock"
        elif self.has_low_stock:
            self.stock_status = "low_stock"
        elif self.is_over_stock:
            self.stock_status = "over_stock"
        else:
            self.stock_status = "normal"

        self.is_available = self.is_active and not self.is_out_of_stock

    def update_expiry_status(self) -> None:
        if not self.expiry_date:
            self.expiry_status = "unknown"
        elif self.is_expired:
            self.expiry_status = "expired"
        elif self.is_critical_expiry:
            self.expiry_status = "critical"
        elif self.is_expiring_soon:
            self.expiry_status = "warning"
        else:
            self.expiry_status = "ok"

    def refresh_statuses(self) -> None:
        self.update_stock_status()
        self.update_expiry_status()

    def adjust_quantity(self, amount: int, reason: str, user_id: Optional[UUID] = None):
        new_quantity = int(self.quantity or 0) + int(amount or 0)
        if new_quantity < 0:
            raise ValueError("La quantité ne peut pas être négative")

        self.quantity = new_quantity
        self.sync_quantities()
        self.last_adjustment_date = datetime.utcnow()
        self.refresh_statuses()
        return self

    def reserve_quantity(self, amount: int):
        amount = int(amount or 0)
        self.sync_quantities()

        if amount <= 0:
            raise ValueError("La quantité à réserver doit être supérieure à 0")
        if amount > self.available_quantity:
            raise ValueError("Quantité non disponible")

        self.reserved_quantity += amount
        self.sync_quantities()
        self.refresh_statuses()
        return self

    def release_reservation(self, amount: int):
        amount = int(amount or 0)
        self.sync_quantities()

        if amount <= 0:
            raise ValueError("La quantité à libérer doit être supérieure à 0")
        if amount > self.reserved_quantity:
            raise ValueError("Quantité réservée insuffisante")

        self.reserved_quantity -= amount
        self.sync_quantities()
        self.refresh_statuses()
        return self

    def calculate_prices(self, margin_percent: Optional[float] = None, tva_rate: Optional[float] = None):
        purchase_price = _as_decimal(self.purchase_price)

        if margin_percent is None:
            margin_percent = 30.0
        if tva_rate is None:
            tva_rate = float(self.tva_rate or 0) if self.has_tva else 0.0

        margin_percent_dec = _as_decimal(margin_percent)
        tva_rate_dec = _as_decimal(tva_rate)

        selling_price_ht = purchase_price * (Decimal("1") + (margin_percent_dec / Decimal("100")))

        if self.has_tva:
            self.selling_price = selling_price_ht * (Decimal("1") + (tva_rate_dec / Decimal("100")))
            self.tva_rate = tva_rate_dec
        else:
            self.selling_price = selling_price_ht
            self.tva_rate = Decimal("0")

        return self

    def to_dict(self, include_details: bool = False) -> Dict[str, Any]:
        self.ensure_meta_data()

        data = {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "pharmacy_id": str(self.pharmacy_id) if self.pharmacy_id else None,
            "code": self.code,
            "barcode": self.barcode,
            "name": self.name,
            "commercial_name": self.commercial_name,
            "category": self.category,
            "product_type": self.product_type,
            "quantity": self.quantity,
            "available_quantity": self.available_quantity,
            "reserved_quantity": self.reserved_quantity,
            "unit": self.unit,
            "alert_threshold": self.alert_threshold,
            "minimum_stock": self.minimum_stock,
            "maximum_stock": self.maximum_stock,
            "purchase_price": float(_as_decimal(self.purchase_price)),
            "selling_price": float(_as_decimal(self.selling_price)),
            "wholesale_price": float(_as_decimal(self.wholesale_price)) if self.wholesale_price is not None else None,
            "tva_rate": float(_as_decimal(self.tva_rate)),
            "has_tva": self.has_tva,
            "margin_amount": float(_as_decimal(self.margin_amount)),
            "margin_rate": float(_as_decimal(self.margin_rate)),
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "batch_number": self.batch_number,
            "days_until_expiry": self.days_until_expiry,
            "stock_status": self.stock_status,
            "expiry_status": self.expiry_status,
            "is_active": self.is_active,
            "is_available": self.is_available,
            "is_expired": self.is_expired,
            "is_expiring_soon": self.is_expiring_soon,
            "is_critical_expiry": self.is_critical_expiry,
            "has_low_stock": self.has_low_stock,
            "is_out_of_stock": self.is_out_of_stock,
            "is_over_stock": self.is_over_stock,
            "purchase_value": self.purchase_value,
            "selling_value": self.selling_value,
            "total_margin": self.total_margin,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_details:
            data.update(
                {
                    "description": self.description,
                    "active_ingredient": self.active_ingredient,
                    "dosage": self.dosage,
                    "galenic_form": self.galenic_form,
                    "laboratory": self.laboratory,
                    "dci": self.dci,
                    "subcategory": self.subcategory,
                    "therapeutic_class": self.therapeutic_class,
                    "location": self.location,
                    "packaging": self.packaging,
                    "prescription_required": self.prescription_required,
                    "regulatory_class": self.regulatory_class,
                    "main_supplier": self.main_supplier,
                    "supplier_code": self.supplier_code,
                    "supplier_price": float(_as_decimal(self.supplier_price)) if self.supplier_price is not None else None,
                    "image_url": self.image_url,
                    "leaflet_url": self.leaflet_url,
                    "notes": self.notes,
                    "authorization_number": self.authorization_number,
                    "meta_data": self.meta_data,
                    "total_sold": self.total_sold,
                    "total_purchased": self.total_purchased,
                    "last_sale_date": self.last_sale_date.isoformat() if self.last_sale_date else None,
                    "last_purchase_date": self.last_purchase_date.isoformat() if self.last_purchase_date else None,
                    "last_adjustment_date": self.last_adjustment_date.isoformat() if self.last_adjustment_date else None,
                }
            )

        return data

    def __repr__(self) -> str:
        return f"<Product {self.code or 'NoCode'}: {self.name} (Stock: {self.quantity})>"


class ProductStock(Base):
    """
    Stock par lot, pour la traçabilité.
    Chaque lot est associé à une pharmacie spécifique.
    """
    __tablename__ = "product_stocks"

    # =====================================
    # IDENTIFIANT UNIQUE
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    
    # ✅ AJOUT CRITIQUE: pharmacy_id pour la gestion multi-pharmacies
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
    
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)

    # =====================================
    # INFORMATION DU LOT
    # =====================================
    batch_number = Column(String(100), nullable=False, index=True)
    expiry_date = Column(Date, nullable=False, index=True)

    # =====================================
    # QUANTITÉS
    # =====================================
    quantity_received = Column(Integer, nullable=False, default=0)
    quantity_available = Column(Integer, nullable=False, default=0)
    quantity_reserved = Column(Integer, nullable=False, default=0)
    quantity_sold = Column(Integer, nullable=False, default=0)
    quantity_lost = Column(Integer, nullable=False, default=0)
    quantity_damaged = Column(Integer, nullable=False, default=0)

    # =====================================
    # PRIX COUTANT
    # =====================================
    cost_price = Column(Numeric(12, 2), nullable=False, default=0.00)

    # =====================================
    # FOURNISSEUR
    # =====================================
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    supplier_name = Column(String(200), nullable=True)
    invoice_number = Column(String(100), nullable=True)
    purchase_date = Column(Date, nullable=True)

    # =====================================
    # EMPLACEMENT
    # =====================================
    location = Column(String(100), nullable=True)
    shelf = Column(String(50), nullable=True)

    # =====================================
    # STATUT
    # =====================================
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    status = Column(
        String(20),
        nullable=False,
        default="available",
        comment="available, reserved, sold, expired, damaged, lost, unavailable",
    )

    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # =====================================
    # RELATIONS
    # =====================================
    product = relationship("Product", back_populates="product_stocks")
    tenant = relationship("Tenant")
    pharmacy = relationship("Pharmacy", back_populates="product_stocks")

    stock_movements = relationship(
        "StockMovement",
        back_populates="product_stock",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index("ix_product_stocks_batch_expiry", "batch_number", "expiry_date"),
        Index("ix_product_stocks_product_status", "product_id", "status"),
        Index("ix_product_stocks_expiry_status", "expiry_date", "status"),
        Index("ix_product_stocks_tenant_active", "tenant_id", "is_active"),
        Index("ix_product_stocks_pharmacy_product", "pharmacy_id", "product_id"),
        Index("ix_product_stocks_pharmacy_status", "pharmacy_id", "status"),
    )

    # =====================================
    # VALIDATIONS
    # =====================================
    @validates(
        "quantity_received",
        "quantity_available",
        "quantity_reserved",
        "quantity_sold",
        "quantity_lost",
        "quantity_damaged",
    )
    def validate_quantities(self, key, value):
        if value is None:
            return 0
        if value < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return int(value)

    @validates("cost_price")
    def validate_cost_price(self, key, value):
        dec = _as_decimal(value)
        if dec < 0:
            raise ValueError("cost_price ne peut pas être négatif")
        return dec

    @validates("expiry_date")
    def validate_expiry_date(self, key, value):
        return value

    @validates("pharmacy_id")
    def validate_pharmacy_id(self, key, value):
        if value is None:
            raise ValueError("pharmacy_id est obligatoire pour ProductStock")
        return value

    # =====================================
    # PROPRIÉTÉS CALCULÉES
    # =====================================
    @hybrid_property
    def total_quantity(self) -> int:
        return (
            int(self.quantity_available or 0)
            + int(self.quantity_reserved or 0)
            + int(self.quantity_sold or 0)
            + int(self.quantity_lost or 0)
            + int(self.quantity_damaged or 0)
        )

    @hybrid_property
    def is_expired(self) -> bool:
        return bool(self.expiry_date and self.expiry_date < date.today())

    @hybrid_property
    def days_until_expiry(self) -> Optional[int]:
        if not self.expiry_date:
            return None
        return (self.expiry_date - date.today()).days

    @hybrid_property
    def is_expiring_soon(self) -> bool:
        days = self.days_until_expiry
        return days is not None and 0 <= days <= 30

    @hybrid_property
    def is_critical_expiry(self) -> bool:
        days = self.days_until_expiry
        return days is not None and 0 <= days <= 7

    @hybrid_property
    def stock_value(self) -> float:
        return float(_as_decimal(self.cost_price) * Decimal(int(self.quantity_available or 0)))

    # =====================================
    # MÉTHODES
    # =====================================
    def update_status(self) -> None:
        if self.is_expired:
            self.status = "expired"
        elif (self.quantity_available or 0) == 0:
            self.status = "sold" if (self.quantity_sold or 0) > 0 else "unavailable"
        elif (self.quantity_reserved or 0) > 0:
            self.status = "reserved"
        else:
            self.status = "available"

    def reserve(self, quantity: int):
        quantity = int(quantity or 0)
        if quantity <= 0:
            raise ValueError("La quantité à réserver doit être supérieure à 0")
        if quantity > (self.quantity_available or 0):
            raise ValueError(
                f"Quantité disponible insuffisante. Disponible: {self.quantity_available}, Demandé: {quantity}"
            )

        self.quantity_reserved += quantity
        self.quantity_available -= quantity
        self.update_status()
        return self

    def release_reservation(self, quantity: int):
        quantity = int(quantity or 0)
        if quantity <= 0:
            raise ValueError("La quantité à libérer doit être supérieure à 0")
        if quantity > (self.quantity_reserved or 0):
            raise ValueError(
                f"Quantité réservée insuffisante. Réservée: {self.quantity_reserved}, Demandé: {quantity}"
            )

        self.quantity_reserved -= quantity
        self.quantity_available += quantity
        self.update_status()
        return self

    def sell(self, quantity: int):
        quantity = int(quantity or 0)
        if quantity <= 0:
            raise ValueError("La quantité à vendre doit être supérieure à 0")
        if quantity > (self.quantity_available or 0):
            raise ValueError(
                f"Quantité disponible insuffisante. Disponible: {self.quantity_available}, Demandé: {quantity}"
            )

        self.quantity_sold += quantity
        self.quantity_available -= quantity
        self.update_status()
        return self

    def adjust_quantity(self, new_quantity: int, reason: str):
        new_quantity = int(new_quantity or 0)
        if new_quantity < 0:
            raise ValueError("La quantité ne peut pas être négative")

        self.quantity_available = new_quantity
        self.update_status()
        return self

    def mark_damaged(self, quantity: int):
        quantity = int(quantity or 0)
        if quantity <= 0:
            raise ValueError("La quantité doit être supérieure à 0")
        if quantity > (self.quantity_available or 0):
            raise ValueError("Quantité disponible insuffisante")

        self.quantity_damaged += quantity
        self.quantity_available -= quantity
        self.update_status()
        return self

    def mark_lost(self, quantity: int):
        quantity = int(quantity or 0)
        if quantity <= 0:
            raise ValueError("La quantité doit être supérieure à 0")
        if quantity > (self.quantity_available or 0):
            raise ValueError("Quantité disponible insuffisante")

        self.quantity_lost += quantity
        self.quantity_available -= quantity
        self.update_status()
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "pharmacy_id": str(self.pharmacy_id) if self.pharmacy_id else None,
            "product_id": str(self.product_id) if self.product_id else None,
            "batch_number": self.batch_number,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "quantity_received": self.quantity_received,
            "quantity_available": self.quantity_available,
            "quantity_reserved": self.quantity_reserved,
            "quantity_sold": self.quantity_sold,
            "quantity_lost": self.quantity_lost,
            "quantity_damaged": self.quantity_damaged,
            "cost_price": float(_as_decimal(self.cost_price)),
            "supplier_name": self.supplier_name,
            "invoice_number": self.invoice_number,
            "purchase_date": self.purchase_date.isoformat() if self.purchase_date else None,
            "location": self.location,
            "shelf": self.shelf,
            "is_active": self.is_active,
            "status": self.status,
            "is_expired": self.is_expired,
            "days_until_expiry": self.days_until_expiry,
            "is_expiring_soon": self.is_expiring_soon,
            "is_critical_expiry": self.is_critical_expiry,
            "stock_value": self.stock_value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<ProductStock {self.batch_number} - {self.expiry_date} (Pharmacy: {self.pharmacy_id}, Disponible: {self.quantity_available})>"


from app.models import stock_movement as _stock_movement  # noqa: E402,F401
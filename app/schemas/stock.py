# app/schemas/stock.py
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProductType(str, Enum):
    MEDICAMENT = "medicament"
    PARAPHARMACIE = "parapharmacie"
    MATERIEL = "materiel"
    AUTRE = "autre"


class StockStatus(str, Enum):
    NORMAL = "normal"
    LOW_STOCK = "low_stock"
    OUT_OF_STOCK = "out_of_stock"
    OVER_STOCK = "over_stock"


class ExpiryStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class ExportFormat(str, Enum):
    EXCEL = "excel"
    PDF = "pdf"
    CSV = "csv"

class SalesImpactResponse(BaseModel):
    """Réponse pour l'impact des ventes sur le stock"""
    product_id: UUID
    product_code: str
    product_name: str
    unit: str
    total_sold: int
    total_revenue: float
    sale_count: int
    average_price: float
    current_stock: Optional[int] = None
    alert_threshold: Optional[int] = None
    stock_status: Optional[str] = None
    stock_value: Optional[float] = None

class StockMovementResponse(BaseModel):
    """Réponse pour les mouvements de stock"""
    id: UUID
    product_id: UUID
    product_name: str
    product_code: str
    pharmacy_id: UUID
    quantity_before: Decimal
    quantity_after: Decimal
    quantity_change: Decimal
    movement_type: str
    reason: str
    reference: Optional[str] = None
    batch_number: Optional[str] = None
    cost_price: Optional[Decimal] = None
    selling_price: Optional[Decimal] = None
    sale_id: Optional[UUID] = None
    sale_item_id: Optional[UUID] = None
    created_at: datetime
    created_by: Optional[UUID] = None


# =========================================================
# BASE PRODUIT
# =========================================================

class ProductBase(BaseModel):
    """Schéma de base pour un produit."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # Identification
    code: Optional[str] = Field(None, min_length=1, max_length=50, description="Code interne")
    barcode: Optional[str] = Field(None, max_length=100, description="Code-barres")
    name: str = Field(..., min_length=2, max_length=200, description="Nom du produit")
    commercial_name: Optional[str] = Field(None, max_length=200)

    # Description
    description: Optional[str] = None
    active_ingredient: Optional[str] = Field(None, max_length=200)
    dosage: Optional[str] = Field(None, max_length=100)
    galenic_form: Optional[str] = Field(None, max_length=100)
    laboratory: Optional[str] = Field(None, max_length=200)
    dci: Optional[str] = Field(None, max_length=200)

    # Classification
    category: Optional[str] = Field(None, max_length=100)
    subcategory: Optional[str] = Field(None, max_length=100)
    therapeutic_class: Optional[str] = Field(None, max_length=200)
    product_type: ProductType = Field(default=ProductType.MEDICAMENT)

    # Stock
    quantity: int = Field(default=0, ge=0)
    unit: str = Field(default="unité", max_length=50)
    alert_threshold: int = Field(default=10, ge=0)
    minimum_stock: int = Field(default=5, ge=0)
    maximum_stock: Optional[int] = Field(None, ge=0)

    # Prix
    purchase_price: float = Field(..., ge=0, description="Prix d'achat")
    selling_price: float = Field(..., ge=0, description="Prix de vente")
    wholesale_price: Optional[float] = Field(None, ge=0)
    has_tva: bool = Field(default=False)
    tva_rate: float = Field(default=0.0, ge=0, le=100)

    # Péremption
    expiry_date: Optional[date] = None
    batch_number: Optional[str] = Field(None, max_length=100)
    authorization_number: Optional[str] = Field(None, max_length=100)

    # Réglementation
    packaging: Optional[str] = Field(None, max_length=100)
    prescription_required: bool = Field(default=False)
    regulatory_class: Optional[str] = Field(None, max_length=50)

    # Gestion
    main_supplier: Optional[str] = Field(None, max_length=200)
    location: Optional[str] = Field(None, max_length=100)

    # Médias / méta
    image_url: Optional[str] = Field(None, max_length=500)
    leaflet_url: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "code",
        "barcode",
        "commercial_name",
        "category",
        "subcategory",
        "therapeutic_class",
        "active_ingredient",
        "dosage",
        "galenic_form",
        "laboratory",
        "dci",
        "batch_number",
        "authorization_number",
        "packaging",
        "regulatory_class",
        "main_supplier",
        "location",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, value):
        if value == "":
            return None
        return value

    @model_validator(mode="after")
    def validate_stock_limits(self):
        if self.maximum_stock is not None and self.minimum_stock > self.maximum_stock:
            raise ValueError("Le stock minimum ne peut pas être supérieur au stock maximum")
        return self


# =========================================================
# CRÉATION
# =========================================================
class ProductCreate(ProductBase):
    """Création d'un nouveau produit."""

    pharmacy_id: UUID = Field(..., description="Pharmacie propriétaire du produit")
    
    # Nouveaux champs pour la gestion des prix
    calcul_auto_prix: Optional[bool] = Field(None, description="Calcul automatique des prix")
    marge_par_defaut: Optional[float] = Field(None, ge=0, description="Marge par défaut (%)")
    sales_type: Optional[str] = Field(None, pattern="^(retail|wholesale|both)$", description="Type de vente")
    selling_price_retail: Optional[float] = Field(None, ge=0, description="Prix de vente détail")
    selling_price_wholesale: Optional[float] = Field(None, ge=0, description="Prix de vente gros")

    @field_validator("expiry_date")
    @classmethod
    def validate_expiry_date(cls, value: Optional[date]):
        if value and value < date.today():
            raise ValueError("La date de péremption ne peut pas être dans le passé")
        return value

    @model_validator(mode="after")
    def validate_business_rules(self):
        if self.selling_price < self.purchase_price:
            raise ValueError("Le prix de vente ne peut pas être inférieur au prix d'achat")

        if self.has_tva is False and self.tva_rate not in (0, 0.0):
            raise ValueError("Le taux TVA doit être 0 si has_tva=False")

        return self

# =========================================================
# MISE À JOUR
# =========================================================

class ProductUpdate(BaseModel):
    """Mise à jour d'un produit."""

    model_config = ConfigDict(str_strip_whitespace=True)

    # Identification / description
    code: Optional[str] = Field(None, min_length=1, max_length=50)
    barcode: Optional[str] = Field(None, max_length=100)
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    commercial_name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    active_ingredient: Optional[str] = Field(None, max_length=200)
    dosage: Optional[str] = Field(None, max_length=100)
    galenic_form: Optional[str] = Field(None, max_length=100)
    laboratory: Optional[str] = Field(None, max_length=200)
    dci: Optional[str] = Field(None, max_length=200)

    # Classification
    category: Optional[str] = Field(None, max_length=100)
    subcategory: Optional[str] = Field(None, max_length=100)
    therapeutic_class: Optional[str] = Field(None, max_length=200)
    product_type: Optional[ProductType] = None

    # Prix
    purchase_price: Optional[float] = Field(None, ge=0)
    selling_price: Optional[float] = Field(None, ge=0)
    wholesale_price: Optional[float] = Field(None, ge=0)
    has_tva: Optional[bool] = None
    tva_rate: Optional[float] = Field(None, ge=0, le=100)

    # Stock
    quantity: Optional[int] = Field(None, ge=0)
    alert_threshold: Optional[int] = Field(None, ge=0)
    minimum_stock: Optional[int] = Field(None, ge=0)
    maximum_stock: Optional[int] = Field(None, ge=0)

    # Péremption
    expiry_date: Optional[date] = None
    batch_number: Optional[str] = Field(None, max_length=100)
    authorization_number: Optional[str] = Field(None, max_length=100)

    # Réglementation / gestion
    packaging: Optional[str] = Field(None, max_length=100)
    prescription_required: Optional[bool] = None
    regulatory_class: Optional[str] = Field(None, max_length=50)
    main_supplier: Optional[str] = Field(None, max_length=200)
    location: Optional[str] = Field(None, max_length=100)

    # Médias / méta
    image_url: Optional[str] = Field(None, max_length=500)
    leaflet_url: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None
    meta_data: Optional[Dict[str, Any]] = None

    # Statut
    is_active: Optional[bool] = None
    is_available: Optional[bool] = None

    @field_validator(
        "code",
        "barcode",
        "commercial_name",
        "category",
        "subcategory",
        "therapeutic_class",
        "active_ingredient",
        "dosage",
        "galenic_form",
        "laboratory",
        "dci",
        "batch_number",
        "authorization_number",
        "packaging",
        "regulatory_class",
        "main_supplier",
        "location",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, value):
        if value == "":
            return None
        return value

    @model_validator(mode="after")
    def validate_update_rules(self):
        if (
            self.purchase_price is not None
            and self.selling_price is not None
            and self.selling_price < self.purchase_price
        ):
            raise ValueError("Le prix de vente ne peut pas être inférieur au prix d'achat")

        if (
            self.minimum_stock is not None
            and self.maximum_stock is not None
            and self.minimum_stock > self.maximum_stock
        ):
            raise ValueError("Le stock minimum ne peut pas être supérieur au stock maximum")

        if self.has_tva is False and self.tva_rate not in (None, 0, 0.0):
            raise ValueError("Le taux TVA doit être 0 si has_tva=False")

        return self


# =========================================================
# LECTURE
# =========================================================

class ProductInDB(ProductBase):
    """Produit tel que stocké en base."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    pharmacy_id: UUID

    # Statuts calculés
    available_quantity: int
    reserved_quantity: int = 0
    stock_status: StockStatus
    expiry_status: ExpiryStatus

    # Valeurs calculées
    purchase_value: float
    selling_value: float
    total_margin: float
    margin_rate: float

    # Historique
    total_sold: int = 0
    total_purchased: int = 0
    last_sale_date: Optional[datetime] = None
    last_purchase_date: Optional[datetime] = None
    last_adjustment_date: Optional[datetime] = None

    # Flags
    is_active: bool = True
    is_available: bool = True
    is_discounted: bool = False

    # Temps
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None


class ProductResponse(BaseModel):
    """Réponse API pour les opérations sur les produits."""

    message: str
    product: ProductInDB


class ProductListResponse(BaseModel):
    """Réponse pour la liste paginée des produits."""

    total: int
    page: int
    limit: int
    products: List[ProductInDB]
    summary: Dict[str, Any] = Field(
        default_factory=lambda: {
            "total_products": 0,
            "total_value_purchase": 0.0,
            "total_value_selling": 0.0,
            "out_of_stock": 0,
            "low_stock": 0,
            "expired_soon": 0,
        }
    )


# =========================================================
# STOCK
# =========================================================

class StockAdjustment(BaseModel):
    """Ajustement manuel de stock."""

    product_id: UUID
    new_quantity: int = Field(..., ge=0)
    reason: str = Field(..., min_length=2, max_length=200)
    notes: Optional[str] = None


class StockMovementFilter(BaseModel):
    """Filtres pour les mouvements de stock."""

    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    product_id: Optional[UUID] = None
    movement_type: Optional[str] = None
    created_by: Optional[UUID] = None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("La date de début ne peut pas être après la date de fin")
        return self


class InventoryCountRequest(BaseModel):
    """Demande de comptage d'inventaire."""

    product_id: UUID
    counted_quantity: int = Field(..., ge=0)
    notes: Optional[str] = None


# =========================================================
# RECHERCHE
# =========================================================

class ProductSearch(BaseModel):
    """Recherche avancée de produits."""

    query: Optional[str] = None
    category: Optional[str] = None
    supplier: Optional[str] = None
    stock_status: Optional[StockStatus] = None
    expiry_status: Optional[ExpiryStatus] = None
    barcode: Optional[str] = None
    code: Optional[str] = None
    pharmacy_id: Optional[UUID] = None


# =========================================================
# STATS
# =========================================================

class StockStats(BaseModel):
    """Statistiques globales du stock."""

    total_products: int
    total_items: int
    total_purchase_value: float
    total_selling_value: float
    average_margin_rate: float
    out_of_stock_count: int
    low_stock_count: int
    expired_count: int
    expiring_soon_count: int
    category_distribution: Dict[str, int]
    value_by_category: Dict[str, float]


# =========================================================
# FUSION
# =========================================================

class ProductMergeRequest(BaseModel):
    """Demande de fusion de produits."""

    product_ids: List[UUID] = Field(..., min_length=2)
    merge_strategy: str = Field(
        default="average",
        pattern="^(average|max|min|first)$",
        description="Stratégie de fusion des prix",
    )
    expiry_strategy: str = Field(
        default="most_recent",
        pattern="^(most_recent|most_ancient|none)$",
        description="Stratégie de fusion des dates de péremption",
    )
    keep_product_id: UUID = Field(..., description="ID du produit à conserver")

    @model_validator(mode="after")
    def validate_merge_request(self):
        if self.keep_product_id not in self.product_ids:
            raise ValueError("keep_product_id doit appartenir à product_ids")
        return self
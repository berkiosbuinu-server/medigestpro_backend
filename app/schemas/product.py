# app/schemas/product.py
from pydantic import BaseModel, Field, validator, model_validator
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, date
from uuid import UUID
from enum import Enum


# ========================
# ENUMS
# ========================
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


# ========================
# BASE SCHEMAS
# ========================
class ProductBase(BaseModel):
    """Schéma de base pour un produit"""
    code: Optional[str] = Field(None, max_length=50, description="Code interne")
    barcode: Optional[str] = Field(None, max_length=100, description="Code-barres")
    name: str = Field(..., min_length=2, max_length=200, description="Nom du produit")
    commercial_name: Optional[str] = Field(None, max_length=200)
    
    # Description et composition
    description: Optional[str] = None
    active_ingredient: Optional[str] = Field(None, max_length=200)
    dosage: Optional[str] = Field(None, max_length=100)
    galenic_form: Optional[str] = Field(None, max_length=100, description="Comprimé, sirop, injectable, etc.")
    laboratory: Optional[str] = Field(None, max_length=200)
    dci: Optional[str] = Field(None, max_length=200, description="Dénomination Commune Internationale")
    
    # Classification
    category: Optional[str] = Field(None, max_length=100)
    subcategory: Optional[str] = Field(None, max_length=100)
    therapeutic_class: Optional[str] = Field(None, max_length=200)
    product_type: ProductType = Field(default=ProductType.MEDICAMENT)
    
    # Gestion du stock
    quantity: int = Field(default=0, ge=0)
    unit: str = Field(default="unité", max_length=50)
    alert_threshold: int = Field(default=10, ge=0)
    minimum_stock: int = Field(default=5, ge=0)
    maximum_stock: Optional[int] = Field(None, ge=0)
    location: Optional[str] = Field(None, max_length=100, description="Emplacement physique")
    
    # Prix et finances
    purchase_price: float = Field(..., ge=0, description="Prix d'achat")
    selling_price: float = Field(..., ge=0, description="Prix de vente")
    wholesale_price: Optional[float] = Field(None, ge=0)
    tva_rate: float = Field(default=0.0, ge=0, le=100)
    has_tva: bool = Field(default=False)
    
    # Péremption et lots
    expiry_date: Optional[date] = None
    batch_number: Optional[str] = Field(None, max_length=100)
    authorization_number: Optional[str] = Field(None, max_length=100)
    
    # Réglementation
    packaging: Optional[str] = Field(None, max_length=100)
    prescription_required: bool = Field(default=False)
    regulatory_class: Optional[str] = Field(None, max_length=50, description="Classe réglementaire: A, B, C, etc.")
    
    # Fournisseurs
    main_supplier: Optional[str] = Field(None, max_length=200)
    supplier_code: Optional[str] = Field(None, max_length=100)
    supplier_price: Optional[float] = Field(None, ge=0)
    
    # Métadonnées et médias
    image_url: Optional[str] = Field(None, max_length=500)
    leaflet_url: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None
    
    # Statut
    is_active: bool = Field(default=True)
    is_discounted: bool = Field(default=False)
    
    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "name": "Paracétamol 500mg",
                "code": "PARA-500",
                "category": "Analgésique",
                "purchase_price": 5.5,
                "selling_price": 10.0,
                "quantity": 100
            }
        }
    }


# ========================
# CREATE SCHEMAS
# ========================
class ProductCreate(ProductBase):
    """Schéma pour la création d'un nouveau produit"""
    tenant_id: UUID
    pharmacy_id: UUID
    
    @validator('expiry_date')
    def validate_expiry_date(cls, v):
        if v and v < date.today():
            raise ValueError('La date de péremption ne peut pas être dans le passé')
        return v
    
    @model_validator(mode='after')
    def validate_prices(self):
        if self.selling_price <= self.purchase_price:
            raise ValueError('Le prix de vente doit être supérieur au prix d\'achat')
        
        if self.wholesale_price and self.wholesale_price >= self.selling_price:
            raise ValueError('Le prix de gros doit être inférieur au prix de vente')
        
        if self.supplier_price and self.supplier_price > self.purchase_price:
            raise ValueError('Le prix fournisseur ne peut pas être supérieur au prix d\'achat')
        
        return self
    
    @model_validator(mode='after')
    def validate_stock_limits(self):
        if self.maximum_stock is not None and self.minimum_stock > self.maximum_stock:
            raise ValueError('Le stock minimum ne peut pas être supérieur au stock maximum')
        
        if self.alert_threshold > self.minimum_stock:
            raise ValueError('Le seuil d\'alerte ne peut pas être supérieur au stock minimum')
        
        return self


class ProductBulkCreate(BaseModel):
    """Schéma pour la création en masse de produits"""
    products: List[ProductCreate]
    batch_operation: bool = Field(default=True)
    skip_duplicates: bool = Field(default=True)
    
    @validator('products')
    def validate_product_list(cls, v):
        if len(v) > 1000:
            raise ValueError('Maximum 1000 produits par opération')
        return v


# ========================
# UPDATE SCHEMAS
# ========================
class ProductUpdate(BaseModel):
    """Schéma pour la mise à jour d'un produit"""
    code: Optional[str] = Field(None, max_length=50)
    barcode: Optional[str] = Field(None, max_length=100)
    name: Optional[str] = Field(None, min_length=2, max_length=200)
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
    product_type: Optional[ProductType] = None
    
    # Stock
    quantity: Optional[int] = Field(None, ge=0)
    unit: Optional[str] = Field(None, max_length=50)
    alert_threshold: Optional[int] = Field(None, ge=0)
    minimum_stock: Optional[int] = Field(None, ge=0)
    maximum_stock: Optional[int] = Field(None, ge=0)
    location: Optional[str] = Field(None, max_length=100)
    
    # Prix
    purchase_price: Optional[float] = Field(None, ge=0)
    selling_price: Optional[float] = Field(None, ge=0)
    wholesale_price: Optional[float] = Field(None, ge=0)
    tva_rate: Optional[float] = Field(None, ge=0, le=100)
    has_tva: Optional[bool] = None
    
    # Péremption
    expiry_date: Optional[date] = None
    batch_number: Optional[str] = Field(None, max_length=100)
    authorization_number: Optional[str] = Field(None, max_length=100)
    
    # Réglementation
    packaging: Optional[str] = Field(None, max_length=100)
    prescription_required: Optional[bool] = None
    regulatory_class: Optional[str] = Field(None, max_length=50)
    
    # Fournisseurs
    main_supplier: Optional[str] = Field(None, max_length=200)
    supplier_code: Optional[str] = Field(None, max_length=100)
    supplier_price: Optional[float] = Field(None, ge=0)
    
    # Métadonnées
    image_url: Optional[str] = Field(None, max_length=500)
    leaflet_url: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = None
    
    # Statut
    is_active: Optional[bool] = None
    is_discounted: Optional[bool] = None
    
    @model_validator(mode='after')
    def validate_prices(self):
        selling_price = getattr(self, 'selling_price', None)
        purchase_price = getattr(self, 'purchase_price', None)
        wholesale_price = getattr(self, 'wholesale_price', None)
        supplier_price = getattr(self, 'supplier_price', None)
        
        if selling_price is not None and purchase_price is not None:
            if selling_price <= purchase_price:
                raise ValueError('Le prix de vente doit être supérieur au prix d\'achat')
        
        if wholesale_price is not None and selling_price is not None:
            if wholesale_price >= selling_price:
                raise ValueError('Le prix de gros doit être inférieur au prix de vente')
        
        if supplier_price is not None and purchase_price is not None:
            if supplier_price > purchase_price:
                raise ValueError('Le prix fournisseur ne peut pas être supérieur au prix d\'achat')
        
        return self
    
    @model_validator(mode='after')
    def validate_stock_limits(self):
        minimum_stock = getattr(self, 'minimum_stock', None)
        maximum_stock = getattr(self, 'maximum_stock', None)
        alert_threshold = getattr(self, 'alert_threshold', None)
        
        if minimum_stock is not None and maximum_stock is not None:
            if minimum_stock > maximum_stock:
                raise ValueError('Le stock minimum ne peut pas être supérieur au stock maximum')
        
        if alert_threshold is not None and minimum_stock is not None:
            if alert_threshold > minimum_stock:
                raise ValueError('Le seuil d\'alerte ne peut pas être supérieur au stock minimum')
        
        return self


class ProductPriceUpdate(BaseModel):
    """Schéma spécifique pour la mise à jour des prix"""
    purchase_price: Optional[float] = Field(None, ge=0)
    selling_price: Optional[float] = Field(None, ge=0)
    wholesale_price: Optional[float] = Field(None, ge=0)
    tva_rate: Optional[float] = Field(None, ge=0, le=100)
    has_tva: Optional[bool] = None
    
    @model_validator(mode='after')
    def validate_prices(self):
        selling_price = getattr(self, 'selling_price', None)
        purchase_price = getattr(self, 'purchase_price', None)
        wholesale_price = getattr(self, 'wholesale_price', None)
        
        if selling_price is not None and purchase_price is not None:
            if selling_price <= purchase_price:
                raise ValueError('Le prix de vente doit être supérieur au prix d\'achat')
        
        if wholesale_price is not None and selling_price is not None:
            if wholesale_price >= selling_price:
                raise ValueError('Le prix de gros doit être inférieur au prix de vente')
        
        return self


class ProductStockUpdate(BaseModel):
    """Schéma spécifique pour la mise à jour du stock"""
    quantity: Optional[int] = Field(None, ge=0)
    alert_threshold: Optional[int] = Field(None, ge=0)
    minimum_stock: Optional[int] = Field(None, ge=0)
    maximum_stock: Optional[int] = Field(None, ge=0)
    location: Optional[str] = Field(None, max_length=100)
    
    @model_validator(mode='after')
    def validate_stock_limits(self):
        minimum_stock = getattr(self, 'minimum_stock', None)
        maximum_stock = getattr(self, 'maximum_stock', None)
        alert_threshold = getattr(self, 'alert_threshold', None)
        
        if minimum_stock is not None and maximum_stock is not None:
            if minimum_stock > maximum_stock:
                raise ValueError('Le stock minimum ne peut pas être supérieur au stock maximum')
        
        if alert_threshold is not None and minimum_stock is not None:
            if alert_threshold > minimum_stock:
                raise ValueError('Le seuil d\'alerte ne peut pas être supérieur au stock minimum')
        
        return self


# ========================
# RESPONSE SCHEMAS
# ========================
class ProductResponse(ProductBase):
    """Schéma de réponse complet pour un produit"""
    id: UUID
    tenant_id: UUID
    pharmacy_id: UUID
    
    # Quantités calculées
    available_quantity: int
    reserved_quantity: int
    stock_status: StockStatus
    expiry_status: ExpiryStatus
    
    # Marges calculées
    margin_amount: float
    margin_rate: float
    
    # Valeurs calculées
    purchase_value: float
    selling_value: float
    total_margin: float
    
    # Jours avant péremption
    days_until_expiry: Optional[int]
    
    # Flags calculés
    is_expired: bool
    is_expiring_soon: bool
    is_critical_expiry: bool
    has_low_stock: bool
    is_out_of_stock: bool
    is_over_stock: bool
    is_available: bool
    
    # Statistiques
    total_sold: int
    total_purchased: int
    last_sale_date: Optional[datetime]
    last_purchase_date: Optional[datetime]
    last_adjustment_date: Optional[datetime]
    
    # Timestamps
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class ProductSummaryResponse(BaseModel):
    """Version allégée pour les listes"""
    id: UUID
    code: Optional[str]
    barcode: Optional[str]
    name: str
    commercial_name: Optional[str]
    category: Optional[str]
    product_type: str
    
    # Stock
    quantity: int
    available_quantity: int
    unit: str
    
    # Prix
    purchase_price: float
    selling_price: float
    margin_rate: float
    
    # Statuts
    stock_status: str
    expiry_status: str
    is_active: bool
    is_available: bool
    is_expired: bool
    
    # Dates importantes
    expiry_date: Optional[date]
    days_until_expiry: Optional[int]
    
    # Valeurs
    purchase_value: float
    selling_value: float
    
    class Config:
        from_attributes = True


class ProductDetailResponse(ProductResponse):
    """Réponse détaillée avec informations supplémentaires"""
    # Relations (optionnelles)
    pharmacy_name: Optional[str] = None
    supplier_name: Optional[str] = None
    
    # Historique récent (optionnel)
    recent_movements: Optional[List[Dict[str, Any]]] = None
    batch_stocks: Optional[List[Dict[str, Any]]] = None
    
    class Config:
        from_attributes = True


# ========================
# LIST RESPONSE SCHEMAS
# ========================
class ProductListResponse(BaseModel):
    """Réponse pour la liste paginée des produits"""
    total: int
    page: int
    page_size: int
    total_pages: int
    products: List[ProductSummaryResponse]
    
    # Statistiques globales
    summary: Dict[str, Any] = Field(
        default_factory=lambda: {
            "total_products": 0,
            "total_value_purchase": 0.0,
            "total_value_selling": 0.0,
            "total_margin_value": 0.0,
            "average_margin_rate": 0.0,
            "out_of_stock_count": 0,
            "low_stock_count": 0,
            "expired_count": 0,
            "expiring_soon_count": 0,
            "critical_expiry_count": 0
        }
    )
    
    class Config:
        from_attributes = True


# ========================
# STOCK OPERATION SCHEMAS
# ========================
class StockAdjustment(BaseModel):
    """Ajustement de stock manuel"""
    product_id: UUID
    quantity_change: int = Field(..., description="Changement de quantité (positif ou négatif)")
    reason: str = Field(..., max_length=200)
    notes: Optional[str] = None
    batch_number: Optional[str] = Field(None, max_length=100)
    expiry_date: Optional[date] = None
    
    @validator('quantity_change')
    def validate_quantity_change(cls, v):
        if v == 0:
            raise ValueError('Le changement de quantité ne peut pas être zéro')
        return v


class StockReservation(BaseModel):
    """Réservation de stock pour une vente en attente"""
    product_id: UUID
    quantity: int = Field(..., gt=0)
    reservation_reason: str = Field(..., max_length=200)
    expires_at: Optional[datetime] = None
    notes: Optional[str] = None


class StockRelease(BaseModel):
    """Libération d'une réservation de stock"""
    product_id: UUID
    quantity: int = Field(..., gt=0)
    reason: str = Field(..., max_length=200)
    notes: Optional[str] = None


class BulkStockOperation(BaseModel):
    """Opération de stock en masse"""
    operations: List[Union[StockAdjustment, StockReservation, StockRelease]]
    
    @validator('operations')
    def validate_operations(cls, v):
        if len(v) > 100:
            raise ValueError('Maximum 100 opérations par lot')
        return v


# ========================
# SEARCH AND FILTER SCHEMAS
# ========================
class ProductSearch(BaseModel):
    """Critères de recherche de produits"""
    query: Optional[str] = Field(None, description="Recherche texte (nom, code, DCI, etc.)")
    barcode: Optional[str] = Field(None, description="Recherche par code-barres")
    code: Optional[str] = Field(None, description="Recherche par code interne")
    
    # Filtres
    category: Optional[str] = None
    subcategory: Optional[str] = None
    product_type: Optional[ProductType] = None
    therapeutic_class: Optional[str] = None
    laboratory: Optional[str] = None
    
    # Filtres de stock
    stock_status: Optional[StockStatus] = None
    expiry_status: Optional[ExpiryStatus] = None
    min_quantity: Optional[int] = Field(None, ge=0)
    max_quantity: Optional[int] = Field(None, ge=0)
    has_expiry_date: Optional[bool] = None
    
    # Filtres de prix
    min_purchase_price: Optional[float] = Field(None, ge=0)
    max_purchase_price: Optional[float] = Field(None, ge=0)
    min_selling_price: Optional[float] = Field(None, ge=0)
    max_selling_price: Optional[float] = Field(None, ge=0)
    
    # Filtres de date
    expiry_before: Optional[date] = None
    expiry_after: Optional[date] = None
    created_before: Optional[datetime] = None
    created_after: Optional[datetime] = None
    
    # Statut
    is_active: Optional[bool] = None
    is_available: Optional[bool] = None
    prescription_required: Optional[bool] = None
    
    # Tri
    sort_by: str = Field(default="name", description="Champ de tri")
    sort_order: str = Field(default="asc", description="Ordre de tri (asc/desc)")
    
    # Pagination
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    
    @validator('sort_order')
    def validate_sort_order(cls, v):
        if v not in ['asc', 'desc']:
            raise ValueError("sort_order doit être 'asc' ou 'desc'")
        return v
    
    @model_validator(mode='after')
    def validate_price_range(self):
        if self.min_purchase_price is not None and self.max_purchase_price is not None:
            if self.min_purchase_price > self.max_purchase_price:
                raise ValueError('min_purchase_price ne peut pas être supérieur à max_purchase_price')
        
        if self.min_selling_price is not None and self.max_selling_price is not None:
            if self.min_selling_price > self.max_selling_price:
                raise ValueError('min_selling_price ne peut pas être supérieur à max_selling_price')
        
        return self
    
    @model_validator(mode='after')
    def validate_quantity_range(self):
        if self.min_quantity is not None and self.max_quantity is not None:
            if self.min_quantity > self.max_quantity:
                raise ValueError('min_quantity ne peut pas être supérieur à max_quantity')
        return self


# ========================
# STATISTICS SCHEMAS
# ========================
class ProductStatsResponse(BaseModel):
    """Statistiques des produits"""
    tenant_id: UUID
    
    # Totaux
    total_products: int = 0
    active_products: int = 0
    inactive_products: int = 0
    
    # Stock
    total_quantity: int = 0
    total_available_quantity: int = 0
    total_reserved_quantity: int = 0
    
    # Valeurs
    total_purchase_value: float = 0.0
    total_selling_value: float = 0.0
    total_margin_value: float = 0.0
    average_margin_rate: float = 0.0
    
    # Statuts
    out_of_stock_count: int = 0
    low_stock_count: int = 0
    over_stock_count: int = 0
    expired_count: int = 0
    expiring_soon_count: int = 0
    critical_expiry_count: int = 0
    
    # Distribution par catégorie
    by_category: Dict[str, int] = {}
    by_product_type: Dict[str, int] = {}
    by_stock_status: Dict[str, int] = {}
    by_expiry_status: Dict[str, int] = {}
    
    # Valeurs par catégorie
    value_by_category: Dict[str, float] = {}
    
    # Top produits
    top_selling_products: List[Dict[str, Any]] = []
    top_value_products: List[Dict[str, Any]] = []
    top_margin_products: List[Dict[str, Any]] = []
    
    # Alertes
    stock_alerts: List[Dict[str, Any]] = []
    expiry_alerts: List[Dict[str, Any]] = []
    
    class Config:
        from_attributes = True


class CategoryStats(BaseModel):
    """Statistiques par catégorie"""
    category: str
    product_count: int
    total_quantity: int
    purchase_value: float
    selling_value: float
    margin_value: float
    average_margin_rate: float


# ========================
# IMPORT/EXPORT SCHEMAS
# ========================
class ProductImportRequest(BaseModel):
    """Demande d'import de produits"""
    file_format: str = Field(default="csv", description="Format du fichier (csv, excel, json)")
    file_content: Optional[str] = Field(None, description="Contenu du fichier (base64)")
    file_url: Optional[str] = Field(None, description="URL du fichier")
    mapping: Dict[str, str] = Field(
        default_factory=lambda: {
            "name": "Nom",
            "code": "Code",
            "barcode": "Code-barres",
            "category": "Catégorie",
            "purchase_price": "Prix d'achat",
            "selling_price": "Prix de vente",
            "quantity": "Quantité"
        },
        description="Mapping des colonnes"
    )
    
    @validator('file_format')
    def validate_format(cls, v):
        if v not in ['csv', 'excel', 'json']:
            raise ValueError("Format doit être 'csv', 'excel' ou 'json'")
        return v


class ProductExportRequest(BaseModel):
    """Demande d'export de produits"""
    format: str = Field(default="csv", description="Format d'export")
    include_columns: List[str] = Field(
        default_factory=lambda: [
            "code", "name", "category", "quantity", 
            "purchase_price", "selling_price", "expiry_date"
        ],
        description="Colonnes à inclure"
    )
    filters: Optional[ProductSearch] = None
    compress: bool = Field(default=False, description="Compresser le fichier")
    
    @validator('format')
    def validate_format(cls, v):
        if v not in ['csv', 'excel', 'json', 'pdf']:
            raise ValueError("Format doit être 'csv', 'excel', 'json' ou 'pdf'")
        return v


# ========================
# BATCH OPERATION SCHEMAS
# ========================
class ProductBatchUpdate(BaseModel):
    """Mise à jour en lot de produits"""
    product_ids: List[UUID] = Field(..., min_items=1)
    update_data: ProductUpdate
    
    @validator('product_ids')
    def validate_product_ids(cls, v):
        if len(v) > 100:
            raise ValueError('Maximum 100 produits par opération')
        return v


class ProductMergeRequest(BaseModel):
    """Fusion de plusieurs produits en un seul"""
    source_product_ids: List[UUID] = Field(..., min_items=1)
    target_product_id: UUID
    merge_strategy: str = Field(
        default="average",
        pattern="^(average|max|min|first|sum)$",
        description="Stratégie de fusion des prix et quantités"
    )
    
    @validator('source_product_ids')
    def validate_source_ids(cls, v):
        if len(v) > 10:
            raise ValueError('Maximum 10 produits source par fusion')
        return v


# ========================
# ALERT SCHEMAS
# ========================
class ProductAlertResponse(BaseModel):
    """Alertes de produit"""
    stock_alerts: List[Dict[str, Any]] = []
    expiry_alerts: List[Dict[str, Any]] = []
    low_stock_alerts: List[Dict[str, Any]] = []
    critical_alerts: List[Dict[str, Any]] = []
    
    total_alerts: int = 0
    critical_count: int = 0
    warning_count: int = 0
    info_count: int = 0


# ========================
# MISC SCHEMAS
# ========================
class ProductDuplicateCheck(BaseModel):
    """Vérification de doublon de produit"""
    code: Optional[str] = None
    barcode: Optional[str] = None
    name: str
    laboratory: Optional[str] = None
    
    @model_validator(mode='after')
    def validate_identifiers(self):
        if not self.code and not self.barcode:
            raise ValueError('Au moins un identifiant (code ou barcode) est requis')
        return self


class ProductBarcodeResponse(BaseModel):
    """Réponse pour la génération de code-barres"""
    product_id: UUID
    barcode: str
    barcode_image_url: Optional[str] = None
    barcode_type: str = Field(default="CODE128")
    message: str = "Code-barres généré avec succès"
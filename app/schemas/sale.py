# app/schemas/sale.py
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict, computed_field
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, date
from uuid import UUID
from enum import Enum
from decimal import Decimal


# ============================
# ENUMS
# ============================
class SaleStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentMethod(str, Enum):
    CASH = "cash"
    MOBILE_MONEY = "mobile_money"
    CARD = "card"
    CHECK = "check"
    BANK_TRANSFER = "bank_transfer"
    CREDIT = "credit"


# ============================
# SALE ITEMS
# ============================
class SaleItemBase(BaseModel):
    product_id: UUID
    quantity: int = Field(..., gt=0, description="Quantité du produit")
    discount_percent: Decimal = Field(Decimal('0.00'), ge=0, le=100, max_digits=5, decimal_places=2, description="Pourcentage de remise")
    batch_number: Optional[str] = Field(None, max_length=100, description="Numéro de lot")
    expiry_date: Optional[date] = Field(None, description="Date de péremption")
    
    @field_validator('expiry_date')
    def validate_expiry_date(cls, v):
        if v and v < date.today():
            raise ValueError('La date de péremption ne peut pas être dans le passé')
        return v


class SaleItemCreate(SaleItemBase):
    """Schéma pour la création d'un item de vente.
    
    IMPORTANT:
    - Le prix de vente (unit_price) est automatiquement pris depuis le stock
    - Le taux de TVA (tva_rate) est automatiquement pris depuis le stock
    - Ces champs ne doivent pas être fournis dans la requête
    """
    # Ces champs sont dépréciés et ne doivent pas être utilisés
    unit_price: Optional[Decimal] = Field(
        None, 
        description="DÉPRÉCIÉ - Ignoré, utilise le prix du stock (product.selling_price)"
    )
    tva_rate: Optional[Decimal] = Field(
        None, 
        description="DÉPRÉCIÉ - Ignoré, utilise le taux du stock (product.tva_rate)"
    )
    
    @model_validator(mode='after')
    def validate_no_price_override(self):
        """Empêche la modification du prix de vente et de la TVA"""
        if self.unit_price is not None:
            raise ValueError(
                "Le prix de vente ne peut pas être modifié. "
                "Utilisez le prix défini dans le stock (product.selling_price)."
            )
        if self.tva_rate is not None:
            raise ValueError(
                "Le taux de TVA ne peut pas être modifié. "
                "Utilisez le taux défini dans le stock (product.tva_rate)."
            )
        return self
    
    model_config = ConfigDict(from_attributes=True)


class SaleItemResponse(BaseModel):
    id: UUID
    sale_id: UUID
    tenant_id: UUID
    pharmacy_id: UUID
    product_id: UUID
    product_code: str
    product_name: str
    quantity: int
    unit_price: Decimal
    discount_percent: Decimal
    discount_amount: Decimal
    tva_rate: Decimal
    tva_amount: Decimal
    subtotal: Decimal
    total: Decimal
    batch_number: Optional[str]
    expiry_date: Optional[date]
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ============================
# SALE CREATE
# ============================
class SaleCreate(BaseModel):
    pharmacy_id: Optional[UUID] = Field(None, description="ID de la pharmacie (optionnel, utilise la pharmacie par défaut)")
    customer_id: Optional[UUID] = None
    customer_name: Optional[str] = Field("Client Générique", max_length=100)
    customer_phone: Optional[str] = Field(None, max_length=20)
    payment_method: PaymentMethod
    reference_payment: Optional[str] = Field(None, max_length=100, description="Référence du paiement (numéro de chèque, transaction, etc.)")
    is_credit: bool = False
    credit_due_date: Optional[date] = None
    guarantee_deposit: Decimal = Field(Decimal('0.00'), ge=0, max_digits=15, decimal_places=2)
    guarantor_name: Optional[str] = Field(None, max_length=100)
    guarantor_phone: Optional[str] = Field(None, max_length=20)
    global_discount: Decimal = Field(Decimal('0.00'), ge=0, le=100, max_digits=5, decimal_places=2)
    notes: Optional[str] = None
    invoice_number: Optional[str] = Field(None, max_length=50)
    reference: Optional[str] = Field(None, max_length=100, description="Référence de la vente (optionnelle, générée automatiquement si non fournie)")
    items: List[SaleItemCreate]
    
    @computed_field
    @property
    def total_amount(self) -> Optional[Decimal]:
        """Montant total calculé (optionnel, sera recalculé par le backend)"""
        # Ce champ est optionnel, le backend recalcule toujours le total
        return None
    
    @model_validator(mode='after')
    def validate_credit_sale(self):
        if self.is_credit:
            if not self.credit_due_date:
                raise ValueError('credit_due_date est requis pour les ventes à crédit')
            if self.credit_due_date < date.today():
                raise ValueError('La date d\'échéance ne peut pas être dans le passé')
        return self
    
    @field_validator('items')
    def validate_items(cls, v):
        if not v or len(v) == 0:
            raise ValueError('La vente doit contenir au moins un article')
        return v


# ============================
# SALE UPDATE
# ============================
class SaleUpdate(BaseModel):
    status: Optional[SaleStatus] = None
    notes: Optional[str] = None
    cancel_reason: Optional[str] = None
    refund_amount: Optional[Decimal] = Field(None, ge=0, max_digits=15, decimal_places=2)


# ============================
# SALE FILTER
# ============================
class SaleFilter(BaseModel):
    pharmacy_id: Optional[UUID] = Field(None, description="Filtrer par pharmacie spécifique")
    branch_id: Optional[UUID] = Field(None, description="Filtrer par branche spécifique")
    status: Optional[SaleStatus] = None
    payment_method: Optional[PaymentMethod] = None
    is_credit: Optional[bool] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    customer_id: Optional[UUID] = None
    seller_id: Optional[UUID] = None
    search: Optional[str] = None
    
    @model_validator(mode='after')
    def validate_date_range(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError('start_date ne peut pas être après end_date')
        return self


# ============================
# DAILY STATS RESPONSE
# ============================
class TopProductStats(BaseModel):
    """Statistiques d'un produit en top"""
    product: str
    quantity: int
    amount: float


class PharmacyDailyStats(BaseModel):
    """Statistiques par pharmacie pour une journée"""
    pharmacy_id: str
    pharmacy_name: str
    sales_count: int
    total_amount: float
    percentage: float


class DailyStatsResponse(BaseModel):
    """Réponse pour les statistiques quotidiennes"""
    date: str
    sales_count: int
    total_amount: float
    average_basket: float
    items_sold: int
    top_products: List[TopProductStats]
    by_pharmacy: List[PharmacyDailyStats]


# ============================
# SALE RESPONSE / IN DB
# ============================
class SaleInDB(BaseModel):
    id: UUID
    tenant_id: UUID
    pharmacy_id: UUID
    pharmacy_name: Optional[str] = None
    pharmacy_code: Optional[str] = None
    branch_id: Optional[UUID] = None
    branch_name: Optional[str] = None
    reference: str
    customer_id: Optional[UUID]
    customer_name: str
    customer_phone: Optional[str]
    created_by: UUID
    seller_name: str
    payment_method: str
    reference_payment: Optional[str]
    payment_date: Optional[datetime]
    is_credit: bool
    credit_due_date: Optional[date]
    guarantee_deposit: Decimal
    guarantor_name: Optional[str]
    guarantor_phone: Optional[str]
    global_discount: Decimal
    notes: Optional[str]
    subtotal: Decimal
    total_discount: Decimal
    total_tva: Decimal
    total_amount: Decimal
    status: str
    invoice_number: Optional[str]
    invoice_path: Optional[str]
    receipt_path: Optional[str]
    created_at: datetime
    updated_at: datetime
    validated_at: Optional[datetime]
    validated_by: Optional[UUID]
    cancelled_at: Optional[datetime]
    cancelled_by: Optional[UUID]
    cancel_reason: Optional[str]
    items: Optional[List[SaleItemResponse]] = Field(default=None, description="Articles de la vente")
    
    @computed_field
    @property
    def amount_paid(self) -> float:
        """Montant total payé"""
        # Pour les ventes au comptant (non crédit), le montant total est payé
        if not self.is_credit:
            return float(self.total_amount)
        # Pour les crédits, calculer depuis les paiements si disponibles
        # Sinon, retourner le dépôt de garantie
        if self.guarantee_deposit:
            return float(self.guarantee_deposit)
        return 0.0
    
    @computed_field
    @property
    def amount_due(self) -> float:
        """Montant restant à payer"""
        total = float(self.total_amount)
        paid = self.amount_paid
        return max(0.0, total - paid)
    
    @computed_field
    @property
    def is_paid(self) -> bool:
        """Vérifie si la vente est entièrement payée"""
        return self.amount_due <= 0.01
    
    @computed_field
    @property
    def credit_status(self) -> str:
        """Statut du crédit"""
        if not self.is_credit:
            return "not_credit"
        if self.is_paid:
            return "paid"
        if self.credit_due_date and date.today() > self.credit_due_date:
            return "overdue"
        return "pending"
    
    @computed_field
    @property
    def days_overdue(self) -> int:
        """Nombre de jours de retard (si crédit)"""
        if not self.is_credit or not self.credit_due_date or self.is_paid:
            return 0
        today = date.today()
        if today > self.credit_due_date:
            return (today - self.credit_due_date).days
        return 0
    
    model_config = ConfigDict(from_attributes=True)


class SaleResponse(BaseModel):
    message: str
    sale: SaleInDB
    pharmacy: Optional[Dict[str, Any]] = None
    alerts: Optional[List[Dict[str, Any]]] = None
    receipt_available: bool = False
    receipt_url: Optional[str] = None
    generated_invoice_number: Optional[str] = None


# ============================
# SALE LIST RESPONSE
# ============================
class SaleListResponse(BaseModel):
    items: List[SaleInDB]
    total: int
    page: int
    size: int
    has_more: bool
    page_size: int
    pharmacies_summary: Optional[Dict[str, Any]] = None


# ============================
# SALE DETAIL RESPONSE
# ============================
class SaleDetailResponse(BaseModel):
    """Réponse détaillée pour une vente spécifique"""
    id: UUID
    tenant_id: UUID
    pharmacy_id: UUID
    pharmacy_name: Optional[str] = None
    branch_id: Optional[UUID] = None
    branch_name: Optional[str] = None
    reference: str
    customer_id: Optional[UUID]
    customer_name: str
    customer_phone: Optional[str]
    created_by: UUID
    seller_name: str
    created_at: datetime
    updated_at: datetime
    payment_method: str
    reference_payment: Optional[str]
    payment_date: Optional[datetime]
    is_credit: bool
    credit_due_date: Optional[date]
    guarantee_deposit: float
    guarantor_name: Optional[str]
    guarantor_phone: Optional[str]
    global_discount: float
    notes: Optional[str]
    subtotal: float
    total_discount: float
    total_tva: float
    total_amount: float
    status: str
    validated_by: Optional[UUID]
    validated_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    cancelled_by: Optional[UUID]
    cancel_reason: Optional[str]
    invoice_number: Optional[str]
    receipt_path: Optional[str]
    invoice_path: Optional[str]
    items: List[SaleItemResponse]
    
    @computed_field
    @property
    def amount_paid(self) -> float:
        """Montant total payé"""
        if not self.is_credit:
            return float(self.total_amount)
        if self.guarantee_deposit:
            return float(self.guarantee_deposit)
        return 0.0
    
    @computed_field
    @property
    def amount_due(self) -> float:
        """Montant restant à payer"""
        total = float(self.total_amount)
        paid = self.amount_paid
        return max(0.0, total - paid)
    
    @computed_field
    @property
    def is_paid(self) -> bool:
        """Vérifie si la vente est entièrement payée"""
        return self.amount_due <= 0.01
    
    @computed_field
    @property
    def can_refund(self) -> bool:
        """Vérifie si la vente peut être remboursée"""
        return self.status in ["completed", "pending"] and self.total_amount > 0
    
    @computed_field
    @property
    def can_cancel(self) -> bool:
        """Vérifie si la vente peut être annulée"""
        return self.status in ["pending", "completed"] and not self.is_credit
    
    @computed_field
    @property
    def can_validate(self) -> bool:
        """Vérifie si la vente peut être validée"""
        return self.status == "pending"
    
    model_config = ConfigDict(from_attributes=True)


# ============================
# SALE STATISTICS
# ============================
class DailyStats(BaseModel):
    date: date
    sales_count: int
    total_amount: float
    average_basket: float
    items_sold: int
    top_products: List[Dict[str, Any]]
    by_payment_method: Dict[str, float]


class PharmacyStats(BaseModel):
    pharmacy_id: UUID
    pharmacy_name: str
    pharmacy_code: str
    is_main: bool
    total_sales: int
    total_amount: float
    average_basket: float
    items_sold: int
    percentage_of_total: float


class PeriodStats(BaseModel):
    """Statistiques pour une période"""
    total: float
    count: int
    average: float


class SalesStatsResponse(BaseModel):
    """Réponse pour les statistiques globales des ventes"""
    today: DailyStatsResponse
    week: PeriodStats
    month: PeriodStats
    year: PeriodStats


# ============================
# PERIOD STATS RESPONSE
# ============================
class PeriodDataPoint(BaseModel):
    """Point de données pour les statistiques par période"""
    date: Optional[str] = None
    sales_count: int
    total_amount: float
    average_basket: float


class PeriodStatsResponse(BaseModel):
    """Réponse pour les statistiques par période"""
    period: str
    start_date: str
    end_date: str
    data: List[PeriodDataPoint]


# ============================
# QUICK SALE
# ============================
class QuickSaleItem(BaseModel):
    product_id: UUID
    quantity: int = Field(..., gt=0)
    
    model_config = ConfigDict(from_attributes=True)


class QuickSaleRequest(BaseModel):
    items: List[QuickSaleItem]
    payment_method: PaymentMethod
    client_name: Optional[str] = "Client Générique"
    pharmacy_id: Optional[UUID] = None
    
    @field_validator('items')
    def validate_items(cls, v):
        if not v or len(v) == 0:
            raise ValueError('La vente rapide doit contenir au moins un article')
        return v


# ============================
# CREDIT SALE
# ============================
class CreditSaleCreate(SaleCreate):
    is_credit: bool = True
    credit_due_date: date
    guarantee_deposit: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)
    guarantor_name: str = Field(..., max_length=100)
    guarantor_phone: str = Field(..., max_length=20)
    
    @field_validator('credit_due_date')
    def validate_credit_due_date(cls, v):
        if v < date.today():
            raise ValueError('La date d\'échéance doit être dans le futur')
        return v


# ============================
# REFUND
# ============================
class RefundItem(BaseModel):
    sale_item_id: UUID
    quantity: int = Field(..., gt=0)
    reason: str


class SaleRefundRequest(BaseModel):
    sale_id: UUID
    items: List[RefundItem]
    refund_amount: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)
    refund_reason: str
    refund_method: PaymentMethod
    
    @field_validator('items')
    def validate_items(cls, v):
        if not v or len(v) == 0:
            raise ValueError('Le remboursement doit concerner au moins un article')
        return v


# ============================
# RECEIPT DATA
# ============================
class ReceiptData(BaseModel):
    sale_id: UUID
    include_logo: bool = True
    include_qrcode: bool = True
    include_pharmacy_info: bool = True
    include_tax_details: bool = True
    additional_notes: Optional[str] = None
    language: str = "fr"


# ============================
# PHARMACY CONTEXT
# ============================
class PharmacyContext(BaseModel):
    id: UUID
    name: str
    code: str
    address: str
    phone: str
    is_main: bool
    is_active: bool


class UserPharmacyAccess(BaseModel):
    accessible_pharmacies: List[PharmacyContext]
    current_pharmacy: Optional[PharmacyContext] = None
    can_switch: bool


# ============================
# VALIDATION
# ============================
class SaleValidationRequest(BaseModel):
    sale_id: UUID
    validator_notes: Optional[str] = None
    force_approval: bool = False


# ============================
# EXPORT
# ============================
class SaleExportRequest(BaseModel):
    start_date: date
    end_date: date
    pharmacy_id: Optional[UUID] = None
    branch_id: Optional[UUID] = None
    format: str = Field("xlsx", pattern="^(xlsx|csv|pdf)$")
    include_details: bool = False
    
    @model_validator(mode='after')
    def validate_date_range(self):
        if self.start_date > self.end_date:
            raise ValueError('start_date ne peut pas être après end_date')
        if (self.end_date - self.start_date).days > 365:
            raise ValueError('La période ne peut pas dépasser 365 jours')
        return self


class SaleExportResponse(BaseModel):
    filename: str
    download_url: str
    record_count: int
    file_size: str
    generated_at: datetime


# ============================
# SALE IMPACT RESPONSE
# ============================
class SaleImpactResponse(BaseModel):
    """Réponse pour l'impact des ventes sur le stock"""
    product_id: UUID
    product_code: str
    product_name: str
    unit: str
    total_sold: int
    total_revenue: float
    sale_count: int
    average_price: float
    stock_impact: int  # Impact négatif sur le stock
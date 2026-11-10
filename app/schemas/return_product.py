# Schémas Pydantic pour les retours
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from decimal import Decimal
from uuid import UUID
from enum import Enum

class ReturnStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROCESSED = "processed"
    CANCELLED = "cancelled"

class ReturnType(str, Enum):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    INTERNAL = "internal"
    DAMAGE = "damage"
    EXPIRY = "expiry"

class ReturnReason(str, Enum):
    EXPIRED = "expired"
    DAMAGED = "damaged"
    DEFECTIVE = "defective"
    WRONG_PRODUCT = "wrong_product"
    WRONG_QUANTITY = "wrong_quantity"
    CUSTOMER_RETURN = "customer_return"
    QUALITY_ISSUE = "quality_issue"
    RECALL = "recall"
    OTHER = "other"

class ReturnItemCreate(BaseModel):
    product_id: UUID
    sale_item_id: Optional[UUID] = None
    quantity: int = Field(gt=0, description="Quantité à retourner")
    batch_number: Optional[str] = None
    expiry_date: Optional[datetime] = None
    discount_percent: Optional[Decimal] = Decimal("0")
    reason: Optional[ReturnReason] = None
    reason_description: Optional[str] = None
    condition: Optional[str] = Field(None, description="État du produit: new, opened, used, damaged")
    condition_notes: Optional[str] = None
    meta_data: Optional[Dict] = {}

class ReturnCreate(BaseModel):
    return_type: ReturnType
    reason: ReturnReason
    sale_id: Optional[UUID] = None
    purchase_id: Optional[UUID] = None
    invoice_number: Optional[str] = None
    customer_id: Optional[UUID] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    supplier_id: Optional[UUID] = None
    supplier_name: Optional[str] = None
    branch_id: Optional[UUID] = None
    items: List[ReturnItemCreate] = Field(..., min_items=1)
    return_date: Optional[datetime] = None
    restocking_fee_percent: Optional[Decimal] = Field(None, ge=0, le=100)
    reference: Optional[str] = None
    notes: Optional[str] = None
    meta_data: Optional[Dict] = {}

class ReturnUpdate(BaseModel):
    status: Optional[ReturnStatus] = None
    notes: Optional[str] = None
    internal_notes: Optional[str] = None
    restocking_fee_percent: Optional[Decimal] = Field(None, ge=0, le=100)

class ReturnApprovalRequest(BaseModel):
    """Requête d'approbation de retour"""
    notes: Optional[str] = Field(None, description="Notes d'approbation")
    restocking_fee_percent: Optional[float] = Field(None, ge=0, le=100, description="Frais de restockage (%)")

class ReturnProcessRequest(BaseModel):
    """Requête de traitement de retour"""
    restore_stock: bool = Field(True, description="Restaurer le stock")
    refund_amount: Optional[float] = Field(None, ge=0, description="Montant à rembourser")
    refund_method: Optional[str] = Field(None, description="Méthode de remboursement: cash, credit, bank_transfer")
    generate_credit_note: bool = Field(False, description="Générer une note de crédit")

class RefundRequest(BaseModel):
    """Requête de remboursement"""
    return_id: UUID = Field(..., description="ID du retour")
    refund_amount: Decimal = Field(..., gt=0, description="Montant à rembourser")
    refund_method: str = Field(..., description="Méthode de remboursement: cash, card, bank_transfer, credit")
    refund_reason: Optional[str] = Field(None, description="Raison du remboursement")
    notes: Optional[str] = None
    send_notification: bool = Field(True, description="Envoyer une notification au client")

class ExchangeRequest(BaseModel):
    """Requête d'échange de produit"""
    sale_id: UUID = Field(..., description="ID de la vente originale")
    customer_id: UUID = Field(..., description="ID du client")
    returned_product_id: UUID = Field(..., description="ID du produit retourné")
    returned_product_name: str = Field(..., description="Nom du produit retourné")
    returned_quantity: int = Field(..., gt=0, description="Quantité retournée")
    returned_condition: str = Field(..., description="État du produit retourné")
    return_reason: Optional[str] = Field(None, description="Raison du retour")
    sale_item_id: UUID = Field(..., description="ID de l'item de vente original")
    new_product_id: UUID = Field(..., description="ID du nouveau produit")
    new_product_name: str = Field(..., description="Nom du nouveau produit")
    new_quantity: int = Field(..., gt=0, description="Quantité du nouveau produit")
    exchange_discount: Optional[float] = Field(None, ge=0, le=100, description="Remise sur l'échange")
    payment_method: str = Field(..., description="Méthode de paiement pour la différence")

class BulkReturnRequest(BaseModel):
    """Requête de création en masse de retours"""
    returns: List[ReturnCreate] = Field(..., min_items=1, description="Liste des retours à créer")

class ReturnFilterParams(BaseModel):
    """Paramètres de filtrage des retours"""
    status: Optional[str] = None
    return_type: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    search: Optional[str] = None
    customer_id: Optional[UUID] = None
    sale_id: Optional[UUID] = None
    period: Optional[str] = Field(None, description="Période: today, yesterday, this_week, this_month")

class ReturnItemResponse(BaseModel):
    """Réponse pour un item de retour"""
    id: UUID
    product_id: UUID
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    quantity: int
    unit_price: Decimal
    subtotal: Decimal
    tva_rate: Decimal
    tva_amount: Decimal
    total: Decimal
    reason: Optional[ReturnReason] = None
    reason_description: Optional[str] = None
    condition: Optional[str] = None
    condition_notes: Optional[str] = None
    quantity_restored: int

class ReturnResponse(BaseModel):
    """Réponse pour un retour"""
    message: str = ""
    return_obj: Any
    items: Optional[List[Any]] = None
    requires_approval: bool = False
    exchange_sale_id: Optional[UUID] = None

class ReturnListResponse(BaseModel):
    """Réponse pour la liste des retours"""
    total: int
    page: int
    page_size: int
    data: List[Dict]
    filters_applied: Optional[Dict] = None
    bulk_errors: Optional[List[Dict]] = None

class ReturnSearchResponse(BaseModel):
    """Réponse pour la recherche de retours"""
    query: str
    total: int
    results: List[Dict]

class ReturnStatsResponse(BaseModel):
    """Réponse pour les statistiques des retours"""
    period: str
    start_date: str
    end_date: str
    total_returns: int
    pending_count: int
    approved_count: int
    rejected_count: int
    processed_count: int
    total_refund_amount: float
    total_restocking_fees: float
    customer_returns: int
    supplier_returns: int
    internal_returns: int
    top_returned_products: List[Dict]

# Classes additionnelles pour les opérations spécifiques

class ReturnCancelRequest(BaseModel):
    """Requête d'annulation de retour"""
    reason: Optional[str] = Field(None, description="Raison de l'annulation")
    notes: Optional[str] = None

class ReturnSearchAdvancedParams(BaseModel):
    """Paramètres de recherche avancée"""
    return_number: Optional[str] = None
    invoice_number: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    supplier_name: Optional[str] = None
    product_name: Optional[str] = None
    product_barcode: Optional[str] = None
    status: Optional[ReturnStatus] = None
    return_type: Optional[ReturnType] = None
    reason: Optional[ReturnReason] = None
    min_amount: Optional[Decimal] = Field(None, ge=0)
    max_amount: Optional[Decimal] = Field(None, ge=0)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    created_by: Optional[UUID] = None
    approved_by: Optional[UUID] = None
    stock_restored: Optional[bool] = None

class ReturnBatchUpdate(BaseModel):
    """Mise à jour en lot de retours"""
    return_ids: List[UUID] = Field(..., min_items=1)
    status: Optional[ReturnStatus] = None
    notes: Optional[str] = None
    restocking_fee_percent: Optional[float] = Field(None, ge=0, le=100)

class CreditNoteGenerateRequest(BaseModel):
    """Requête de génération de note de crédit"""
    return_id: UUID
    include_details: bool = Field(True, description="Inclure les détails des items")
    language: str = Field("fr", description="Langue du document")

class ReturnExportRequest(BaseModel):
    """Requête d'export des retours"""
    format: str = Field("excel", description="Format d'export: excel, csv, pdf")
    filters: Optional[ReturnFilterParams] = None
    include_items: bool = Field(True, description="Inclure les items")
    date_range_start: Optional[date] = None
    date_range_end: Optional[date] = None

class ReturnValidationResult(BaseModel):
    """Résultat de validation d'un retour"""
    is_valid: bool
    errors: List[str] = []
    warnings: List[str] = []
    suggested_refund_amount: Optional[Decimal] = None
    available_stock_for_exchange: Optional[Dict[UUID, int]] = None

class ReturnNotificationPayload(BaseModel):
    """Payload pour les notifications de retour"""
    return_id: UUID
    return_number: str
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    notification_type: str = Field(..., description="created, approved, rejected, processed, refunded")
    send_email: bool = True
    send_sms: bool = False
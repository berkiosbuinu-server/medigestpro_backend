"""
app/schemas/report.py
Schémas Pydantic pour les rapports
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import date, datetime
from enum import Enum


class ReportType(str, Enum):
    """Types de rapports disponibles"""
    SALES = "sales"
    INVENTORY = "inventory"
    FINANCIAL = "financial"
    CLIENTS = "clients"
    STOCK_MOVEMENTS = "stock_movements"
    PURCHASES = "purchases"
    TAX = "tax"


class ExportFormat(str, Enum):
    """Formats d'export disponibles"""
    EXCEL = "excel"
    PDF = "pdf"
    CSV = "csv"
    JSON = "json"


class GroupByType(str, Enum):
    """Types de regroupement pour les rapports"""
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    PRODUCT = "product"
    CATEGORY = "category"
    SELLER = "seller"
    CLIENT = "client"


class InventoryReportType(str, Enum):
    """Types de rapports d'inventaire"""
    SUMMARY = "summary"
    DETAILED = "detailed"
    VALUATION = "valuation"
    EXPIRY = "expiry"


class SalesReportRequest(BaseModel):
    """Requête pour un rapport de ventes"""
    start_date: date = Field(..., description="Date de début")
    end_date: date = Field(..., description="Date de fin")
    group_by: GroupByType = Field(GroupByType.DAY, description="Type de regroupement")
    force_refresh: bool = Field(False, description="Forcer le recalcul")
    update_stats: bool = Field(True, description="Mettre à jour les statistiques")
    
    @validator("end_date")
    def validate_dates(cls, v, values):
        if "start_date" in values and v < values["start_date"]:
            raise ValueError("La date de fin doit être postérieure à la date de début")
        return v


class InventoryReportRequest(BaseModel):
    """Requête pour un rapport d'inventaire"""
    report_type: InventoryReportType = Field(
        InventoryReportType.SUMMARY, 
        description="Type de rapport d'inventaire"
    )
    include_zero_stock: bool = Field(
        False, 
        description="Inclure les produits en rupture de stock"
    )


class FinancialReportRequest(BaseModel):
    """Requête pour un rapport financier"""
    start_date: date = Field(..., description="Date de début")
    end_date: date = Field(..., description="Date de fin")
    
    @validator("end_date")
    def validate_dates(cls, v, values):
        if "start_date" in values and v < values["start_date"]:
            raise ValueError("La date de fin doit être postérieure à la date de début")
        return v


class ClientReportRequest(BaseModel):
    """Requête pour un rapport clients"""
    include_inactive: bool = Field(
        False, 
        description="Inclure les clients inactifs"
    )
    min_purchases: Optional[int] = Field(
        None, 
        ge=0, 
        description="Nombre minimum d'achats"
    )
    min_amount: Optional[float] = Field(
        None, 
        ge=0, 
        description="Montant minimum d'achats"
    )


class ReportResponse(BaseModel):
    """Réponse standard pour un rapport"""
    type: ReportType
    period_start: date
    period_end: date
    generated_at: datetime
    data: Dict[str, Any]
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            date: lambda v: v.isoformat()
        }


class ReportMetadata(BaseModel):
    """Métadonnées d'un rapport"""
    report_id: str
    type: ReportType
    period_start: date
    period_end: date
    generated_at: datetime
    generated_by: str
    size_bytes: int
    format: ExportFormat
    download_url: Optional[str]
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            date: lambda v: v.isoformat()
        }


class ReportSummary(BaseModel):
    """Résumé d'un rapport"""
    report_id: str
    type: ReportType
    period_start: date
    period_end: date
    record_count: int
    total_amount: Optional[float]
    status: str  # generated, pending, failed
    generated_at: Optional[datetime]
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None,
            date: lambda v: v.isoformat()
        }

class CustomerReportRequest(BaseModel):
    """Requête pour rapport clients"""
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    include_inactive: bool = False
    customer_type: Optional[str] = None
    category: Optional[str] = None
    min_purchase_amount: Optional[float] = None
    max_debt_ratio: Optional[float] = None
    group_by: Optional[str] = "category"
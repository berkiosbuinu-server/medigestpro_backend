# app/schemas/inventory.py
from pydantic import BaseModel, Field, validator, root_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from uuid import UUID
from enum import Enum

class InventoryType(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    SPOT = "spot"
    CYCLE = "cycle"

class InventoryStatus(str, Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    COUNTING = "counting"
    VALIDATION = "validation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class ScheduleType(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    CYCLE = "cycle"

class InventoryBase(BaseModel):
    """Schéma de base pour un inventaire physique"""
    inventory_type: InventoryType
    description: Optional[str] = None
    planned_date: Optional[date] = None
    tags: List[str] = Field(default_factory=list)

class InventoryCreate(InventoryBase):
    """Création d'un inventaire"""
    product_ids: Optional[List[UUID]] = Field(None, description="Liste des produits pour inventaire partiel")
    
    @validator('planned_date')
    def validate_planned_date(cls, v):
        if v and v < date.today():
            raise ValueError('La date planifiée ne peut pas être dans le passé')
        return v

class InventoryUpdate(BaseModel):
    """Mise à jour d'un inventaire"""
    description: Optional[str] = None
    status: Optional[InventoryStatus] = None
    notes: Optional[str] = None

class InventoryInDB(InventoryBase):
    """Inventaire tel que stocké en base"""
    id: UUID
    tenant_id: UUID
    inventory_number: str
    
    # Dates
    start_date: datetime
    end_date: Optional[datetime] = None
    
    # Statut
    status: InventoryStatus
    
    # Responsables
    created_by: UUID
    counted_by: Optional[UUID] = None
    validated_by: Optional[UUID] = None
    
    # Résultats
    total_items: int = 0
    items_counted: int = 0
    items_missing: int = 0
    items_excess: int = 0
    
    # Valeurs
    system_value: float = 0.0
    counted_value: float = 0.0
    variance_value: float = 0.0
    variance_percentage: float = 0.0
    
    # Métadonnées
    notes: Optional[str] = None
    
    # Timestamps
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class InventoryItemCreate(BaseModel):
    """Création d'un item d'inventaire"""
    product_id: UUID
    counted_quantity: int = Field(..., ge=0)
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None
    location: Optional[str] = None
    notes: Optional[str] = None

class InventoryItemUpdate(BaseModel):
    """Mise à jour d'un item d'inventaire"""
    counted_quantity: Optional[int] = Field(None, ge=0)
    status: Optional[str] = None
    notes: Optional[str] = None

class InventoryItemInDB(BaseModel):
    """Item d'inventaire tel que stocké en base"""
    id: UUID
    product_id: UUID
    product_name: str
    product_code: str
    
    # Quantités
    expected_quantity: int
    counted_quantity: Optional[int] = None
    variance: int = 0
    
    # Valeurs
    expected_value: float
    counted_value: Optional[float] = None
    variance_value: float = 0.0
    
    # Statut
    status: str
    
    # Métadonnées
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    
    class Config:
        from_attributes = True

class InventoryReport(BaseModel):
    """Rapport d'inventaire"""
    inventory: InventoryInDB
    items: List[InventoryItemInDB]
    summary: Dict[str, Any]
    recommendations: List[str]

class ScheduleCreate(BaseModel):
    """Création d'un planning d'inventaire"""
    schedule_type: ScheduleType
    frequency: int = Field(1, ge=1, le=365)
    description: Optional[str] = None
    
    # Paramètres spécifiques
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    day_of_month: Optional[int] = Field(None, ge=1, le=31)
    month_of_year: Optional[int] = Field(None, ge=1, le=12)
    
    # Cycle counting
    cycle_count: Optional[int] = Field(None, ge=1, le=1000)
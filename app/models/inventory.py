# app/models/inventory.py
import uuid
from datetime import datetime, date
from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, ForeignKey, Text, Date, Enum, Index
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from decimal import Decimal

from app.db.base import Base

class PhysicalInventory(Base):
    __tablename__ = "physical_inventories"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    
    # Information de l'inventaire
    inventory_type = Column(
        String(20),
        nullable=False,
        comment="complete, partial, spot, cycle"
    )
    inventory_number = Column(String(50), unique=True, nullable=False, index=True)
    
    # Période
    start_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    end_date = Column(DateTime, nullable=True)
    planned_date = Column(Date, nullable=True)
    
    # Statut
    status = Column(
        String(20),
        default="draft",
        comment="draft, in_progress, counting, validation, completed, cancelled"
    )
    
    # Responsables
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    counted_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    validated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # Résultats
    total_items = Column(Integer, default=0)
    items_counted = Column(Integer, default=0)
    items_missing = Column(Integer, default=0)
    items_excess = Column(Integer, default=0)
    
    # Valeurs
    system_value = Column(Float, default=0.0)
    counted_value = Column(Float, default=0.0)
    variance_value = Column(Float, default=0.0)
    variance_percentage = Column(Float, default=0.0)
    
    # Métadonnées
    description = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    tags = Column(JSON, default=list)
    
    # Relations
    tenant = relationship("Tenant", backref="inventories")
    creator = relationship("User", foreign_keys=[created_by])
    counter = relationship("User", foreign_keys=[counted_by])
    validator = relationship("User", foreign_keys=[validated_by])
    items = relationship("InventoryItem", back_populates="inventory", cascade="all, delete-orphan")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # CORRIGÉ: Syntaxe correcte pour les indexes
    __table_args__ = (
        Index('ix_inventories_tenant_status', 'tenant_id', 'status'),
        Index('ix_inventories_tenant_date', 'tenant_id', 'start_date'),
    )
    
    def generate_inventory_number(self):
        """Génère un numéro d'inventaire unique"""
        from datetime import datetime
        date_str = datetime.utcnow().strftime('%Y%m%d')
        return f"INV-{date_str}-{str(self.id)[:8].upper()}"
    
    def calculate_variance(self):
        """Calcule les écarts après comptage"""
        if not self.items:
            return
        
        total_items = 0
        items_counted = 0
        items_missing = 0
        items_excess = 0
        system_value = Decimal('0.0')
        counted_value = Decimal('0.0')
        
        for item in self.items:
            total_items += 1
            
            if item.counted_quantity is not None:
                items_counted += 1
                
                system_value += item.expected_quantity * Decimal(str(item.product.purchase_price))
                counted_value += item.counted_quantity * Decimal(str(item.product.purchase_price))
                
                if item.counted_quantity < item.expected_quantity:
                    items_missing += 1
                elif item.counted_quantity > item.expected_quantity:
                    items_excess += 1
        
        self.total_items = total_items
        self.items_counted = items_counted
        self.items_missing = items_missing
        self.items_excess = items_excess
        self.system_value = float(system_value)
        self.counted_value = float(counted_value)
        self.variance_value = float(counted_value - system_value)
        
        if system_value > 0:
            self.variance_percentage = (self.variance_value / self.system_value) * 100

class InventoryItem(Base):
    __tablename__ = "inventory_items"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    inventory_id = Column(UUID(as_uuid=True), ForeignKey("physical_inventories.id"), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    
    # Quantités
    expected_quantity = Column(Integer, nullable=False)
    counted_quantity = Column(Integer, nullable=True)
    variance = Column(Integer, default=0)
    
    # Valeurs
    expected_value = Column(Float, nullable=False)
    counted_value = Column(Float, nullable=True)
    variance_value = Column(Float, default=0.0)
    
    # Statut
    status = Column(
        String(20),
        default="pending",
        comment="pending, counted, validated, adjusted"
    )
    
    # Métadonnées
    notes = Column(Text, nullable=True)
    batch_number = Column(String(100), nullable=True)
    expiry_date = Column(Date, nullable=True)
    location = Column(String(100), nullable=True)
    
    # Relation
    tenant = relationship("Tenant")
    inventory = relationship("PhysicalInventory", back_populates="items")
    product = relationship("Product")
    
    # Timestamps
    counted_at = Column(DateTime, nullable=True)
    validated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # CORRIGÉ: Syntaxe correcte pour les indexes
    __table_args__ = (
        Index('ix_inventory_items_product', 'tenant_id', 'product_id'),
        Index('ix_inventory_items_inventory', 'inventory_id', 'status'),
    )
    
    def calculate_variance(self):
        """Calcule l'écart pour cet item"""
        if self.counted_quantity is not None:
            self.variance = self.counted_quantity - self.expected_quantity
            
            if self.product:
                expected_val = self.expected_quantity * self.product.purchase_price
                counted_val = self.counted_quantity * self.product.purchase_price
                self.expected_value = expected_val
                self.counted_value = counted_val
                self.variance_value = counted_val - expected_val

class InventorySchedule(Base):
    __tablename__ = "inventory_schedules"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    
    # Planification
    schedule_type = Column(
        String(20),
        nullable=False,
        comment="daily, weekly, monthly, quarterly, yearly, cycle"
    )
    frequency = Column(Integer, nullable=False, default=1)
    
    # Détails
    day_of_week = Column(Integer, nullable=True, comment="0-6 (Lundi-Dimanche)")
    day_of_month = Column(Integer, nullable=True)
    month_of_year = Column(Integer, nullable=True)
    
    # Périodicité par cycle
    cycle_count = Column(Integer, default=0)
    current_cycle = Column(Integer, default=0)
    
    # Statut
    is_active = Column(Boolean, default=True)
    last_executed = Column(DateTime, nullable=True)
    next_schedule = Column(Date, nullable=False)
    
    # Métadonnées
    description = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    
    # Relations
    tenant = relationship("Tenant", backref="inventory_schedules")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
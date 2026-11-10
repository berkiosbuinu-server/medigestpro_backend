# app/models/inventory_alert.py
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Text
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base
import uuid

class InventoryAlert(Base):
    __tablename__ = "inventory_alerts"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # CORRECTION : Utiliser UUID pour les clés étrangères
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    
    alert_type = Column(String(50), nullable=False)  # low_stock, expiring, expired
    severity = Column(String(20), default="medium")  # low, medium, high
    message = Column(Text)
    is_resolved = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    
    @property
    def severity_priority(self) -> int:
        """Retourne la priorité numérique pour le tri"""
        priorities = {"high": 3, "medium": 2, "low": 1}
        return priorities.get(self.severity, 0)
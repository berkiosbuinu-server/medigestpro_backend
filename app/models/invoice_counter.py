from sqlalchemy import Column, Integer, Date, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from app.db.base import Base
from sqlalchemy.schema import UniqueConstraint

class InvoiceCounter(Base):
    __tablename__ = "invoice_counter"
    
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    branch_id = Column(UUID(as_uuid=True), nullable=False)
    date = Column(Date, nullable=False)
    current_number = Column(Integer, default=1, nullable=False)
    last_invoice_date = Column(Date, nullable=True)
    created_at = Column(Date, server_default=func.now())
    updated_at = Column(Date, onupdate=func.now())
    
    # Pour la compatibilité avec les requêtes
    __table_args__ = (
        UniqueConstraint('tenant_id', 'pharmacy_id', 'date', name='unique_tenant_pharmacy_date'),
    )
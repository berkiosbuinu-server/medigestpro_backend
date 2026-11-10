# app/models/refund.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime, ForeignKey, Text, Enum
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship

from app.db.base import Base

class Refund(Base):
    __tablename__ = "refunds"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    # Vente remboursée
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=False)

    # Référence unique
    reference = Column(String(50), unique=True, nullable=False)

    # Montant et méthode de remboursement
    amount = Column(Float, nullable=False)
    refund_method = Column(
        String(20),
        nullable=False,
        comment="cash, mobile_money, bank_transfer, store_credit"
    )

    # Détails
    reason = Column(Text, nullable=False)
    
    # CHANGÉ: Renommé 'refund_details' et 'metadata'
    refund_details = Column(JSON, default=dict)
    refund_data = Column(JSON, default=dict)  # Anciennement 'metadata'

    # Statut
    status = Column(
        String(20),
        default="pending",
        comment="pending, approved, processed, completed, rejected"
    )

    # Traçabilité
    requested_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    requestor_name = Column(String(100), nullable=True)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approver_name = Column(String(100), nullable=True)
    processed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    processor_name = Column(String(100), nullable=True)

    # Métadonnées
    notes = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)

    # Relations
    tenant = relationship("Tenant", back_populates="refunds")
    sale = relationship("Sale", back_populates="refunds")
    requestor = relationship("User", foreign_keys=[requested_by], backref="requested_refunds")
    approver = relationship("User", foreign_keys=[approved_by], backref="approved_refunds")
    processor = relationship("User", foreign_keys=[processed_by], backref="processed_refunds")
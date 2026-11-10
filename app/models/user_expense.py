# app/models/user_expense.py - Nouveau fichier

import uuid
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import Column, String, Date, DECIMAL, ForeignKey, Text, DateTime, Boolean, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base

class UserExpense(Base):
    """Modèle pour le suivi détaillé des dépenses par utilisateur"""
    __tablename__ = "user_expenses"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)
    
    # Référence à la dépense principale
    expense_id = Column(UUID(as_uuid=True), ForeignKey("expenses.id"), nullable=False)
    
    # Informations de la dépense
    expense_date = Column(Date, nullable=False)
    expense_type = Column(String(50), nullable=False)
    amount = Column(DECIMAL(15, 2), nullable=False)
    
    # Remboursement
    is_reimbursed = Column(Boolean, default=False)
    reimbursement_date = Column(Date, nullable=True)
    reimbursement_reference = Column(String(100), nullable=True)
    
    # Approbation
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approval_date = Column(DateTime, nullable=True)
    
    # Métadonnées
    description = Column(Text, nullable=True)
    receipt_url = Column(String(500), nullable=True, comment="URL de la pièce jointe")
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relations
    tenant = relationship("Tenant", back_populates="user_expenses")
    user = relationship("User", foreign_keys=[user_id], back_populates="user_expenses")
    branch = relationship("Branch", foreign_keys=[branch_id])
    expense = relationship("Expense", foreign_keys=[expense_id])
    approver = relationship("User", foreign_keys=[approved_by])
    
    __table_args__ = (
        Index('ix_user_expenses_tenant_user', 'tenant_id', 'user_id'),
        Index('ix_user_expenses_user_date', 'user_id', 'expense_date'),
        Index('ix_user_expenses_reimbursed', 'is_reimbursed', 'tenant_id'),
    )
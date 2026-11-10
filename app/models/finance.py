# app/models/finance.py
import uuid
from datetime import datetime, date

from sqlalchemy import (
    Column,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Date,
    DECIMAL,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


# =====================================
# PÉRIODES FINANCIÈRES
# =====================================
class FinancialPeriod(Base):
    __tablename__ = "financial_periods"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    period_type = Column(
        String(20),
        nullable=False,
        comment="daily, weekly, monthly, quarterly, yearly",
    )
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    period_name = Column(String(100), nullable=False)

    total_sales = Column(DECIMAL(15, 2), default=0)
    total_cost = Column(DECIMAL(15, 2), default=0)
    gross_profit = Column(DECIMAL(15, 2), default=0)
    gross_margin = Column(Float, default=0.0)

    total_expenses = Column(DECIMAL(15, 2), default=0)
    net_profit = Column(DECIMAL(15, 2), default=0)
    net_margin = Column(Float, default=0.0)

    notes = Column(Text)
    is_closed = Column(Boolean, default=False)
    closed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    closed_at = Column(DateTime)

    # Relations
    tenant = relationship("Tenant", back_populates="financial_periods")
    transactions = relationship(
        "FinancialTransaction",
        back_populates="period",
        cascade="all, delete-orphan",
    )
    closer = relationship("User")

    __table_args__ = (
        Index(
            "ix_financial_periods_tenant_period",
            "tenant_id",
            "period_type",
            "period_start",
        ),
        Index(
            "ix_financial_periods_period_range",
            "period_start",
            "period_end",
        ),
    )


# =====================================
# TRANSACTIONS FINANCIÈRES
# =====================================
class FinancialTransaction(Base):
    __tablename__ = "financial_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    period_id = Column(UUID(as_uuid=True), ForeignKey("financial_periods.id"))
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=True)
    expense_id = Column(UUID(as_uuid=True), ForeignKey("expenses.id"), nullable=True)

    transaction_date = Column(Date, nullable=False, index=True)
    transaction_type = Column(String(30), nullable=False)
    reference = Column(String(100))

    amount = Column(DECIMAL(15, 2), nullable=False)
    tax_amount = Column(DECIMAL(15, 2), default=0)
    total_amount = Column(DECIMAL(15, 2), nullable=False)

    category = Column(String(100), index=True)
    subcategory = Column(String(100))
    description = Column(String(500))
    notes = Column(Text)

    is_reconciled = Column(Boolean, default=False)
    reconciled_at = Column(DateTime)

    # Relations
    tenant = relationship("Tenant", back_populates="financial_transactions")
    period = relationship("FinancialPeriod", back_populates="transactions")

    sale = relationship(
        "Sale",
        back_populates="financial_transaction",
        uselist=False,
        foreign_keys=[sale_id],
    )

    expense = relationship(
        "Expense",
        back_populates="financial_transaction",
        uselist=False,
        foreign_keys=[expense_id],
    )

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# =====================================
# EXPENSES
# =====================================
class Expense(Base):
    __tablename__ = "expenses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    # =====================================
    # NOUVEAUX CHAMPS : BRANCHE ET UTILISATEUR
    # =====================================
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True, 
                    comment="Utilisateur qui a généré la dépense")
    
    expense_date = Column(Date, nullable=False, index=True)
    expense_type = Column(String(50), nullable=False)

    amount = Column(DECIMAL(15, 2), nullable=False)
    tax_amount = Column(DECIMAL(15, 2), default=0)
    total_amount = Column(DECIMAL(15, 2), nullable=False)

    supplier = Column(String(200))
    payee = Column(String(200))
    payment_method = Column(String(30))
    payment_reference = Column(String(100))

    description = Column(String(500))
    notes = Column(Text)

    invoice_number = Column(String(100))
    invoice_date = Column(Date)

    is_recurring = Column(Boolean, default=False)
    recurrence_interval = Column(String(30))
    next_due_date = Column(Date)

    # =====================================
    # NOUVEAUX CHAMPS POUR LE SUIVI
    # =====================================
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approval_status = Column(String(20), default="pending", 
                            comment="pending, approved, rejected")
    rejection_reason = Column(Text, nullable=True)
    
    cost_center = Column(String(100), nullable=True, comment="Centre de coût")
    project_code = Column(String(50), nullable=True, comment="Code projet si applicable")

    # Relations
    tenant = relationship("Tenant", back_populates="expenses")
    branch = relationship("Branch", foreign_keys=[branch_id], back_populates="expenses")
    user = relationship("User", foreign_keys=[user_id], back_populates="expenses")
    approver = relationship("User", foreign_keys=[approved_by], overlaps="expenses_approved")

    financial_transaction = relationship(
        "FinancialTransaction",
        back_populates="expense",
        uselist=False,
    )

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index('ix_expenses_tenant_branch', 'tenant_id', 'branch_id'),
        Index('ix_expenses_tenant_user', 'tenant_id', 'user_id'),
        Index('ix_expenses_branch_date', 'branch_id', 'expense_date'),
        Index('ix_expenses_user_date', 'user_id', 'expense_date'),
        Index('ix_expenses_approval_status', 'tenant_id', 'approval_status'),
    )
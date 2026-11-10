# app/models/debt_payment.py
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    Text,
    DECIMAL,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship

from app.db.base import Base


class DebtPayment(Base):
    __tablename__ = "debt_payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    reference = Column(String(50), nullable=False, unique=True)

    # Liens
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)  
    debt_id = Column(UUID(as_uuid=True), ForeignKey("debts.id"), nullable=False)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=True)

    # Paiement
    amount = Column(DECIMAL(15, 2), nullable=False)
    payment_method = Column(
        String(20),
        nullable=False,
        comment="cash, mobile_money, card, check, bank_transfer",
    )

    # Détails méthode
    mobile_network = Column(String(20))
    mobile_number = Column(String(20))
    card_last_four = Column(String(4))
    card_type = Column(String(20))
    check_number = Column(String(50))
    bank_name = Column(String(100))
    bank_account = Column(String(100))

    status = Column(
        String(20),
        default="success",
        comment="success, failed, pending, refunded",
    )
    failure_reason = Column(Text)

    processed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    notes = Column(Text)
    payment_data = Column(JSON, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # =======================
    # Relations
    # =======================
    tenant = relationship("Tenant")
    customer = relationship("Customer", back_populates="debt_payments")  # Changé: client -> customer
    debt = relationship("Debt", back_populates="payments")
    sale = relationship("Sale")
    processor = relationship("User", backref="payments_processed", overlaps="processed_debt_payments")

    __table_args__ = (
        Index("ix_debt_payments_tenant_date", "tenant_id", "created_at"),
        Index("ix_debt_payments_customer", "tenant_id", "customer_id"),  # Changé: client -> customer
        Index("ix_debt_payments_reference", "reference"),
        Index("ix_debt_payments_debt", "tenant_id", "debt_id"),
        Index("ix_debt_payments_sale", "tenant_id", "sale_id"),
        Index("ix_debt_payments_processor", "tenant_id", "processed_by"),
    )

    # =======================
    # Méthodes
    # =======================
    def process_payment(self, user_id):
        """Traite le paiement"""
        self.processed_by = user_id
        self.status = "success"
        return self
    
    def refund_payment(self, reason=None):
        """Rembourse le paiement"""
        self.status = "refunded"
        if reason:
            self.notes = f"{self.notes or ''}\nRemboursé: {reason}"
        return self
    
    def __repr__(self):
        return f"<DebtPayment {self.reference} | {self.amount} | {self.status}>"
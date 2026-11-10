# app/models/debt.py
import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    Column, String, DateTime, ForeignKey, 
    Text, Date, Index, DECIMAL, Boolean
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base

class Debt(Base):
    __tablename__ = "debts"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    
    # Customer (anciennement client)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    
    # Vente associée
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=True)
    
    # Montant
    initial_amount = Column(DECIMAL(15, 2), nullable=False)
    paid_amount = Column(DECIMAL(15, 2), default=0)
    remaining_amount = Column(DECIMAL(15, 2), nullable=False)
    interest_rate = Column(DECIMAL(5, 2), default=0.0)
    interest_amount = Column(DECIMAL(15, 2), default=0.0)
    
    # Dates
    issue_date = Column(Date, nullable=False, default=datetime.utcnow().date())
    due_date = Column(Date, nullable=False)
    last_payment_date = Column(Date, nullable=True)
    
    # Statut
    status = Column(
        String(20),
        default="pending",
        comment="pending, partially_paid, paid, overdue, defaulted, cancelled"
    )
    
    # Métadonnées
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # =======================
    # Relations
    # =======================
    tenant = relationship("Tenant")
    customer = relationship("Customer", back_populates="debts")  # Changé: client -> customer
    sale = relationship("Sale", back_populates="debts")
    payments = relationship("DebtPayment", back_populates="debt")
    
    # =======================
    # Indexes
    # =======================
    __table_args__ = (
        Index("ix_debts_tenant_customer", "tenant_id", "customer_id"),  # Changé: client -> customer
        Index("ix_debts_tenant_status", "tenant_id", "status"),
        Index("ix_debts_tenant_due_date", "tenant_id", "due_date"),
        Index("ix_debts_tenant_sale", "tenant_id", "sale_id"),
    )
    
    # =======================
    # Propriétés
    # =======================
    @property
    def days_overdue(self):
        """Jours de retard"""
        if self.status in ["overdue", "defaulted"] and self.due_date:
            today = datetime.utcnow().date()
            if today > self.due_date:
                return (today - self.due_date).days
        return 0
    
    @property
    def total_due(self):
        """Montant total dû (capital + intérêts)"""
        return self.remaining_amount + self.interest_amount
    
    @property
    def payment_progress(self):
        """Progression du paiement en pourcentage"""
        if self.initial_amount == 0:
            return 100
        return ((self.initial_amount - self.remaining_amount) / self.initial_amount) * 100
    
    # =======================
    # Méthodes
    # =======================
    def calculate_interest(self):
        """Calcule les intérêts si en retard"""
        if self.days_overdue > 0 and self.interest_rate > 0:
            daily_interest = self.interest_rate / 36500  # Taux journalier
            self.interest_amount = self.remaining_amount * daily_interest * self.days_overdue
        return self
    
    def add_payment(self, amount, payment_method="cash", notes=None):
        """Ajoute un paiement à la dette"""
        if amount <= 0:
            raise ValueError("Le montant du paiement doit être positif")
        
        if amount > self.total_due:
            raise ValueError(f"Le paiement ({amount}) dépasse le montant dû ({self.total_due})")
        
        self.paid_amount += amount
        self.remaining_amount = max(0, self.remaining_amount - amount)
        self.last_payment_date = datetime.utcnow().date()
        
        # Mettre à jour le statut
        if self.remaining_amount <= 0:
            self.status = "paid"
        elif self.remaining_amount < self.initial_amount:
            self.status = "partially_paid"
        
        return self
    
    def update_status(self):
        """Met à jour le statut en fonction de la date d'échéance"""
        if self.status == "paid":
            return self
            
        today = datetime.utcnow().date()
        if self.due_date and today > self.due_date:
            if self.status not in ["overdue", "defaulted"]:
                self.status = "overdue"
        
        return self
    
    def __repr__(self):
        return f"<Debt Customer:{self.customer_id} Amount:{self.remaining_amount}/{self.initial_amount} Status:{self.status}>"  # Changé: client -> customer
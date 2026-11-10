# app/models/payment.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime, ForeignKey, DECIMAL
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship

from app.db.base import Base


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = {"extend_existing": True}

    # =====================================
    # IDENTIFIANTS
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # =====================================
    # RÉFÉRENCES EXTERNES
    # =====================================
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=True)
    invoice_id = Column(UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=True)
    invoice_payment_id = Column(UUID(as_uuid=True), ForeignKey("invoice_payments.id"), nullable=True)

    # =====================================
    # INFORMATIONS D'ABONNEMENT
    # =====================================
    subscription_id = Column(UUID(as_uuid=True), nullable=True)
    subscription_plan = Column(String(50), nullable=True)  # "starter", "professional", "enterprise"
    billing_period = Column(String(20), nullable=True)     # "monthly", "yearly"
    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)
    subscription_type = Column(String(30), nullable=True)  # "trial", "paid", "renewal"

    # =====================================
    # MONTANT ET MÉTHODE DE PAIEMENT
    # =====================================
    amount = Column(DECIMAL(15, 2), nullable=False)
    payment_method = Column(
        String(30),
        nullable=False,
        comment="cash, mobile_money, visa, mastercard, bank_transfer, cheque, credit_note"
    )

    # =====================================
    # RÉFÉRENCES ET TRANSACTION
    # =====================================
    reference = Column(String(100), nullable=True)
    transaction_id = Column(String(100), nullable=True)
    
    # =====================================
    # MÉTADONNÉES
    # =====================================
    payment_meta = Column(JSON, default=dict)
    
    # =====================================
    # STATUT
    # =====================================
    status = Column(String(20), default="success", comment="success, failed, pending, refunded, cancelled")
    
    # =====================================
    # INFORMATIONS MOBILE MONEY
    # =====================================
    mobile_operator = Column(String(20), nullable=True)
    mobile_number = Column(String(20), nullable=True)
    
    # =====================================
    # INFORMATIONS CARTE BANCAIRE
    # =====================================
    card_last4 = Column(String(4), nullable=True)
    card_brand = Column(String(20), nullable=True)

    # =====================================
    # TIMESTAMPS
    # =====================================
    paid_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", back_populates="payments")
    sale = relationship("Sale", back_populates="payments")
    invoice = relationship("Invoice", foreign_keys=[invoice_id])
    invoice_payment = relationship("InvoicePayment", foreign_keys=[invoice_payment_id])

    # =====================================
    # PROPRIÉTÉS
    # =====================================
    @property
    def is_subscription_payment(self):
        """Vérifie si c'est un paiement d'abonnement"""
        return self.subscription_plan is not None or self.subscription_id is not None
    
    @property
    def is_trial_payment(self):
        """Vérifie si c'est un paiement d'essai"""
        return self.subscription_type == "trial"
    
    @property
    def is_paid_subscription(self):
        """Vérifie si c'est un abonnement payé"""
        return self.subscription_type == "paid" and self.status == "success"
    
    @property
    def is_invoice_payment(self):
        """Vérifie si c'est un paiement de facture"""
        return self.invoice_id is not None
    
    @property
    def is_mobile_money(self):
        """Vérifie si c'est un paiement mobile money"""
        return self.payment_method == "mobile_money"
    
    @property
    def subscription_active(self):
        """Vérifie si l'abonnement est actif"""
        if not self.period_end:
            return False
        return datetime.utcnow() <= self.period_end and self.status == "success"
    
    @property
    def days_remaining(self):
        """Nombre de jours restants avant expiration"""
        if not self.period_end:
            return None
        remaining = (self.period_end - datetime.utcnow()).days
        return max(0, remaining)
    
    def to_dict(self):
        """Convertit en dictionnaire"""
        return {
            # Identifiants
            'id': str(self.id),
            'tenant_id': str(self.tenant_id),
            'sale_id': str(self.sale_id) if self.sale_id else None,
            'invoice_id': str(self.invoice_id) if self.invoice_id else None,
            'invoice_payment_id': str(self.invoice_payment_id) if self.invoice_payment_id else None,
            
            # Abonnement
            'subscription_id': str(self.subscription_id) if self.subscription_id else None,
            'subscription_plan': self.subscription_plan,
            'billing_period': self.billing_period,
            'period_start': self.period_start.isoformat() if self.period_start else None,
            'period_end': self.period_end.isoformat() if self.period_end else None,
            'subscription_type': self.subscription_type,
            
            # Paiement
            'amount': float(self.amount),
            'payment_method': self.payment_method,
            'reference': self.reference,
            'transaction_id': self.transaction_id,
            'status': self.status,
            
            # Métadonnées
            'payment_meta': self.payment_meta if self.payment_meta else {},
            
            # Mobile money
            'mobile_operator': self.mobile_operator,
            'mobile_number': self.mobile_number,
            
            # Carte bancaire
            'card_last4': self.card_last4,
            'card_brand': self.card_brand,
            
            # Timestamps
            'paid_at': self.paid_at.isoformat() if self.paid_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            
            # Propriétés calculées
            'is_subscription_payment': self.is_subscription_payment,
            'is_trial_payment': self.is_trial_payment,
            'is_paid_subscription': self.is_paid_subscription,
            'is_invoice_payment': self.is_invoice_payment,
            'is_mobile_money': self.is_mobile_money,
            'subscription_active': self.subscription_active,
            'days_remaining': self.days_remaining
        }
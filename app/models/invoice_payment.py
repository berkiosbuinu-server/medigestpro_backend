# app/models/invoice_payment.py
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Column, String, Float, DateTime, ForeignKey, Text, DECIMAL, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship, validates
from sqlalchemy.ext.hybrid import hybrid_property
from app.db.base import Base

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .payment import Payment


class InvoicePayment(Base):
    """
    Paiement spécifique pour les factures.
    Peut être lié au modèle Payment général pour synchronisation.
    """
    __tablename__ = "invoice_payments"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    invoice_id = Column(UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False)
    
    # Lien vers le paiement général (optionnel)
    payment_id = Column(UUID(as_uuid=True), nullable=True)
    
    # Paiement
    amount = Column(DECIMAL(15, 2), nullable=False)
    payment_method = Column(String(30), nullable=False, 
                           comment="cash, mobile_money, visa, mastercard, bank_transfer, cheque, credit_note")
    payment_gateway = Column(String(50), nullable=True)
    
    # Références
    reference = Column(String(100), nullable=True)
    transaction_id = Column(String(100), nullable=True)
    
    # Statut
    status = Column(String(20), default="success", comment="success, failed, pending, refunded, cancelled")
    
    # Métadonnées
    notes = Column(Text, nullable=True)
    payment_meta = Column(JSON, default=dict)
    
    # Informations bancaires (si applicable)
    bank_name = Column(String(100), nullable=True)
    bank_account = Column(String(50), nullable=True)
    cheque_number = Column(String(50), nullable=True)
    
    # Mobile money
    mobile_operator = Column(String(20), nullable=True, comment="airtel, orange, vodacom, mpesa")
    mobile_number = Column(String(20), nullable=True)
    
    # Cartes
    card_last4 = Column(String(4), nullable=True)
    card_brand = Column(String(20), nullable=True)
    
    # Pour abonnements
    subscription_id = Column(UUID(as_uuid=True), nullable=True)
    subscription_period = Column(String(20), nullable=True, comment="monthly, quarterly, yearly")
    
    # Timestamps
    payment_date = Column(DateTime, default=datetime.utcnow)
    received_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relations
    tenant = relationship("Tenant")
    invoice = relationship("Invoice", back_populates="payments")
    
    # =====================================
    # VALIDATEURS
    # =====================================
    @validates('amount')
    def validate_amount(self, key, value):
        if value is not None and float(value) <= 0:
            raise ValueError("Le montant du paiement doit être positif")
        return value
    
    @validates('payment_method')
    def validate_payment_method(self, key, value):
        valid_methods = [
            'cash', 'mobile_money', 'visa', 'mastercard', 
            'bank_transfer', 'cheque', 'credit_note'
        ]
        if value not in valid_methods:
            raise ValueError(f"Méthode de paiement invalide. Options: {', '.join(valid_methods)}")
        return value
    
    # =====================================
    # PROPRIÉTÉS
    # =====================================
    @hybrid_property
    def is_online_payment(self):
        return self.payment_gateway is not None
    
    @is_online_payment.expression
    def is_online_payment(cls):
        return cls.payment_gateway.isnot(None)
    
    @hybrid_property
    def is_mobile_money(self):
        return self.payment_method == 'mobile_money'
    
    @is_mobile_money.expression
    def is_mobile_money(cls):
        return cls.payment_method == 'mobile_money'
    
    @hybrid_property
    def is_paid(self):
        return self.status == 'success'
    
    @hybrid_property
    def is_pending(self):
        return self.status == 'pending'
    
    @hybrid_property
    def is_failed(self):
        return self.status == 'failed'
    
    # =====================================
    # MÉTHODES
    # =====================================
    def mark_as_success(self, confirmed_by=None):
        """Marque le paiement comme réussi"""
        self.status = 'success'
        self.confirmed_at = datetime.utcnow()
        if confirmed_by:
            self.payment_meta['confirmed_by'] = str(confirmed_by)
    
    def mark_as_failed(self, reason=""):
        """Marque le paiement comme échoué"""
        self.status = 'failed'
        if reason:
            self.notes = f"{self.notes or ''}\nÉchec: {reason}".strip()
    
    def to_dict(self):
        """Convertit le paiement en dictionnaire"""
        return {
            'id': str(self.id),
            'invoice_id': str(self.invoice_id),
            'payment_id': str(self.payment_id) if self.payment_id else None,
            'amount': float(self.amount),
            'payment_method': self.payment_method,
            'reference': self.reference,
            'transaction_id': self.transaction_id,
            'status': self.status,
            'payment_date': self.payment_date.isoformat() if self.payment_date else None,
            'received_at': self.received_at.isoformat() if self.received_at else None,
            'confirmed_at': self.confirmed_at.isoformat() if self.confirmed_at else None,
            'is_online_payment': self.is_online_payment,
            'is_mobile_money': self.is_mobile_money,
            'mobile_operator': self.mobile_operator,
            'mobile_number': self.mobile_number,
            'bank_name': self.bank_name,
            'cheque_number': self.cheque_number,
            'card_last4': self.card_last4,
            'card_brand': self.card_brand,
            'payment_gateway': self.payment_gateway,
            'subscription_id': str(self.subscription_id) if self.subscription_id else None,
            'subscription_period': self.subscription_period,
            'notes': self.notes,
            'is_paid': self.is_paid,
            'is_pending': self.is_pending,
            'is_failed': self.is_failed
        }
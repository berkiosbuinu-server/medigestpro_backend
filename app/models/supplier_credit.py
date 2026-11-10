# app/models/supplier_credit.py
"""
Gestion du crédit fournisseurs, achats à crédit et suivi des dettes
Conforme aux normes SYSCOHADA révisées
"""

from __future__ import annotations

import uuid
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Any
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, String, Integer, DateTime, ForeignKey, Numeric, Text, Date, 
    Boolean, Index, CheckConstraint, Float
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func

from app.db.base import Base


# =====================================
# ENUMS
# =====================================

class CreditStatus(str, PyEnum):
    """Statut du crédit"""
    ACTIVE = "active"
    PARTIALLY_PAID = "partially_paid"
    FULLY_PAID = "fully_paid"
    OVERDUE = "overdue"
    DEFAULTED = "defaulted"
    CANCELLED = "cancelled"


class PaymentFrequency(str, PyEnum):
    """Fréquence de remboursement"""
    PER_SALE = "per_sale"  # À chaque vente
    PER_DAY = "per_day"    # Quotidien
    PER_WEEK = "per_week"  # Hebdomadaire
    PER_MONTH = "per_month" # Mensuel
    FIXED_DATE = "fixed_date" # Date fixe
    CUSTOM = "custom"      # Personnalisé


class ProductOwnershipStatus(str, PyEnum):
    """Statut de propriété du produit"""
    FULLY_OWNED = "fully_owned"  # Produit entièrement payé (capital propre)
    CREDIT = "credit"             # Produit acheté à crédit
    PARTIAL_CREDIT = "partial_credit" # Partiellement à crédit
    CONSIGNMENT = "consignment"   # Dépôt-vente
    LEASED = "leased"             # En location
    MIXED = "mixed"               # Mixte (plusieurs sources)


# =====================================
# MODÈLE: Configuration fournisseur
# =====================================

class SupplierCreditConfig(Base):
    """
    Configuration du crédit par fournisseur
    Définit les règles de remboursement et les échéances
    """
    __tablename__ = "supplier_credit_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False, index=True)

    # =====================================
    # CONFIGURATION GÉNÉRALE
    # =====================================
    name = Column(String(200), nullable=False, comment="Nom de la configuration")
    description = Column(Text, nullable=True)
    is_default = Column(Boolean, default=False, comment="Configuration par défaut pour ce fournisseur")
    is_active = Column(Boolean, default=True)

    # =====================================
    # CONDITIONS DE CRÉDIT
    # =====================================
    max_credit_amount = Column(Numeric(15, 2), nullable=True, comment="Montant maximum de crédit autorisé")
    max_credit_days = Column(Integer, nullable=True, comment="Nombre maximum de jours de crédit")
    interest_rate = Column(Numeric(5, 2), default=0.00, comment="Taux d'intérêt annuel (%)")
    late_fee_rate = Column(Numeric(5, 2), default=0.00, comment="Taux de pénalité de retard (%)")
    
    # =====================================
    # CONFIGURATION REMBOURSEMENT
    # =====================================
    payment_frequency = Column(String(30), nullable=False, default=PaymentFrequency.PER_SALE.value)
    
    # Pourcentage du produit de vente à utiliser pour le remboursement
    # Ex: 30% de chaque vente va au remboursement du crédit
    repayment_percentage_of_sale = Column(Numeric(5, 2), default=30.00, 
                                         comment="% de chaque vente alloué au remboursement")
    
    # Définition: 6$ remboursement sur 20$ de vente = 30%
    min_repayment_amount = Column(Numeric(15, 2), default=0.00, 
                                 comment="Montant minimum de remboursement par période")
    max_repayment_amount = Column(Numeric(15, 2), nullable=True, 
                                 comment="Montant maximum de remboursement par période")
    
    # Échéances personnalisées
    custom_due_dates = Column(JSONB, default=list, 
                             comment="Liste des dates d'échéance personnalisées")
    
    # Jours de grâce
    grace_period_days = Column(Integer, default=0, comment="Jours de grâce après échéance")
    
    # =====================================
    # RÈGLES DE PRIORITÉ
    # =====================================
    repayment_priority = Column(Integer, default=1, comment="Priorité de remboursement (1=haute)")
    auto_repayment_enabled = Column(Boolean, default=True, comment="Remboursement automatique à chaque vente")
    
    # =====================================
    # NOTIFICATIONS
    # =====================================
    send_reminders = Column(Boolean, default=True)
    reminder_days_before = Column(Integer, default=3, comment="Jours avant échéance pour rappel")
    
    # =====================================
    # MÉTADONNÉES
    # =====================================
    notes = Column(Text, nullable=True)
    meta_data = Column(JSONB, default=dict)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    supplier = relationship("Supplier", back_populates="credit_configs")
    creator = relationship("User", foreign_keys=[created_by])

    __table_args__ = (
        Index("ix_supplier_credit_configs_supplier", "supplier_id"),
        Index("ix_supplier_credit_configs_default", "supplier_id", "is_default"),
        Index("ix_supplier_credit_configs_active", "tenant_id", "is_active"),
        CheckConstraint("max_credit_amount IS NULL OR max_credit_amount >= 0", 
                       name="check_max_credit_positive"),
        CheckConstraint("repayment_percentage_of_sale BETWEEN 0 AND 100", 
                       name="check_repayment_percentage"),
    )

    @validates("repayment_percentage_of_sale")
    def validate_percentage(self, key, value):
        if value < 0 or value > 100:
            raise ValueError("Le pourcentage doit être entre 0 et 100")
        return value


# =====================================
# MODÈLE: Dette fournisseur
# =====================================

class SupplierDebt(Base):
    """
    Dette envers un fournisseur
    Suivi global du crédit par fournisseur
    """
    __tablename__ = "supplier_debts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False, index=True)
    
    # =====================================
    # MONTANTS
    # =====================================
    total_credit_amount = Column(Numeric(15, 2), nullable=False, default=0.00, 
                                 comment="Montant total du crédit accordé")
    total_repaid_amount = Column(Numeric(15, 2), nullable=False, default=0.00, 
                                 comment="Montant total déjà remboursé")
    current_debt = Column(Numeric(15, 2), nullable=False, default=0.00, 
                          comment="Dette actuelle = total_credit - total_repaid")
    
    # =====================================
    # INTÉRÊTS ET PÉNALITÉS
    # =====================================
    accrued_interest = Column(Numeric(15, 2), default=0.00, comment="Intérêts courus")
    late_fees = Column(Numeric(15, 2), default=0.00, comment="Pénalités de retard")
    
    # =====================================
    # DATES
    # =====================================
    first_credit_date = Column(Date, nullable=True, comment="Date du premier crédit")
    last_repayment_date = Column(Date, nullable=True, comment="Date du dernier remboursement")
    next_due_date = Column(Date, nullable=True, comment="Prochaine échéance")
    
    # =====================================
    # STATUT
    # =====================================
    status = Column(String(30), nullable=False, default=CreditStatus.ACTIVE.value)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    supplier = relationship("Supplier", back_populates="debt")
    transactions = relationship("SupplierCreditTransaction", back_populates="debt", 
                               cascade="all, delete-orphan")
    purchase_credits = relationship("PurchaseCredit", back_populates="debt")

    __table_args__ = (
        Index("ix_supplier_debts_supplier_status", "supplier_id", "status"),
        Index("ix_supplier_debts_next_due", "next_due_date"),
        Index("ix_supplier_debts_tenant_supplier", "tenant_id", "supplier_id"),
        CheckConstraint("total_credit_amount >= 0", name="check_total_credit_positive"),
        CheckConstraint("total_repaid_amount >= 0", name="check_total_repaid_positive"),
    )

    @property
    def debt_ratio(self) -> float:
        """Ratio dette / crédit total"""
        if self.total_credit_amount == 0:
            return 0.0
        return float((self.current_debt / self.total_credit_amount) * 100)

    @property
    def is_fully_paid(self) -> bool:
        """Dette entièrement remboursée"""
        return self.current_debt <= 0.01

    def update_debt(self) -> 'SupplierDebt':
        """Met à jour le montant de la dette"""
        self.current_debt = self.total_credit_amount - self.total_repaid_amount + self.accrued_interest + self.late_fees
        
        if self.is_fully_paid:
            self.status = CreditStatus.FULLY_PAID.value
        elif self.current_debt > 0:
            self.status = CreditStatus.PARTIALLY_PAID.value if self.total_repaid_amount > 0 else CreditStatus.ACTIVE.value
        
        return self


# =====================================
# MODÈLE: Achat à crédit
# =====================================

class PurchaseCredit(Base):
    """
    Achat spécifique réalisé à crédit
    Lie un achat à une configuration de crédit fournisseur
    """
    __tablename__ = "purchase_credits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    
    # =====================================
    # RÉFÉRENCES
    # =====================================
    purchase_id = Column(UUID(as_uuid=True), ForeignKey("purchases.id"), nullable=False, index=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False, index=True)
    config_id = Column(UUID(as_uuid=True), ForeignKey("supplier_credit_configs.id"), nullable=True)
    debt_id = Column(UUID(as_uuid=True), ForeignKey("supplier_debts.id"), nullable=False)
    
    # =====================================
    # MONTANTS
    # =====================================
    credit_amount = Column(Numeric(15, 2), nullable=False, comment="Montant crédité pour cet achat")
    repaid_amount = Column(Numeric(15, 2), default=0.00, comment="Montant déjà remboursé")
    remaining_amount = Column(Numeric(15, 2), default=0.00, comment="Reste à rembourser")
    
    # =====================================
    # CONDITIONS
    # =====================================
    interest_rate_applied = Column(Numeric(5, 2), default=0.00)
    payment_frequency = Column(String(30), nullable=False)
    repayment_percentage = Column(Numeric(5, 2), nullable=False, comment="% à rembourser par vente")
    
    # =====================================
    # ÉCHÉANCES
    # =====================================
    due_date = Column(Date, nullable=False, comment="Date d'échéance")
    grace_date = Column(Date, nullable=True, comment="Date de grâce")
    
    # Date de la dernière vente qui a déclenché un remboursement
    last_sale_trigger_date = Column(DateTime, nullable=True)
    
    # =====================================
    # STATUT
    # =====================================
    status = Column(String(30), nullable=False, default=CreditStatus.ACTIVE.value)
    
    # =====================================
    # NOTES
    # =====================================
    notes = Column(Text, nullable=True)
    meta_data = Column(JSONB, default=dict)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    purchase = relationship("Purchase", back_populates="credit")
    supplier = relationship("Supplier", back_populates="purchase_credits")
    config = relationship("SupplierCreditConfig")
    debt = relationship("SupplierDebt", back_populates="purchase_credits")
    creator = relationship("User", foreign_keys=[created_by])
    items = relationship("ProductCreditItem", back_populates="purchase_credit", 
                        cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_purchase_credits_purchase", "purchase_id"),
        Index("ix_purchase_credits_supplier_status", "supplier_id", "status"),
        Index("ix_purchase_credits_due_date", "due_date"),
        CheckConstraint("credit_amount >= 0", name="check_credit_amount_positive"),
    )

    @property
    def repayment_progress(self) -> float:
        """Progression du remboursement (%)"""
        if self.credit_amount == 0:
            return 100.0
        return float((self.repaid_amount / self.credit_amount) * 100)

    @property
    def is_overdue(self) -> bool:
        """Vérifie si l'échéance est dépassée"""
        if self.status == CreditStatus.FULLY_PAID.value:
            return False
        return date.today() > self.due_date

    def update_remaining(self) -> 'PurchaseCredit':
        """Met à jour le montant restant"""
        self.remaining_amount = self.credit_amount - self.repaid_amount
        if self.remaining_amount <= 0.01:
            self.status = CreditStatus.FULLY_PAID.value
            self.remaining_amount = Decimal('0')
        return self


# =====================================
# MODÈLE: Produit avec statut de crédit
# =====================================

class ProductCreditItem(Base):
    """
    Produit acheté à crédit avec traçabilité par fournisseur
    Permet de gérer un même produit venant de différents fournisseurs à prix différents
    """
    __tablename__ = "product_credit_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    
    # =====================================
    # RÉFÉRENCES PRODUIT
    # =====================================
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    purchase_credit_id = Column(UUID(as_uuid=True), ForeignKey("purchase_credits.id"), nullable=False, index=True)
    
    # Lien vers le stock spécifique (lot)
    product_stock_id = Column(UUID(as_uuid=True), ForeignKey("product_stocks.id"), nullable=True)
    
    # =====================================
    # INFORMATIONS PRODUIT
    # =====================================
    product_name = Column(String(200), nullable=False)
    product_code = Column(String(50), nullable=False)
    batch_number = Column(String(100), nullable=True)
    
    # =====================================
    # STATUT DE PROPRIÉTÉ
    # =====================================
    ownership_status = Column(String(30), nullable=False, default=ProductOwnershipStatus.CREDIT.value,
                              comment="credit, fully_owned, partial_credit, consignment")
    
    # =====================================
    # QUANTITÉS ET PRIX
    # =====================================
    quantity = Column(Integer, nullable=False, default=1)
    unit_cost = Column(Numeric(15, 4), nullable=False, comment="Prix d'achat unitaire")
    total_cost = Column(Numeric(15, 2), nullable=False, comment="Coût total")
    
    # Partie crédit vs capital propre
    credit_portion = Column(Numeric(15, 2), nullable=False, comment="Partie financée à crédit")
    equity_portion = Column(Numeric(15, 2), nullable=False, comment="Partie sur capital propre")
    
    # =====================================
    # SUIVI DES VENTES
    # =====================================
    total_sold_quantity = Column(Integer, default=0, comment="Quantité totale vendue")
    remaining_quantity = Column(Integer, default=0, comment="Quantité restante en stock")
    
    # Montant remboursé via les ventes
    amount_repaid_from_sales = Column(Numeric(15, 2), default=0.00)
    
    # =====================================
    # STATUT
    # =====================================
    is_active = Column(Boolean, default=True)
    is_fully_repaid = Column(Boolean, default=False)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    product = relationship("Product", back_populates="credit_items")
    purchase_credit = relationship("PurchaseCredit", back_populates="items")
    product_stock = relationship("ProductStock")
    sale_allocations = relationship("SaleCreditAllocation", back_populates="product_credit_item")

    __table_args__ = (
        Index("ix_product_credit_items_product", "product_id", "ownership_status"),
        Index("ix_product_credit_items_purchase", "purchase_credit_id"),
        Index("ix_product_credit_items_batch", "batch_number"),
        Index("ix_product_credit_items_supplier_trace", "tenant_id", "product_id", "purchase_credit_id"),
        CheckConstraint("quantity >= 0", name="check_quantity_positive"),
        CheckConstraint("credit_portion >= 0", name="check_credit_portion_positive"),
    )

    @property
    def credit_ratio(self) -> float:
        """Ratio crédit / coût total"""
        if self.total_cost == 0:
            return 0.0
        return float((self.credit_portion / self.total_cost) * 100)

    @property
    def remaining_credit_to_reimburse(self) -> Decimal:
        """Crédit restant à rembourser pour ce produit"""
        # Calcul basé sur la proportion de produit non encore vendu * portion crédit
        if self.quantity == 0:
            return Decimal('0')
        sold_ratio = Decimal(self.total_sold_quantity) / Decimal(self.quantity)
        return self.credit_portion * (Decimal('1') - sold_ratio)

    def update_from_sale(self, sold_quantity: int, sale_amount: Decimal) -> Decimal:
        """
        Met à jour le produit après une vente
        Retourne le montant à rembourser au fournisseur
        """
        if sold_quantity <= 0 or self.remaining_quantity < sold_quantity:
            return Decimal('0')
        
        self.total_sold_quantity += sold_quantity
        self.remaining_quantity -= sold_quantity
        
        # Calcul du remboursement basé sur la proportion crédit
        sold_ratio = Decimal(sold_quantity) / Decimal(self.quantity)
        repayment_amount = self.credit_portion * sold_ratio
        
        self.amount_repaid_from_sales += repayment_amount
        
        # Vérifier si entièrement remboursé
        if self.amount_repaid_from_sales >= self.credit_portion - Decimal('0.01'):
            self.is_fully_repaid = True
            self.ownership_status = ProductOwnershipStatus.FULLY_OWNED.value
        
        return repayment_amount


# =====================================
# MODÈLE: Allocation de vente au crédit
# =====================================

class SaleCreditAllocation(Base):
    """
    Allocation d'une vente au remboursement du crédit
    Chaque vente d'un produit à crédit déclenche une allocation de remboursement
    """
    __tablename__ = "sale_credit_allocations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    
    # =====================================
    # RÉFÉRENCES
    # =====================================
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=False, index=True)
    sale_item_id = Column(UUID(as_uuid=True), ForeignKey("sale_items.id"), nullable=False)
    product_credit_item_id = Column(UUID(as_uuid=True), ForeignKey("product_credit_items.id"), nullable=False)
    purchase_credit_id = Column(UUID(as_uuid=True), ForeignKey("purchase_credits.id"), nullable=False)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False)
    
    # =====================================
    # MONTANTS
    # =====================================
    sale_amount = Column(Numeric(15, 2), nullable=False, comment="Montant total de la vente")
    allocated_repayment = Column(Numeric(15, 2), nullable=False, comment="Montant alloué au remboursement")
    capital_portion = Column(Numeric(15, 2), nullable=False, comment="Partie intégrée au capital propre")
    
    # =====================================
    # DÉTAILS DE LA VENTE
    # =====================================
    quantity_sold = Column(Integer, nullable=False)
    unit_sale_price = Column(Numeric(15, 4), nullable=False)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, default=datetime.utcnow)
    sale_date = Column(Date, nullable=False)

    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    sale = relationship("Sale", back_populates="credit_allocations")
    sale_item = relationship("SaleItem", foreign_keys=[sale_item_id])
    product_credit_item = relationship("ProductCreditItem", back_populates="sale_allocations")
    purchase_credit = relationship("PurchaseCredit")
    supplier = relationship("Supplier")
    transaction = relationship("SupplierCreditTransaction", back_populates="sale_allocation", 
                              uselist=False)

    __table_args__ = (
        Index("ix_sale_credit_allocations_sale", "sale_id"),
        Index("ix_sale_credit_allocations_supplier", "supplier_id"),
        Index("ix_sale_credit_allocations_date", "sale_date"),
        Index("ix_sale_credit_allocations_product_credit", "product_credit_item_id"),
        CheckConstraint("allocated_repayment >= 0", name="check_repayment_positive"),
    )


# =====================================
# MODÈLE: Transaction de crédit fournisseur
# =====================================

class SupplierCreditTransaction(Base):
    """
    Transaction de crédit (remboursement, ajustement)
    """
    __tablename__ = "supplier_credit_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    debt_id = Column(UUID(as_uuid=True), ForeignKey("supplier_debts.id"), nullable=False, index=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False, index=True)
    
    # =====================================
    # TYPE DE TRANSACTION
    # =====================================
    transaction_type = Column(String(30), nullable=False,
                              comment="credit_purchase, repayment_from_sale, manual_repayment, interest, late_fee, adjustment")
    
    # =====================================
    # MONTANTS
    # =====================================
    amount = Column(Numeric(15, 2), nullable=False)
    amount_applied_to_principal = Column(Numeric(15, 2), default=0.00)
    amount_applied_to_interest = Column(Numeric(15, 2), default=0.00)
    amount_applied_to_fees = Column(Numeric(15, 2), default=0.00)
    
    # Balance avant/après
    balance_before = Column(Numeric(15, 2), nullable=False)
    balance_after = Column(Numeric(15, 2), nullable=False)
    
    # =====================================
    # RÉFÉRENCES EXTERNES
    # =====================================
    sale_allocation_id = Column(UUID(as_uuid=True), ForeignKey("sale_credit_allocations.id"), nullable=True)
    purchase_credit_id = Column(UUID(as_uuid=True), ForeignKey("purchase_credits.id"), nullable=True)
    payment_id = Column(UUID(as_uuid=True), nullable=True, comment="ID du paiement manuel")
    
    # =====================================
    # DESCRIPTION
    # =====================================
    description = Column(String(500), nullable=True)
    reference = Column(String(100), nullable=True)
    
    # =====================================
    # DATES
    # =====================================
    transaction_date = Column(Date, nullable=False, default=date.today)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    debt = relationship("SupplierDebt", back_populates="transactions")
    supplier = relationship("Supplier")
    sale_allocation = relationship("SaleCreditAllocation", back_populates="transaction")
    purchase_credit = relationship("PurchaseCredit")
    creator = relationship("User", foreign_keys=[created_by])

    __table_args__ = (
        Index("ix_supplier_credit_transactions_debt", "debt_id", "transaction_date"),
        Index("ix_supplier_credit_transactions_supplier", "supplier_id", "transaction_date"),
        Index("ix_supplier_credit_transactions_type", "transaction_type"),
    )

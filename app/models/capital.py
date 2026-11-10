# app/models/capital.py
"""
Modèles pour la gestion du capital et du chiffre d'affaires
Conformes aux normes SYSCOHADA révisées
"""

from __future__ import annotations

import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Dict, Any

from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    Date,
    Index,
    Boolean,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func

from app.db.base import Base


# =====================================
# MODÈLE CAPITAL
# =====================================
class Capital(Base):
    """
    Capital d'une pharmacie ou succursale.
    Conforme aux normes SYSCOHADA (comptes 101, 102, 103, etc.)
    """
    __tablename__ = "capitals"

    # =====================================
    # IDENTIFIANTS
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)

    # =====================================
    # CAPITAL (Compte 101 - Capital social)
    # =====================================
    initial_capital = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Capital initial (1011)")
    current_capital = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Capital actuel")
    
    # =====================================
    # COMPOSITION DU CAPITAL
    # =====================================
    cash_capital = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Capital en caisse (531)")
    stock_capital = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Capital en stock (31)")
    equipment_capital = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Capital en équipement (23)")
    other_capital = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Autres capitaux")

    # =====================================
    # DATES
    # =====================================
    start_date = Column(Date, nullable=False, comment="Date de début du capital")
    last_update_date = Column(Date, nullable=False, comment="Date de dernière mise à jour")

    # =====================================
    # MÉTADONNÉES
    # =====================================
    notes = Column(Text, nullable=True, comment="Notes sur le capital")
    meta_data = Column(JSONB, default=lambda: {}, comment="Métadonnées supplémentaires")

    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", back_populates="capitals")
    pharmacy = relationship("Pharmacy", back_populates="capitals")
    branch = relationship("Branch", back_populates="capitals")
    transactions = relationship("CapitalTransaction", back_populates="capital", cascade="all, delete-orphan")
    adjusted_capitals = relationship("AdjustedCapital", back_populates="capital", cascade="all, delete-orphan")

    # =====================================
    # INDEXES ET CONFIGURATION
    # =====================================
    __table_args__ = (
        Index("ix_capitals_tenant_pharmacy", "tenant_id", "pharmacy_id"),
        Index("ix_capitals_tenant_branch", "tenant_id", "branch_id"),
        Index("ix_capitals_start_date", "start_date"),
        Index("ix_capitals_last_update", "last_update_date"),
    )

    # =====================================
    # VALIDATIONS
    # =====================================
    @validates("initial_capital", "current_capital", "cash_capital", "stock_capital", "equipment_capital", "other_capital")
    def validate_capital_amounts(self, key, value):
        if value is None:
            return Decimal('0')
        dec_value = Decimal(str(value))
        if dec_value < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return dec_value

    @validates("start_date")
    def validate_start_date(self, key, value):
        if value is None:
            return date.today()
        return value

    # =====================================
    # PROPRIÉTÉS CALCULÉES
    # =====================================
    @property
    def total_capital(self) -> Decimal:
        return self.cash_capital + self.stock_capital + self.equipment_capital + self.other_capital

    @property
    def capital_variation(self) -> Decimal:
        return self.current_capital - self.initial_capital

    @property
    def capital_growth_rate(self) -> float:
        if self.initial_capital == 0:
            return 0.0
        return float((self.capital_variation / self.initial_capital) * 100)

    # =====================================
    # MÉTHODES
    # =====================================
    def add_capital(self, amount: Decimal, category: str, description: str = None) -> None:
        amount = Decimal(str(amount))

        allowed = ["cash", "stock", "equipment", "other"]
        if category not in allowed:
            raise ValueError(f"Catégorie invalide: {category}")

        if category == "cash":
            self.cash_capital += amount
        elif category == "stock":
            self.stock_capital += amount
        elif category == "equipment":
            self.equipment_capital += amount
        elif category == "other":
            self.other_capital += amount

        self.current_capital = self.total_capital
        self.last_update_date = date.today()

    def remove_capital(self, amount: Decimal, category: str, description: str = None) -> None:
        amount = Decimal(str(amount))
        if amount > self.current_capital:
            raise ValueError("Montant à retirer supérieur au capital actuel")
        
        self.current_capital -= amount
        
        if category == "cash":
            if amount > self.cash_capital:
                raise ValueError("Montant à retirer supérieur à la caisse")
            self.cash_capital -= amount
        elif category == "stock":
            if amount > self.stock_capital:
                raise ValueError("Montant à retirer supérieur au stock")
            self.stock_capital -= amount
        elif category == "equipment":
            if amount > self.equipment_capital:
                raise ValueError("Montant à retirer supérieur à l'équipement")
            self.equipment_capital -= amount
        elif category == "other":
            if amount > self.other_capital:
                raise ValueError("Montant à retirer supérieur aux autres capitaux")
            self.other_capital -= amount
        
        self.last_update_date = date.today()

    def sync_stock_capital(self, stock_value: Decimal) -> None:
        self.stock_capital = Decimal(str(stock_value))
        self.current_capital = self.total_capital
        self.last_update_date = date.today()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "pharmacy_id": str(self.pharmacy_id) if self.pharmacy_id else None,
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "initial_capital": float(self.initial_capital),
            "current_capital": float(self.current_capital),
            "cash_capital": float(self.cash_capital),
            "stock_capital": float(self.stock_capital),
            "equipment_capital": float(self.equipment_capital),
            "other_capital": float(self.other_capital),
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "last_update_date": self.last_update_date.isoformat() if self.last_update_date else None,
            "notes": self.notes,
            "meta_data": self.meta_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<Capital {self.pharmacy_id}: {self.current_capital} (Initial: {self.initial_capital})>"


# =====================================
# MODÈLE CAPITAL TRANSACTION
# =====================================
class CapitalTransaction(Base):
    """
    Transactions sur le capital.
    Trace toutes les opérations qui modifient le capital.
    """
    __tablename__ = "capital_transactions"

    # =====================================
    # IDENTIFIANTS
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    capital_id = Column(UUID(as_uuid=True), ForeignKey("capitals.id"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)

    # =====================================
    # TYPE DE TRANSACTION
    # =====================================
    transaction_type = Column(
        String(50), 
        nullable=False, 
        comment="initial, increase, decrease, profit_added, loss_deducted"
    )
    transaction_category = Column(
        String(50), 
        nullable=False, 
        comment="cash, stock, equipment, other, turnover, expense"
    )

    # =====================================
    # MONTANTS
    # =====================================
    amount = Column(Numeric(15, 2), nullable=False, comment="Montant de la transaction")
    previous_capital = Column(Numeric(15, 2), nullable=False, comment="Capital avant transaction")
    new_capital = Column(Numeric(15, 2), nullable=False, comment="Capital après transaction")

    # =====================================
    # RÉFÉRENCES
    # =====================================
    reference_id = Column(UUID(as_uuid=True), nullable=True, comment="ID de référence (sale, purchase, etc.)")
    reference_type = Column(String(50), nullable=True, comment="sale, purchase, expense, investment")

    # =====================================
    # DESCRIPTION
    # =====================================
    description = Column(String(500), nullable=True, comment="Description de la transaction")
    notes = Column(Text, nullable=True, comment="Notes supplémentaires")

    # =====================================
    # DATES
    # =====================================
    transaction_date = Column(Date, nullable=False, comment="Date de la transaction")

    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, comment="Utilisateur ayant créé la transaction")

    # =====================================
    # RELATIONS
    # =====================================
    capital = relationship("Capital", back_populates="transactions")
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    pharmacy = relationship("Pharmacy", foreign_keys=[pharmacy_id])
    branch = relationship("Branch", foreign_keys=[branch_id])
    creator = relationship("User", foreign_keys=[created_by])

    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index("ix_capital_transactions_date", "transaction_date"),
        Index("ix_capital_transactions_type", "transaction_type"),
        Index("ix_capital_transactions_reference", "reference_type", "reference_id"),
        Index("ix_capital_transactions_tenant_pharmacy", "tenant_id", "pharmacy_id", "transaction_date"),
    )

    # =====================================
    # VALIDATIONS
    # =====================================
    @validates("amount")
    def validate_amount(self, key, value):
        if value is None:
            return Decimal('0')
        return Decimal(str(value))

    @validates("transaction_type")
    def validate_transaction_type(self, key, value):
        allowed_types = ["initial", "increase", "decrease", "profit_added", "loss_deducted"]
        if value not in allowed_types:
            raise ValueError(f"transaction_type doit être l'un des: {allowed_types}")
        return value

    @validates("transaction_category")
    def validate_transaction_category(self, key, value):
        allowed_categories = ["cash", "stock", "equipment", "other", "turnover", "expense", "all"]
        if value not in allowed_categories:
            raise ValueError(f"transaction_category doit être l'un des: {allowed_categories}")
        return value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "capital_id": str(self.capital_id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "pharmacy_id": str(self.pharmacy_id) if self.pharmacy_id else None,
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "transaction_type": self.transaction_type,
            "transaction_category": self.transaction_category,
            "amount": float(self.amount),
            "previous_capital": float(self.previous_capital),
            "new_capital": float(self.new_capital),
            "reference_id": str(self.reference_id) if self.reference_id else None,
            "reference_type": self.reference_type,
            "description": self.description,
            "notes": self.notes,
            "transaction_date": self.transaction_date.isoformat() if self.transaction_date else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": str(self.created_by) if self.created_by else None,
        }

    def __repr__(self) -> str:
        return f"<CapitalTransaction {self.transaction_type}: {self.amount} (Capital: {self.previous_capital} -> {self.new_capital})>"


# =====================================
# MODÈLE CAPITAL ACCOUNT (Comptes SYSCOHADA)
# =====================================
class CapitalAccount(Base):
    """
    Comptes selon la nomenclature SYSCOHADA.
    Suivi des soldes des comptes comptables.
    """
    __tablename__ = "capital_accounts"

    # =====================================
    # IDENTIFIANTS
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)

    # =====================================
    # COMPTE
    # =====================================
    account_code = Column(String(20), nullable=False, comment="Code du compte SYSCOHADA (101, 531, 31, etc.)")
    account_name = Column(String(200), nullable=False, comment="Nom du compte")
    account_type = Column(
        String(50), 
        nullable=False, 
        comment="Type de compte: asset, liability, equity, income, expense"
    )
    
    # =====================================
    # SOLDES
    # =====================================
    balance = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Solde du compte")
    debit = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Total débit")
    credit = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Total crédit")

    # =====================================
    # PÉRIODE
    # =====================================
    period_year = Column(Integer, nullable=False, comment="Année comptable")
    period_month = Column(Integer, nullable=True, comment="Mois comptable (optionnel)")

    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    pharmacy = relationship("Pharmacy", foreign_keys=[pharmacy_id])
    branch = relationship("Branch", foreign_keys=[branch_id])

    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index("ix_capital_accounts_code", "account_code"),
        Index("ix_capital_accounts_period", "period_year", "period_month"),
        Index("ix_capital_accounts_tenant_pharmacy", "tenant_id", "pharmacy_id", "period_year"),
        Index("ix_capital_accounts_type", "account_type"),
    )

    # =====================================
    # VALIDATIONS
    # =====================================
    @validates("account_code")
    def validate_account_code(self, key, value):
        if not value or not value.strip():
            raise ValueError("Le code du compte est obligatoire")
        return value.strip()

    @validates("account_name")
    def validate_account_name(self, key, value):
        if not value or not value.strip():
            raise ValueError("Le nom du compte est obligatoire")
        return value.strip()

    @validates("account_type")
    def validate_account_type(self, key, value):
        allowed_types = ["asset", "liability", "equity", "income", "expense"]
        if value not in allowed_types:
            raise ValueError(f"account_type doit être l'un des: {allowed_types}")
        return value

    @validates("balance", "debit", "credit")
    def validate_numeric_values(self, key, value):
        if value is None:
            return Decimal('0')
        return Decimal(str(value))

    # =====================================
    # MÉTHODES
    # =====================================
    def debit_account(self, amount: Decimal, description: str = None) -> None:
        amount = Decimal(str(amount))
        self.debit += amount
        self.balance += amount

    def credit_account(self, amount: Decimal, description: str = None) -> None:
        amount = Decimal(str(amount))
        self.credit += amount
        self.balance -= amount

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "pharmacy_id": str(self.pharmacy_id) if self.pharmacy_id else None,
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "account_code": self.account_code,
            "account_name": self.account_name,
            "account_type": self.account_type,
            "balance": float(self.balance),
            "debit": float(self.debit),
            "credit": float(self.credit),
            "period_year": self.period_year,
            "period_month": self.period_month,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<CapitalAccount {self.account_code} - {self.account_name}: {self.balance}>"


# =====================================
# MODÈLE TURNOVER (Chiffre d'affaires)
# =====================================
class Turnover(Base):
    """
    Chiffre d'affaires par période.
    Stockage des données de CA pour analyse rapide.
    """
    __tablename__ = "turnovers"

    # =====================================
    # IDENTIFIANTS
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)

    # =====================================
    # CHIFFRE D'AFFAIRES
    # =====================================
    total_turnover = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="CA total TTC")
    net_turnover = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="CA net HT")
    tax_amount = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Montant TVA")
    discount_amount = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Montant remises")

    # =====================================
    # COMPOSITION
    # =====================================
    sales_count = Column(Integer, nullable=False, default=0, comment="Nombre de ventes")
    items_sold = Column(Integer, nullable=False, default=0, comment="Nombre d'articles vendus")

    # =====================================
    # PÉRIODE
    # =====================================
    period_date = Column(Date, nullable=False, comment="Date de début de période")
    period_type = Column(String(20), nullable=False, comment="day, week, month, year")

    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    pharmacy = relationship("Pharmacy", foreign_keys=[pharmacy_id])
    branch = relationship("Branch", foreign_keys=[branch_id])

    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index("ix_turnovers_period", "tenant_id", "pharmacy_id", "period_date", "period_type"),
        Index("ix_turnovers_period_date", "period_date"),
        Index("ix_turnovers_period_type", "period_type"),
        Index("ix_turnovers_branch", "branch_id", "period_date"),
    )

    # =====================================
    # VALIDATIONS
    # =====================================
    @validates("total_turnover", "net_turnover", "tax_amount", "discount_amount")
    def validate_amounts(self, key, value):
        if value is None:
            return Decimal('0')
        dec_value = Decimal(str(value))
        if dec_value < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return dec_value

    @validates("sales_count", "items_sold")
    def validate_counts(self, key, value):
        if value is None:
            return 0
        if value < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return int(value)

    @validates("period_type")
    def validate_period_type(self, key, value):
        allowed_types = ["day", "week", "month", "year"]
        if value not in allowed_types:
            raise ValueError(f"period_type doit être l'un des: {allowed_types}")
        return value

    # =====================================
    # MÉTHODES
    # =====================================
    def add_turnover(self, amount: Decimal, tax: Decimal = None, discount: Decimal = None) -> None:
        amount = Decimal(str(amount))
        self.total_turnover += amount
        self.net_turnover += amount - (tax or Decimal('0'))
        
        if tax is not None:
            self.tax_amount += Decimal(str(tax))
        if discount is not None:
            self.discount_amount += Decimal(str(discount))

    def increment_sales(self, count: int = 1, items: int = 1) -> None:
        self.sales_count += count
        self.items_sold += items

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "pharmacy_id": str(self.pharmacy_id) if self.pharmacy_id else None,
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "total_turnover": float(self.total_turnover),
            "net_turnover": float(self.net_turnover),
            "tax_amount": float(self.tax_amount),
            "discount_amount": float(self.discount_amount),
            "sales_count": self.sales_count,
            "items_sold": self.items_sold,
            "period_date": self.period_date.isoformat() if self.period_date else None,
            "period_type": self.period_type,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<Turnover {self.period_type} {self.period_date}: {self.total_turnover}>"


# =====================================
# MODÈLE ADJUSTED CAPITAL (Capital ajusté)
# =====================================
class AdjustedCapital(Base):
    """
    Capital ajusté prenant en compte les dettes fournisseurs.
    Capital réel = Actif total - Dettes fournisseurs
    Conforme aux normes SYSCOHADA
    """
    __tablename__ = "adjusted_capitals"

    # =====================================
    # IDENTIFIANTS
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)
    capital_id = Column(UUID(as_uuid=True), ForeignKey("capitals.id"), nullable=True, index=True)

    # =====================================
    # ACTIFS (Ce que possède l'entreprise)
    # =====================================
    cash_in_hand = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Argent en caisse (531)")
    bank_balance = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Solde bancaire (57)")
    total_liquidities = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Total liquidités")
    
    stock_value = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Valeur du stock (31)")
    equipment_value = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Valeur de l'équipement (23)")
    other_assets = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Autres actifs")
    
    # =====================================
    # PASSIFS (Ce que l'entreprise doit)
    # =====================================
    total_supplier_debt = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Total dettes fournisseurs (40)")
    other_liabilities = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), comment="Autres dettes")
    
    # =====================================
    # CAPITAUX CALCULÉS
    # =====================================
    gross_capital = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), 
                          comment="Capital brut = Total actifs")
    adjusted_capital = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), 
                             comment="Capital réel = Actif total - Dettes")
    equity_capital = Column(Numeric(15, 2), nullable=False, default=Decimal('0'), 
                           comment="Capitaux propres")
    
    # =====================================
    # RATIOS FINANCIERS
    # =====================================
    debt_ratio = Column(Numeric(5, 2), nullable=False, default=Decimal('0'), 
                       comment="Ratio d'endettement (Dettes/Actif) en %")
    liquidity_ratio = Column(Numeric(5, 2), nullable=False, default=Decimal('0'), 
                            comment="Ratio de liquidité (Liquidités/Dettes à court terme)")
    
    # =====================================
    # PÉRIODE DE CALCUL
    # =====================================
    calculation_date = Column(Date, nullable=False, default=date.today, index=True)
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    
    # =====================================
    # STATUT
    # =====================================
    is_current = Column(Boolean, default=True, comment="Est-ce la version la plus récente?")
    calculation_version = Column(Integer, default=1, comment="Version du calcul")
    
    # =====================================
    # MÉTADONNÉES
    # =====================================
    notes = Column(Text, nullable=True)
    calculation_method = Column(String(50), default="auto", comment="auto, manual")
    meta_data = Column(JSONB, default=dict)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    calculated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    pharmacy = relationship("Pharmacy", foreign_keys=[pharmacy_id])
    branch = relationship("Branch", foreign_keys=[branch_id])
    capital = relationship("Capital", back_populates="adjusted_capitals")
    calculator = relationship("User", foreign_keys=[calculated_by])

    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index("ix_adjusted_capitals_pharmacy_date", "pharmacy_id", "calculation_date"),
        Index("ix_adjusted_capitals_tenant_date", "tenant_id", "calculation_date"),
        Index("ix_adjusted_capitals_current", "tenant_id", "pharmacy_id", "is_current"),
        Index("ix_adjusted_capitals_capital", "capital_id"),
        Index("ix_adjusted_capitals_period", "period_start", "period_end"),
    )

    # =====================================
    # VALIDATIONS
    # =====================================
    @validates("cash_in_hand", "bank_balance", "stock_value", "equipment_value", "other_assets")
    def validate_assets(self, key, value):
        if value is None:
            return Decimal('0')
        dec_value = Decimal(str(value))
        if dec_value < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return dec_value

    @validates("total_supplier_debt", "other_liabilities")
    def validate_liabilities(self, key, value):
        if value is None:
            return Decimal('0')
        dec_value = Decimal(str(value))
        if dec_value < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return dec_value

    # =====================================
    # PROPRIÉTÉS CALCULÉES
    # =====================================
    @property
    def total_assets(self) -> Decimal:
        """Total des actifs"""
        return (self.cash_in_hand + self.bank_balance + self.stock_value + 
                self.equipment_value + self.other_assets)

    @property
    def total_liabilities(self) -> Decimal:
        """Total des dettes"""
        return self.total_supplier_debt + self.other_liabilities

    @property
    def net_liquidities(self) -> Decimal:
        """Liquidités nettes après déduction des dettes fournisseurs"""
        return self.total_liquidities - self.total_supplier_debt

    @property
    def working_capital(self) -> Decimal:
        """Fonds de roulement = Actif courant - Passif courant"""
        current_assets = self.cash_in_hand + self.bank_balance + self.stock_value
        current_liabilities = self.total_supplier_debt
        return current_assets - current_liabilities

    # =====================================
    # MÉTHODES
    # =====================================
    def calculate(self) -> 'AdjustedCapital':
        """
        Calcule tous les indicateurs financiers selon les normes SYSCOHADA
        """
        # Calcul des totaux
        self.total_liquidities = self.cash_in_hand + self.bank_balance
        self.gross_capital = self.total_assets
        self.equity_capital = self.total_assets - self.total_liabilities
        self.adjusted_capital = self.gross_capital - self.total_supplier_debt
        
        # Calcul des ratios
        if self.gross_capital > 0:
            self.debt_ratio = (self.total_supplier_debt / self.gross_capital) * 100
        else:
            self.debt_ratio = Decimal('0')
        
        if self.total_supplier_debt > 0:
            self.liquidity_ratio = (self.total_liquidities / self.total_supplier_debt) * 100
        else:
            self.liquidity_ratio = Decimal('100')
        
        self.updated_at = datetime.utcnow()
        return self

    def mark_as_current(self) -> 'AdjustedCapital':
        """Marque cette entrée comme la version actuelle"""
        # Désactiver les autres versions courantes pour cette pharmacie
        from sqlalchemy.orm import Session
        # Cette partie doit être exécutée avec une session
        self.is_current = True
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Convertit le modèle en dictionnaire"""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "pharmacy_id": str(self.pharmacy_id) if self.pharmacy_id else None,
            "branch_id": str(self.branch_id) if self.branch_id else None,
            "capital_id": str(self.capital_id) if self.capital_id else None,
            "cash_in_hand": float(self.cash_in_hand),
            "bank_balance": float(self.bank_balance),
            "total_liquidities": float(self.total_liquidities),
            "stock_value": float(self.stock_value),
            "equipment_value": float(self.equipment_value),
            "other_assets": float(self.other_assets),
            "total_supplier_debt": float(self.total_supplier_debt),
            "other_liabilities": float(self.other_liabilities),
            "gross_capital": float(self.gross_capital),
            "adjusted_capital": float(self.adjusted_capital),
            "equity_capital": float(self.equity_capital),
            "debt_ratio": float(self.debt_ratio),
            "liquidity_ratio": float(self.liquidity_ratio),
            "total_assets": float(self.total_assets),
            "total_liabilities": float(self.total_liabilities),
            "net_liquidities": float(self.net_liquidities),
            "working_capital": float(self.working_capital),
            "calculation_date": self.calculation_date.isoformat() if self.calculation_date else None,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "is_current": self.is_current,
            "calculation_method": self.calculation_method,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "calculated_by": str(self.calculated_by) if self.calculated_by else None,
        }

    def __repr__(self) -> str:
        return f"<AdjustedCapital {self.pharmacy_id}: Gross={self.gross_capital}, Adjusted={self.adjusted_capital}, Date={self.calculation_date}>"


# =====================================
# FONCTIONS UTILITAIRES
# =====================================

def get_account_codes() -> Dict[str, Dict[str, str]]:
    """
    Retourne la liste des comptes SYSCOHADA standards pour une pharmacie.
    
    Returns:
        Dict avec les codes et noms des comptes par catégorie
    """
    return {
        "assets": {
            "31": "Stocks",
            "53": "Caisse",
            "57": "Banque",
            "58": "Chèques postaux",
            "23": "Immobilisations corporelles",
            "28": "Amortissements",
        },
        "liabilities": {
            "40": "Fournisseurs",
            "44": "État - TVA",
            "45": "Personnel",
            "47": "Dettes diverses",
        },
        "equity": {
            "101": "Capital social",
            "102": "Capital personnel",
            "106": "Réserves",
            "12": "Report à nouveau",
            "13": "Résultat de l'exercice",
        },
        "income": {
            "701": "Ventes de marchandises",
            "706": "Prestations de services",
            "76": "Produits financiers",
            "79": "Transferts de charges",
        },
        "expense": {
            "601": "Achats de marchandises",
            "62": "Services extérieurs",
            "64": "Charges de personnel",
            "68": "Dotations aux amortissements",
            "69": "Impôts et taxes",
        },
    }


def get_default_accounts(tenant_id: UUID, pharmacy_id: UUID, year: int, branch_id: UUID = None) -> List[CapitalAccount]:
    """
    Crée les comptes par défaut pour une pharmacie.
    
    Args:
        tenant_id: ID du tenant
        pharmacy_id: ID de la pharmacie
        year: Année comptable
        branch_id: ID de la succursale (optionnel)
    
    Returns:
        Liste des comptes CapitalAccount
    """
    accounts = []
    account_codes = get_account_codes()
    
    for account_type, accounts_dict in account_codes.items():
        for code, name in accounts_dict.items():
            account = CapitalAccount(
                tenant_id=tenant_id,
                pharmacy_id=pharmacy_id,
                branch_id=branch_id,
                account_code=code,
                account_name=name,
                account_type=account_type,
                balance=Decimal('0'),
                debit=Decimal('0'),
                credit=Decimal('0'),
                period_year=year,
                period_month=None,
            )
            accounts.append(account)
    
    return accounts
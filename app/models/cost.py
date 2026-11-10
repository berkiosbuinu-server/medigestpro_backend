# app/models/cost.py
import uuid
from datetime import datetime, date
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Optional, Dict, Any

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, 
    Text, Date, Index, DECIMAL, Integer, CheckConstraint
)
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func

from app.db.base import Base

# =====================================
# ENUMS
# =====================================
class CostCategory(str, PyEnum):
    SALAIRE = "salaire"
    LOYER = "loyer"
    UTILITIES = "utilities"
    MAINTENANCE = "maintenance"
    FOURNITURES = "fournitures"
    MARKETING = "marketing"
    LOGICIEL = "logiciel"
    ASSURANCE = "assurance"
    TRANSPORT = "transport"
    FORMATION = "formation"
    CONSULTATION = "consultation"
    IMPOTS = "impots"
    TELEPHONE = "telephone"
    INTERNET = "internet"
    ELECTRICITE = "electricite"
    EAU = "eau"
    PUBLICITE = "publicite"
    FRAIS_BANCAIRES = "frais_bancaires"
    AMORTISSEMENT = "amortissement"
    PROVISION = "provision"
    DIVERSE = "diverse"

class CostFrequency(str, PyEnum):
    UNIQUE = "unique"
    QUOTIDIEN = "quotidien"
    HEBDOMADAIRE = "hebdomadaire"
    MENSUEL = "mensuel"
    TRIMESTRIEL = "trimestriel"
    SEMESTRIEL = "semestriel"
    ANNUEL = "annuel"

class PaymentMethod(str, PyEnum):
    CASH = "cash"
    MOBILE_MONEY = "mobile_money"
    VIREMENT = "virement"
    CHEQUE = "cheque"
    CARTE = "carte"
    CREDIT = "credit"

class BudgetPeriod(str, PyEnum):
    JOURNALIER = "journalier"
    HEBDOMADAIRE = "hebdomadaire"
    MENSUEL = "mensuel"
    TRIMESTRIEL = "trimestriel"
    SEMESTRIEL = "semestriel"
    ANNUEL = "annuel"
    PERSONNALISE = "personnalise"

class AllocationStatus(str, PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    PARTIALLY_ALLOCATED = "partially_allocated"
    FULLY_ALLOCATED = "fully_allocated"


# =====================================
# MODÈLE COST (DÉPENSE)
# =====================================
class Cost(Base):
    """Modèle représentant une dépense ou coût"""
    __tablename__ = "costs"

    # =====================================
    # IDENTIFIANT
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    
    # =====================================
    # RÉFÉRENCE ET CLASSIFICATION
    # =====================================
    reference = Column(String(50), unique=True, nullable=False, index=True, 
                      comment="Référence unique de la dépense")
    
    category = Column(
        String(50),
        nullable=False, 
        default=CostCategory.DIVERSE.value
    )
    subcategory = Column(String(100), nullable=True)
    
    # =====================================
    # INFORMATIONS FINANCIÈRES
    # =====================================
    amount = Column(DECIMAL(15, 2), nullable=False)
    tax_amount = Column(DECIMAL(15, 2), default=0.0)
    total_amount = Column(DECIMAL(15, 2), nullable=False)
    currency = Column(String(3), default="CDF", nullable=False)
    exchange_rate = Column(DECIMAL(10, 4), default=1.0)
    
    # =====================================
    # PAIEMENT
    # =====================================
    payment_method = Column(String(20), default=PaymentMethod.CASH.value)
    payment_date = Column(Date, nullable=False, default=date.today)
    due_date = Column(Date, nullable=True)
    is_paid = Column(Boolean, default=True)
    
    # Détails paiement
    payment_reference = Column(String(100), nullable=True)
    bank_name = Column(String(100), nullable=True)
    account_number = Column(String(50), nullable=True)
    check_number = Column(String(50), nullable=True)
    
    # =====================================
    # RÉCURRENCE
    # =====================================
    frequency = Column(String(20), default=CostFrequency.UNIQUE.value)
    is_recurring = Column(Boolean, default=False)
    recurring_until = Column(Date, nullable=True)
    next_payment_date = Column(Date, nullable=True)
    
    # =====================================
    # FACTURATION ET FOURNISSEUR
    # =====================================
    invoice_number = Column(String(100), nullable=True)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    
    # =====================================
    # DESCRIPTION
    # =====================================
    description = Column(Text, nullable=True)
    justification = Column(Text, nullable=True, comment="Justification de la dépense")
    notes = Column(Text, nullable=True)
    tags = Column(JSON, default=list)
    attachments = Column(JSON, default=list, comment="Liste des pièces jointes")
    
    # =====================================
    # RESPONSABLE ET VALIDATION
    # =====================================
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # Statut de validation
    status = Column(
        String(20), 
        default="draft",
        comment="draft, submitted, approved, rejected, paid"
    )
    approval_date = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    
    # =====================================
    # BUDGET
    # =====================================
    budget_id = Column(UUID(as_uuid=True), ForeignKey("budgets.id"), nullable=True)
    is_budgeted = Column(Boolean, default=False)
    budget_variance = Column(DECIMAL(15, 2), default=0.0, 
                           comment="Différence entre budget alloué et dépense réelle")
    
    # =====================================
    # DÉPARTEMENT ET PROJET
    # =====================================
    department_id = Column(UUID(as_uuid=True), ForeignKey("departments.id"), nullable=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=True)
    
    # =====================================
    # MÉTADONNÉES
    # =====================================
    cost_metadata = Column(JSON, default=dict)
    is_active = Column(Boolean, default=True)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # =====================================
    # RELATIONS
    # =====================================
    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", back_populates="costs")
    supplier = relationship("Supplier", back_populates="costs")
    creator = relationship("User", foreign_keys=[created_by], back_populates="costs_created")
    approver = relationship("User", foreign_keys=[approved_by], back_populates="costs_approved")
    budget = relationship("Budget", back_populates="costs")
    department = relationship("Department", back_populates="costs", lazy='select')
    project = relationship("Project", back_populates="costs", lazy='select')
    allocations = relationship("CostAllocation", back_populates="cost", cascade="all, delete-orphan")
        
    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index('ix_costs_tenant_category', 'tenant_id', 'category'),
        Index('ix_costs_tenant_date', 'tenant_id', 'payment_date'),
        Index('ix_costs_tenant_status', 'tenant_id', 'status'),
        Index('ix_costs_tenant_supplier', 'tenant_id', 'supplier_id'),
        Index('ix_costs_tenant_budget', 'tenant_id', 'budget_id'),
        Index('ix_costs_tenant_created_by', 'tenant_id', 'created_by'),
        Index('ix_costs_tenant_department', 'tenant_id', 'department_id'),
        Index('ix_costs_tenant_project', 'tenant_id', 'project_id'),
        CheckConstraint('amount >= 0', name='check_amount_positive'),
        CheckConstraint('total_amount >= 0', name='check_total_amount_positive'),
    )
    
    # =====================================
    # VALIDATIONS
    # =====================================
    @validates('amount', 'tax_amount', 'total_amount')
    def validate_amounts(self, key, value):
        if value < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return value
    
    @validates('payment_date', 'due_date')
    def validate_dates(self, key, value):
        if value and value > date.today() and key == 'payment_date':
            raise ValueError("La date de paiement ne peut pas être dans le futur")
        return value
    
    # =====================================
    # PROPRIÉTÉS
    # =====================================
    @property
    def days_overdue(self) -> int:
        if not self.is_paid and self.due_date:
            today = date.today()
            if today > self.due_date:
                return (today - self.due_date).days
        return 0
    
    @property
    def tax_percentage(self) -> float:
        if self.amount == 0:
            return 0.0
        return float((self.tax_amount / self.amount) * 100)
    
    @property
    def payment_status(self) -> str:
        if self.is_paid:
            return "paid"
        if self.due_date and date.today() > self.due_date:
            return "overdue"
        if self.due_date:
            return "pending"
        return "unplanned"
    
    @property
    def total_allocated(self) -> Decimal:
        """Montant total alloué à ce coût"""
        total = sum(a.allocated_amount for a in self.allocations if a.status == AllocationStatus.APPROVED.value)
        return Decimal(str(total))
    
    @property
    def remaining_to_allocate(self) -> Decimal:
        """Montant restant à allouer"""
        return self.total_amount - self.total_allocated
    
    @property
    def allocation_percentage(self) -> float:
        """Pourcentage alloué"""
        if self.total_amount == 0:
            return 0.0
        return float((self.total_allocated / self.total_amount) * 100)
    
    # =====================================
    # MÉTHODES
    # =====================================
    def calculate_totals(self) -> 'Cost':
        self.total_amount = self.amount + (self.tax_amount or 0)
        
        if self.budget and self.is_budgeted:
            allocated = self.budget.allocated_amount
            if allocated > 0:
                self.budget_variance = self.total_amount - allocated
        
        return self
    
    def mark_as_paid(self, user_id: uuid.UUID, payment_date: date = None) -> 'Cost':
        self.is_paid = True
        self.status = "paid"
        self.approved_by = user_id
        self.approval_date = datetime.utcnow()
        self.payment_date = payment_date or date.today()
        return self
    
    def submit_for_approval(self) -> 'Cost':
        self.status = "submitted"
        return self
    
    def approve(self, user_id: uuid.UUID) -> 'Cost':
        self.status = "approved"
        self.approved_by = user_id
        self.approval_date = datetime.utcnow()
        return self
    
    def reject(self, user_id: uuid.UUID, reason: str) -> 'Cost':
        self.status = "rejected"
        self.approved_by = user_id
        self.approval_date = datetime.utcnow()
        self.rejection_reason = reason
        return self
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "reference": self.reference,
            "category": self.category,
            "subcategory": self.subcategory,
            "amount": float(self.amount),
            "tax_amount": float(self.tax_amount),
            "total_amount": float(self.total_amount),
            "currency": self.currency,
            "payment_method": self.payment_method,
            "payment_date": self.payment_date.isoformat() if self.payment_date else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "is_paid": self.is_paid,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "payment_status": self.payment_status,
            "days_overdue": self.days_overdue,
            "total_allocated": float(self.total_allocated),
            "remaining_to_allocate": float(self.remaining_to_allocate),
            "allocation_percentage": self.allocation_percentage
        }
    
    def __repr__(self) -> str:
        return f"<Cost {self.reference} | {self.total_amount} {self.currency} | {self.category}>"


# =====================================
# MODÈLE COST ALLOCATION
# =====================================
class CostAllocation(Base):
    """
    Allocation des coûts entre différents départements, projets ou centres de coût.
    Permet de répartir un coût sur plusieurs entités.
    """
    __tablename__ = "cost_allocations"

    # =====================================
    # IDENTIFIANTS
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    cost_id = Column(UUID(as_uuid=True), ForeignKey("costs.id"), nullable=False, index=True)
    
    # =====================================
    # ENTITÉS D'ALLOCATION
    # =====================================
    department_id = Column(UUID(as_uuid=True), ForeignKey("departments.id"), nullable=True, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=True, index=True)
    
    # =====================================
    # MONTANTS
    # =====================================
    allocated_amount = Column(DECIMAL(15, 2), nullable=False, comment="Montant alloué")
    allocation_percentage = Column(DECIMAL(5, 2), nullable=True, comment="Pourcentage d'allocation (si répartition proportionnelle)")
    
    # =====================================
    # STATUT
    # =====================================
    status = Column(String(20), default=AllocationStatus.PENDING.value, 
                   comment="pending, approved, rejected, cancelled")
    
    # =====================================
    # DESCRIPTION
    # =====================================
    notes = Column(Text, nullable=True, comment="Justification de l'allocation")
    allocation_reference = Column(String(100), nullable=True, comment="Référence externe")
    
    # =====================================
    # APPROBATION
    # =====================================
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    
    # =====================================
    # PÉRIODE
    # =====================================
    allocation_date = Column(Date, nullable=False, default=date.today, comment="Date d'allocation")
    start_date = Column(Date, nullable=True, comment="Date de début de validité")
    end_date = Column(Date, nullable=True, comment="Date de fin de validité")
    
    # =====================================
    # MÉTADONNÉES
    # =====================================
    allocation_metadata = Column(JSON, default=dict, comment="Métadonnées supplémentaires")
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", back_populates="cost_allocations")
    cost = relationship("Cost", back_populates="allocations")
    department = relationship("Department", back_populates="cost_allocations")
    project = relationship("Project", back_populates="cost_allocations")
    approver = relationship("User", foreign_keys=[approved_by])
    creator = relationship("User", foreign_keys=[created_by])
    
    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index('ix_cost_allocations_cost', 'cost_id'),
        Index('ix_cost_allocations_department', 'department_id'),
        Index('ix_cost_allocations_project', 'project_id'),
        Index('ix_cost_allocations_status', 'status'),
        Index('ix_cost_allocations_date', 'allocation_date'),
        Index('ix_cost_allocations_tenant_cost', 'tenant_id', 'cost_id'),
        CheckConstraint('allocated_amount >= 0', name='check_allocated_amount_positive'),
        CheckConstraint('allocation_percentage IS NULL OR (allocation_percentage >= 0 AND allocation_percentage <= 100)', 
                       name='check_allocation_percentage_range'),
    )
    
    # =====================================
    # VALIDATIONS
    # =====================================
    @validates('allocated_amount')
    def validate_allocated_amount(self, key, value):
        if value < 0:
            raise ValueError("Le montant alloué ne peut pas être négatif")
        return value
    
    @validates('allocation_percentage')
    def validate_percentage(self, key, value):
        if value is not None and (value < 0 or value > 100):
            raise ValueError("Le pourcentage d'allocation doit être entre 0 et 100")
        return value
    
    @validates('status')
    def validate_status(self, key, value):
        allowed = ["pending", "approved", "rejected", "cancelled", "partially_allocated", "fully_allocated"]
        if value not in allowed:
            raise ValueError(f"Status doit être l'un des: {allowed}")
        return value
    
    # =====================================
    # PROPRIÉTÉS
    # =====================================
    @property
    def is_approved(self) -> bool:
        return self.status == AllocationStatus.APPROVED.value
    
    @property
    def is_pending(self) -> bool:
        return self.status == AllocationStatus.PENDING.value
    
    @property
    def is_active(self) -> bool:
        """Vérifie si l'allocation est active (approuvée et dans la période)"""
        if not self.is_approved:
            return False
        today = date.today()
        if self.start_date and today < self.start_date:
            return False
        if self.end_date and today > self.end_date:
            return False
        return True
    
    # =====================================
    # MÉTHODES
    # =====================================
    def approve(self, user_id: uuid.UUID) -> 'CostAllocation':
        """Approuve l'allocation"""
        self.status = AllocationStatus.APPROVED.value
        self.approved_by = user_id
        self.approved_at = datetime.utcnow()
        return self
    
    def reject(self, user_id: uuid.UUID, reason: str = None) -> 'CostAllocation':
        """Rejette l'allocation"""
        self.status = AllocationStatus.REJECTED.value
        self.approved_by = user_id
        self.approved_at = datetime.utcnow()
        if reason:
            self.notes = reason
        return self
    
    def cancel(self) -> 'CostAllocation':
        """Annule l'allocation"""
        self.status = AllocationStatus.CANCELLED.value
        return self
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "cost_id": str(self.cost_id),
            "department_id": str(self.department_id) if self.department_id else None,
            "project_id": str(self.project_id) if self.project_id else None,
            "allocated_amount": float(self.allocated_amount),
            "allocation_percentage": float(self.allocation_percentage) if self.allocation_percentage else None,
            "status": self.status,
            "notes": self.notes,
            "allocation_reference": self.allocation_reference,
            "allocation_date": self.allocation_date.isoformat() if self.allocation_date else None,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "is_approved": self.is_approved,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": str(self.created_by) if self.created_by else None,
            "approved_by": str(self.approved_by) if self.approved_by else None,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
        }
    
    def __repr__(self) -> str:
        return f"<CostAllocation {self.id} | Cost: {self.cost_id} | Amount: {self.allocated_amount}>"


# =====================================
# MODÈLE BUDGET
# =====================================
class Budget(Base):
    """Modèle représentant un budget"""
    __tablename__ = "budgets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    
    # Informations générales
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    
    # Catégorie
    category = Column(String(50), nullable=False)
    subcategory = Column(String(100), nullable=True)
    
    # Période
    period_type = Column(String(20), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    
    # Montants
    allocated_amount = Column(DECIMAL(15, 2), nullable=False)
    spent_amount = Column(DECIMAL(15, 2), default=0.0)
    committed_amount = Column(DECIMAL(15, 2), default=0.0, comment="Montant engagé mais non encore payé")
    remaining_amount = Column(DECIMAL(15, 2), default=0.0)
    
    # Seuils d'alerte
    warning_threshold = Column(DECIMAL(5, 2), default=80.0, comment="Seuil d'avertissement en %")
    critical_threshold = Column(DECIMAL(5, 2), default=95.0, comment="Seuil critique en %")
    
    # Département/Projet
    department_id = Column(UUID(as_uuid=True), ForeignKey("departments.id"), nullable=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=True)
    
    # Responsable
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Statut
    status = Column(String(20), default="active", comment="active, closed, cancelled")
    is_active = Column(Boolean, default=True)
    
    # Métadonnées
    notes = Column(Text, nullable=True)
    budget_metadata = Column(JSON, default=dict)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    closed_at = Column(DateTime, nullable=True)
    
    # Relations
    tenant = relationship("Tenant", back_populates="budgets")
    owner = relationship("User", foreign_keys=[owner_id], back_populates="budgets_owned")
    costs = relationship("Cost", back_populates="budget")
    department = relationship("Department", back_populates="budgets")
    project = relationship("Project", back_populates="budgets")
    
    # Indexes
    __table_args__ = (
        Index('ix_budgets_tenant_period', 'tenant_id', 'start_date', 'end_date'),
        Index('ix_budgets_tenant_category', 'tenant_id', 'category'),
        Index('ix_budgets_tenant_status', 'tenant_id', 'status'),
        Index('ix_budgets_department', 'department_id'),
        Index('ix_budgets_project', 'project_id'),
        CheckConstraint('allocated_amount >= 0', name='check_budget_amount_positive'),
        CheckConstraint('start_date <= end_date', name='check_budget_dates'),
    )
    
    # Propriétés
    @property
    def spending_percentage(self) -> float:
        if self.allocated_amount == 0:
            return 0.0
        return float((self.spent_amount / self.allocated_amount) * 100)
    
    @property
    def commitment_percentage(self) -> float:
        if self.allocated_amount == 0:
            return 0.0
        total_committed = self.spent_amount + self.committed_amount
        return float((total_committed / self.allocated_amount) * 100)
    
    @property
    def days_remaining(self) -> int:
        today = date.today()
        if today > self.end_date:
            return 0
        return (self.end_date - today).days
    
    @property
    def alert_level(self) -> str:
        percentage = self.spending_percentage
        if percentage >= self.critical_threshold:
            return "critical"
        elif percentage >= self.warning_threshold:
            return "warning"
        return "normal"
    
    # Méthodes
    def update_spent_amount(self, db_session) -> 'Budget':
        """Met à jour les montants dépensés et engagés"""
        from sqlalchemy import func
        
        spent_result = db_session.query(func.sum(Cost.total_amount)).filter(
            Cost.tenant_id == self.tenant_id,
            Cost.budget_id == self.id,
            Cost.is_paid == True,
            Cost.status == 'paid'
        ).scalar()
        self.spent_amount = spent_result or Decimal('0')
        
        committed_result = db_session.query(func.sum(Cost.total_amount)).filter(
            Cost.tenant_id == self.tenant_id,
            Cost.budget_id == self.id,
            Cost.is_paid == False,
            Cost.status.in_(['approved', 'submitted'])
        ).scalar()
        self.committed_amount = committed_result or Decimal('0')
        
        self.remaining_amount = self.allocated_amount - self.spent_amount
        
        return self
    
    def close_budget(self) -> 'Budget':
        self.status = "closed"
        self.closed_at = datetime.utcnow()
        self.is_active = False
        return self
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "code": self.code,
            "category": self.category,
            "period_type": self.period_type,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "allocated_amount": float(self.allocated_amount),
            "spent_amount": float(self.spent_amount),
            "committed_amount": float(self.committed_amount),
            "remaining_amount": float(self.remaining_amount),
            "spending_percentage": self.spending_percentage,
            "commitment_percentage": self.commitment_percentage,
            "alert_level": self.alert_level,
            "days_remaining": self.days_remaining,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
    
    def __repr__(self) -> str:
        return f"<Budget {self.code} | {self.name} | {self.allocated_amount} {self.category}>"


# =====================================
# MODÈLE SUPPLIER (FOURNISSEUR)
# =====================================
class Supplier(Base):
    """Modèle représentant un fournisseur"""
    __tablename__ = "suppliers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    
    # =====================================
    # IDENTIFICATION
    # =====================================
    code = Column(String(50), unique=True, nullable=False, index=True, comment="Code unique du fournisseur")
    name = Column(String(200), nullable=False)
    company_name = Column(String(200), nullable=True)
    type_supplier = Column(String(30), default="company", comment="company, individual, government")
    
    # =====================================
    # INFORMATIONS LÉGALES
    # =====================================
    tax_id = Column(String(50), nullable=True, comment="Numéro de contribuable")
    rccm = Column(String(50), nullable=True, comment="Registre de commerce")
    id_nat = Column(String(50), nullable=True, comment="Numéro d'identification nationale")
    
    # =====================================
    # CONTACT
    # =====================================
    email = Column(String(200), nullable=True)
    phone = Column(String(50), nullable=True)
    phone_secondary = Column(String(50), nullable=True)
    fax = Column(String(50), nullable=True)
    
    # =====================================
    # ADRESSE
    # =====================================
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    province = Column(String(100), nullable=True)
    country = Column(String(100), default="RDC")
    postal_code = Column(String(20), nullable=True)
    
    # =====================================
    # INFORMATIONS BANCAIRES
    # =====================================
    bank_name = Column(String(100), nullable=True)
    bank_account = Column(String(100), nullable=True)
    bank_swift = Column(String(20), nullable=True)
    payment_terms = Column(String(200), nullable=True, default="30 days")
    
    # =====================================
    # SPÉCIALISATION
    # =====================================
    categories = Column(JSON, default=list, comment="Catégories de produits/services fournis")
    specialities = Column(JSON, default=list, comment="Spécialités")
    
    # =====================================
    # ÉVALUATION
    # =====================================
    rating = Column(DECIMAL(3, 2), default=0.0, comment="Note sur 5")
    rating_count = Column(Integer, default=0)
    reliability_score = Column(DECIMAL(5, 2), default=0.0, comment="Score de fiabilité")
    delivery_score = Column(DECIMAL(5, 2), default=0.0, comment="Score de livraison")
    quality_score = Column(DECIMAL(5, 2), default=0.0, comment="Score de qualité")
    
    # =====================================
    # STATISTIQUES
    # =====================================
    total_transactions = Column(Integer, default=0)
    total_amount = Column(DECIMAL(15, 2), default=0.0)
    average_delivery_time = Column(Integer, nullable=True, comment="Temps de livraison moyen en jours")
    
    # =====================================
    # STATUT
    # =====================================
    status = Column(String(20), default="active", comment="active, inactive, blacklisted, pending")
    is_preferred = Column(Boolean, default=False, comment="Fournisseur préféré")
    is_blacklisted = Column(Boolean, default=False)
    blacklist_reason = Column(Text, nullable=True)
    
    # =====================================
    # MÉTADONNÉES
    # =====================================
    website = Column(String(200), nullable=True)
    contact_person = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    supplier_metadata = Column(JSON, default=dict)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_order_date = Column(Date, nullable=True)
    
    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", back_populates="suppliers")
    costs = relationship("Cost", back_populates="supplier")
    purchases = relationship("Purchase", back_populates="supplier", overlaps="supplier_purchases") 
    credit_configs = relationship("SupplierCreditConfig", back_populates="supplier", 
                              cascade="all, delete-orphan")
    debt = relationship("SupplierDebt", back_populates="supplier", uselist=False)
    purchase_credits = relationship("PurchaseCredit", back_populates="supplier")
    credit_transactions = relationship("SupplierCreditTransaction", back_populates="supplier")
    
    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index('ix_suppliers_tenant_name', 'tenant_id', 'name'),
        Index('ix_suppliers_tenant_code', 'tenant_id', 'code'),
        Index('ix_suppliers_tenant_status', 'tenant_id', 'status'),
        Index('ix_suppliers_tenant_rating', 'tenant_id', 'rating'),
        Index('ix_suppliers_tenant_type', 'tenant_id', 'type_supplier'),
    )
    
    # =====================================
    # VALIDATIONS
    # =====================================
    @validates('email')
    def validate_email(self, key, email):
        if email and '@' not in email:
            raise ValueError("Format d'email invalide")
        return email
    
    @validates('rating')
    def validate_rating(self, key, rating):
        if rating < 0 or rating > 5:
            raise ValueError("La note doit être entre 0 et 5")
        return rating
    
    # =====================================
    # PROPRIÉTÉS
    # =====================================
    @property
    def full_address(self) -> str:
        parts = []
        if self.address:
            parts.append(self.address)
        if self.city:
            parts.append(self.city)
        if self.province:
            parts.append(self.province)
        if self.country:
            parts.append(self.country)
        return ", ".join(filter(None, parts))
    
    @property
    def overall_score(self) -> float:
        weights = {'reliability': 0.4, 'delivery': 0.3, 'quality': 0.3}
        total = (
            self.reliability_score * weights['reliability'] +
            self.delivery_score * weights['delivery'] +
            self.quality_score * weights['quality']
        )
        return float(total)
    
    @property
    def days_since_last_order(self) -> Optional[int]:
        if self.last_order_date:
            return (date.today() - self.last_order_date).days
        return None
    
    # =====================================
    # MÉTHODES
    # =====================================
    def update_rating(self, new_rating: float, reliability: float = None, 
                     delivery: float = None, quality: float = None) -> 'Supplier':
        self.rating_count += 1
        current_total = self.rating * (self.rating_count - 1)
        self.rating = (current_total + new_rating) / self.rating_count
        
        if reliability is not None:
            self.reliability_score = reliability
        if delivery is not None:
            self.delivery_score = delivery
        if quality is not None:
            self.quality_score = quality
            
        return self
    
    def blacklist(self, reason: str) -> 'Supplier':
        self.is_blacklisted = True
        self.status = "blacklisted"
        self.blacklist_reason = reason
        return self
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "code": self.code,
            "name": self.name,
            "company_name": self.company_name,
            "type_supplier": self.type_supplier,
            "email": self.email,
            "phone": self.phone,
            "address": self.address,
            "full_address": self.full_address,
            "tax_id": self.tax_id,
            "status": self.status,
            "rating": float(self.rating),
            "overall_score": self.overall_score,
            "is_preferred": self.is_preferred,
            "is_blacklisted": self.is_blacklisted,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
    
    def __repr__(self) -> str:
        return f"<Supplier {self.code} | {self.name}>"
# app/models/customer.py
import uuid
from datetime import datetime
import sqlalchemy as sa
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Text, 
    Date, Integer, DECIMAL, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship

from app.db.base import Base


class Customer(Base):
    """Modèle client unifié (particuliers et professionnels)"""
    __tablename__ = "customers"

    # =======================
    # Identité de base
    # =======================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=True)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True)

    # Nom complet (format flexible)
    nom = Column(String(100), nullable=False)
    prenom = Column(String(100), nullable=True)
    
    # Contact
    telephone = Column(String(20), nullable=False)
    email = Column(String(255), nullable=True)
    adresse = Column(Text, nullable=True)
    ville = Column(String(100), nullable=True)
    code_postal = Column(String(20), nullable=True)
    pays = Column(String(100), default="RDC")

    # =======================
    # Type et catégorie
    # =======================
    type_client = Column(
        String(20),
        default="particulier",
        comment="particulier, professionnel, assureur, etat, hopital, clinique"
    )
    
    # Catégorie client (fidélité)
    category = Column(String(20), default="standard", comment="standard, premium, vip")

    # =======================
    # Informations légales (pro)
    # =======================
    entreprise = Column(String(100), nullable=True)
    num_contribuable = Column(String(50), nullable=True)
    rccm = Column(String(50), nullable=True)
    id_nat = Column(String(50), nullable=True)

    # =======================
    # Informations médicales
    # =======================
    birth_date = Column(Date, nullable=True)
    blood_type = Column(String(5), nullable=True)
    allergies = Column(Text, nullable=True)
    medical_notes = Column(Text, nullable=True)
    
    # Assurance
    insurance_provider = Column(String(255), nullable=True)
    insurance_number = Column(String(100), nullable=True)

    # =======================
    # Crédit & statistiques
    # =======================
    credit_limit = Column(DECIMAL(15, 2), default=0)
    eligible_credit = Column(Boolean, default=False)
    dette_actuelle = Column(DECIMAL(15, 2), default=0)
    credit_score = Column(Integer, default=100)

    total_achats = Column(DECIMAL(15, 2), default=0)
    nombre_achats = Column(Integer, default=0)
    moyenne_achat = Column(DECIMAL(15, 2), default=0)
    
    # Fidélité
    loyalty_points = Column(Integer, default=0)

    # =======================
    # Suivi
    # =======================
    date_inscription = Column(DateTime, default=datetime.utcnow)
    dernier_achat = Column(DateTime, nullable=True)
    date_dernier_paiement = Column(DateTime, nullable=True)
    last_visit = Column(DateTime, nullable=True)
    
    notes = Column(Text, nullable=True)
    preferences = Column(JSON, default=dict)
    customer_metadata = Column("metadata", JSON, default=dict)

    # =======================
    # Statut
    # =======================
    is_active = Column(Boolean, default=True)
    blacklisted = Column(Boolean, default=False)
    blacklist_reason = Column(Text, nullable=True)

    # Audit
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # =======================
    # Relations
    # =======================
    tenant = relationship("Tenant", back_populates="customers")
    pharmacy = relationship("Pharmacy", back_populates="customers")
    branch = relationship("Branch", back_populates="customers")
    creator = relationship("User", foreign_keys=[created_by])
    
    sales = relationship("Sale", back_populates="customer", foreign_keys="Sale.customer_id")
    debts = relationship("Debt", back_populates="customer", cascade="all, delete-orphan")
    debt_payments = relationship("DebtPayment", back_populates="customer")
    orders = relationship("Order", back_populates="customer", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="customer")

    # =======================
    # Indexes
    # =======================
    __table_args__ = (
        Index("ix_customers_tenant_nom", "tenant_id", "nom"),
        Index("ix_customers_tenant_phone", "tenant_id", "telephone"),
        Index("ix_customers_tenant_email", "tenant_id", "email"),
        Index("ix_customers_type", "tenant_id", "type_client"),
        Index("ix_customers_category", "tenant_id", "category"),
        Index("ix_customers_credit_status", "tenant_id", "eligible_credit", "blacklisted"),
        Index("ix_customers_last_purchase", "tenant_id", "dernier_achat"),
        Index("ix_customers_insurance", "tenant_id", "insurance_provider"),
    )

    # =======================
    # Propriétés métier
    # =======================
    @property
    def full_name(self):
        if self.prenom:
            return f"{self.prenom} {self.nom}"
        return self.nom

    @property
    def credit_available(self):
        return max(0, float(self.credit_limit - self.dette_actuelle))

    @property
    def days_since_last_purchase(self):
        if self.dernier_achat:
            return (datetime.utcnow() - self.dernier_achat).days
        return None

    @property
    def credit_utilization(self):
        if self.credit_limit > 0:
            return (float(self.dette_actuelle) / float(self.credit_limit)) * 100
        return 0

    @property
    def credit_status(self):
        utilization = self.credit_utilization
        if utilization > 90:
            return "critical"
        elif utilization > 70:
            return "warning"
        elif self.dette_actuelle == 0:
            return "clean"
        return "normal"

    # =======================
    # Méthodes métier
    # =======================
    def update_stats(self, sale_amount):
        self.total_achats += sale_amount
        self.nombre_achats += 1
        self.moyenne_achat = self.total_achats / self.nombre_achats
        self.dernier_achat = datetime.utcnow()
        self.last_visit = datetime.utcnow()
        return self

    def add_debt(self, amount):
        self.dette_actuelle += amount
        if self.dette_actuelle > self.credit_limit:
            self.eligible_credit = False
        return self

    def pay_debt(self, amount):
        self.dette_actuelle = max(0, self.dette_actuelle - amount)
        self.date_dernier_paiement = datetime.utcnow()
        if self.dette_actuelle <= self.credit_limit * 0.8:
            self.eligible_credit = True
        return self

    def add_loyalty_points(self, points):
        self.loyalty_points += points
        # Mise à jour de la catégorie basée sur les points
        if self.loyalty_points >= 1000:
            self.category = "vip"
        elif self.loyalty_points >= 500:
            self.category = "premium"
        return self

    def __repr__(self):
        return f"<Customer {self.full_name} ({self.telephone})>"
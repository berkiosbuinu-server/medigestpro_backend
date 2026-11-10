# app/models/branch_subscription.py
"""
Modèle pour les abonnements liés à une branche.
Chaque branche a son propre abonnement.
Les utilisateurs de la branche bénéficient de cet abonnement.
"""

import uuid
from datetime import datetime, timedelta
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Integer, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum

from app.db.base import Base


class SubscriptionPlan(str, enum.Enum):
    """Plans d'abonnement disponibles - TOUT EN MAJUSCULE"""
    TRIAL = "TRIAL"
    STARTER = "STARTER"
    PROFESSIONAL = "PROFESSIONAL"
    ENTERPRISE = "ENTERPRISE"
    INFINITE = "INFINITE"
    
    @classmethod
    def _missing_(cls, value):
        """Gère les valeurs en minuscule en les convertissant en majuscule"""
        if isinstance(value, str):
            value_upper = value.upper()
            for member in cls:
                if member.value == value_upper:
                    return member
        return None


class SubscriptionStatus(str, enum.Enum):
    """Statuts d'abonnement"""
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"
    CANCELLED = "CANCELLED"
    TRIAL = "TRIAL"


class BranchSubscription(Base):
    """
    Abonnement d'une branche.
    Une branche = un abonnement.
    Tous les utilisateurs de la branche partagent cet abonnement.
    """
    __tablename__ = "branch_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Lien avec la branche (unique - une branche = un abonnement)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    
    # Pour compatibilité et requêtes rapides
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=True, index=True)
    
    # Plan (stocké en majuscule)
    plan = Column(String(50), nullable=False, default="TRIAL")
    plan_name = Column(String(100), nullable=False)
    
    # Période
    start_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    end_date = Column(DateTime, nullable=False)
    trial_end_date = Column(DateTime, nullable=True)
    
    # Statut (stocké en majuscule)
    status = Column(String(20), nullable=False, default="TRIAL")
    
    # Paiement
    billing_cycle = Column(String(20), nullable=False, default="monthly")
    price = Column(Float, nullable=False, default=0.0)
    currency = Column(String(3), default="EUR")
    auto_renew = Column(Boolean, default=True)
    
    # Limites
    max_products = Column(Integer, nullable=False, default=100)
    max_users = Column(Integer, nullable=False, default=5)
    max_storage_mb = Column(Integer, nullable=False, default=100)
    
    # Métadonnées
    payment_id = Column(UUID(as_uuid=True), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancelled_reason = Column(String(500), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    branch = relationship("Branch", back_populates="branch_subscription", foreign_keys="[Branch.subscription_id]")
    tenant = relationship("Tenant")
    pharmacy = relationship("Pharmacy")

    # =========================
    # PROPRIÉTÉS ET MÉTHODES
    # =========================
    
    @property
    def plan_enum(self) -> SubscriptionPlan:
        """Retourne le plan comme énumération"""
        try:
            return SubscriptionPlan(self.plan.upper())
        except ValueError:
            return SubscriptionPlan.TRIAL
    
    @property
    def status_enum(self) -> SubscriptionStatus:
        """Retourne le statut comme énumération"""
        try:
            return SubscriptionStatus(self.status.upper())
        except ValueError:
            return SubscriptionStatus.TRIAL

    def is_active(self) -> bool:
        """Vérifie si l'abonnement est actif"""
        status_active = self.status_enum in [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL]
        if not status_active:
            return False
        if self.end_date and self.end_date < datetime.utcnow():
            return False
        return True

    def is_trial(self) -> bool:
        """Vérifie si l'abonnement est en période d'essai"""
        if self.status_enum != SubscriptionStatus.TRIAL:
            return False
        if self.end_date and self.end_date < datetime.utcnow():
            return False
        return True

    def days_remaining(self) -> int:
        """Jours restants avant expiration"""
        if not self.end_date:
            return 365
        return max(0, (self.end_date - datetime.utcnow()).days)

    def trial_days_remaining(self) -> int:
        """Jours d'essai restants"""
        if not self.end_date:
            return 0
        if self.status_enum != SubscriptionStatus.TRIAL:
            return 0
        return max(0, (self.end_date - datetime.utcnow()).days)

    def renew(self, new_end_date: datetime = None):
        """Renouvelle l'abonnement"""
        self.start_date = datetime.utcnow()
        if new_end_date:
            self.end_date = new_end_date
        elif self.billing_cycle == "yearly":
            self.end_date = datetime.utcnow() + timedelta(days=365)
        else:
            self.end_date = datetime.utcnow() + timedelta(days=30)
        self.status = SubscriptionStatus.ACTIVE.value
        self.updated_at = datetime.utcnow()

    def upgrade(self, new_plan: str, plan_config: dict):
        """Met à niveau l'abonnement"""
        self.plan = new_plan.upper()
        self.plan_name = plan_config["name"]
        self.max_products = plan_config.get("max_products", 100)
        self.max_users = plan_config.get("max_users", 5)
        self.max_storage_mb = plan_config.get("max_storage_mb", 100)
        self.updated_at = datetime.utcnow()

    def __repr__(self):
        return f"<BranchSubscription branch={self.branch_id} plan={self.plan} status={self.status}>"
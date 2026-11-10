import uuid
from datetime import datetime, timedelta
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Integer, Float, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum

from app.db.base import Base

class SubscriptionPlan(str, enum.Enum):
    TRIAL = "trial"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"
    INFINITE = "infinite"

class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"

class PharmacySubscription(Base):
    __tablename__ = "pharmacy_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    
    # Plan
    plan = Column(SQLEnum(SubscriptionPlan), nullable=False, default=SubscriptionPlan.TRIAL)
    plan_name = Column(String(100), nullable=False)
    
    # Période
    start_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    end_date = Column(DateTime, nullable=False)
    trial_end_date = Column(DateTime, nullable=True)
    
    # Statut
    status = Column(SQLEnum(SubscriptionStatus), nullable=False, default=SubscriptionStatus.ACTIVE)
    
    # Paiement
    billing_cycle = Column(String(20), nullable=False, default="monthly")  # monthly, yearly
    price = Column(Float, nullable=False, default=0.0)
    currency = Column(String(3), default="EUR")
    auto_renew = Column(Boolean, default=True)
    
    # Limites (dénormalisées depuis le plan)
    max_products = Column(Integer, nullable=False)
    max_users = Column(Integer, nullable=False)
    max_branches = Column(Integer, default=0, comment="0 = illimité")
    
    # Métadonnées
    payment_id = Column(UUID(as_uuid=True), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    pharmacy = relationship(
        "Pharmacy", 
        back_populates="subscription",
        foreign_keys="[Pharmacy.subscription_id]"
    )

    def is_active(self) -> bool:
        if self.status != SubscriptionStatus.ACTIVE:
            return False
        if self.end_date and self.end_date < datetime.utcnow():
            return False
        return True

    def days_remaining(self) -> int:
        if not self.end_date:
            return 365
        return max(0, (self.end_date - datetime.utcnow()).days)

    def renew(self, new_end_date: datetime = None):
        self.start_date = datetime.utcnow()
        if new_end_date:
            self.end_date = new_end_date
        elif self.billing_cycle == "yearly":
            self.end_date = datetime.utcnow() + timedelta(days=365)
        else:
            self.end_date = datetime.utcnow() + timedelta(days=30)
        self.status = SubscriptionStatus.ACTIVE
        self.updated_at = datetime.utcnow()
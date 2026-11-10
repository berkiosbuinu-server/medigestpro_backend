# app/models/user_subscription.py
import uuid
from datetime import datetime, timedelta
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Integer, Float, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class UserSubscription(Base):
    """
    Abonnement individuel pour chaque utilisateur
    """
    __tablename__ = "user_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Informations de l'abonnement
    plan_type = Column(String(50), nullable=False, default="trial")  # trial, starter, professional, enterprise
    plan_name = Column(String(100), nullable=False, default="Essai gratuit")
    
    # Période
    start_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    end_date = Column(DateTime, nullable=True)  # Null = illimité (enterprise)
    trial_end_date = Column(DateTime, nullable=True)  # Date de fin d'essai
    
    # Statut
    status = Column(String(20), nullable=False, default="active")  # active, expired, cancelled, suspended
    
    # Paiement
    price = Column(Float, default=0.0)
    currency = Column(String(3), default="EUR")
    billing_cycle = Column(String(20), default="monthly")  # monthly, yearly, one_time
    
    # Configuration des limites (basées sur le plan)
    max_users = Column(Integer, nullable=False, default=1)  # L'utilisateur ne peut créer que selon son plan
    max_products = Column(Integer, nullable=False, default=100)
    max_pharmacies = Column(Integer, nullable=False, default=1)  # Pour l'admin qui crée des pharmacies
    
    # Métadonnées
    payment_id = Column(UUID(as_uuid=True), ForeignKey("payments.id"), nullable=True)
    auto_renew = Column(Boolean, default=True)
    cancelled_at = Column(DateTime, nullable=True)
    
    # Configuration supplémentaire
    config = Column(JSON, nullable=True, default=dict)
    
    # Relations
    #user = relationship("User", back_populates="user_subscription")
    tenant = relationship("Tenant")
    payment = relationship("Payment")
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Définir trial_end_date si c'est un essai
        if self.plan_type == "trial" and not self.trial_end_date:
            self.trial_end_date = self.start_date + timedelta(days=14)
            self.end_date = self.trial_end_date

    def is_active(self) -> bool:
        """Vérifie si l'abonnement est actif"""
        if self.status != "active":
            return False
        if self.end_date and self.end_date < datetime.utcnow():
            return False
        return True

    def is_trial(self) -> bool:
        """Vérifie si l'utilisateur est en période d'essai"""
        return self.plan_type == "trial" and self.trial_end_date and self.trial_end_date > datetime.utcnow()

    def days_remaining(self) -> int:
        """Nombre de jours restants avant expiration"""
        if not self.end_date:
            return 365  # Illimité
        remaining = (self.end_date - datetime.utcnow()).days
        return max(0, remaining)

    def has_expired(self) -> bool:
        """Vérifie si l'abonnement a expiré"""
        return self.end_date and self.end_date < datetime.utcnow()

    def get_mode(self) -> str:
        """Retourne le mode d'accès (FULL ou READ_ONLY)"""
        if self.is_active():
            return "FULL"
        return "READ_ONLY"
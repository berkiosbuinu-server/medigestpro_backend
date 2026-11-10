# app/models/subscription_code.py
"""
Modèle pour les codes d'activation d'abonnement.
Un code est lié à une branche spécifique.
"""

import uuid
from datetime import datetime, timedelta
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Integer, Float, Text, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum

from app.db.base import Base


class SubscriptionCodeStatus(str, enum.Enum):
    """Statuts des codes d'abonnement"""
    PENDING = "pending"
    ACTIVATED = "activated"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SubscriptionCode(Base):
    """
    Code d'activation d'abonnement.
    Peut être lié à une branche spécifique ou générique.
    """
    __tablename__ = "subscription_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Le code lui-même (ex: ABCD-1234)
    code = Column(String(50), unique=True, nullable=False, index=True)
    
    # ✅ Lien avec la branche (optionnel - si None, code générique)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)
    
    # Pour compatibilité (ancien système)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True)
    
    # Plan
    plan_type = Column(String(50), nullable=False)
    plan_name = Column(String(100), nullable=False)
    duration_days = Column(Integer, nullable=False, default=30)
    
    # Prix (en cents pour éviter les problèmes de flottants)
    price = Column(Integer, nullable=False, default=0)
    currency = Column(String(3), default="EUR")
    
    # Validité du code
    valid_from = Column(DateTime, nullable=False, default=datetime.utcnow)
    valid_until = Column(DateTime, nullable=False)
    
    # Statut
    status = Column(SQLEnum(SubscriptionCodeStatus), nullable=False, default=SubscriptionCodeStatus.PENDING)
    
    # Métadonnées
    notes = Column(Text, nullable=True)
    
    # Audit
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    activated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    activated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    branch = relationship("Branch", foreign_keys=[branch_id])
    pharmacy = relationship("Pharmacy", foreign_keys=[pharmacy_id])
    created_by_user = relationship("User", foreign_keys=[created_by_user_id])
    activated_by_user = relationship("User", foreign_keys=[activated_by_user_id])

    def is_valid(self) -> bool:
        """Vérifie si le code est valide et peut être utilisé"""
        if self.status != SubscriptionCodeStatus.PENDING:
            return False
        now = datetime.utcnow()
        if self.valid_until and now > self.valid_until:
            return False
        if self.valid_from and now < self.valid_from:
            return False
        return True

    def days_remaining(self) -> int:
        """Jours restants avant expiration du code"""
        if not self.valid_until:
            return 0
        return max(0, (self.valid_until - datetime.utcnow()).days)

    def activate(self, user_id: uuid.UUID):
        """Active le code"""
        self.status = SubscriptionCodeStatus.ACTIVATED
        self.activated_by_user_id = user_id
        self.activated_at = datetime.utcnow()

    def __repr__(self):
        return f"<SubscriptionCode {self.code} plan={self.plan_type} status={self.status.value}>"
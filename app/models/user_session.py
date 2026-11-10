# app/models/user_session.py
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.db.base import Base


class UserSession(Base):
    """Modèle pour suivre les sessions utilisateur multi-plateformes"""
    __tablename__ = "user_sessions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    # Pharmacie et branche actives pendant la session
    active_pharmacy_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("pharmacies.id", ondelete="SET NULL"),
        nullable=True
    )
    active_branch_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True
    )
    
    # Identifiants de session
    session_id = Column(String(255), unique=True, nullable=False)
    refresh_token = Column(Text, nullable=True)
    
    # Informations plateforme
    platform = Column(String(50), nullable=False)  # web, mobile, pos, tablet
    device_type = Column(String(50))  # desktop, mobile, tablet, pos_terminal
    device_name = Column(String(255))  # iPhone 14, Samsung Galaxy, etc.
    browser = Column(String(100))  # Chrome, Safari, etc.
    browser_version = Column(String(50))
    os = Column(String(100))  # iOS, Android, Windows, macOS
    os_version = Column(String(50))
    
    # Informations réseau
    ip_address = Column(String(45))
    user_agent = Column(Text)
    
    # Métadonnées
    location_city = Column(String(100))
    location_country = Column(String(100))
    
    # Statut
    is_active = Column(Boolean, default=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    last_activity = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relations
    user = relationship("User", back_populates="sessions", foreign_keys=[user_id])
    tenant = relationship("Tenant", back_populates="sessions", foreign_keys=[tenant_id])
    active_pharmacy = relationship(
        "Pharmacy",
        foreign_keys=[active_pharmacy_id],
        lazy="joined"
    )
    active_branch = relationship(
        "Branch",
        foreign_keys=[active_branch_id],
        lazy="joined"
    )
    
    # Index
    __table_args__ = (
        Index("idx_user_sessions_user_id", "user_id"),
        Index("idx_user_sessions_tenant_id", "tenant_id"),
        Index("idx_user_sessions_session_id", "session_id"),
        Index("idx_user_sessions_is_active", "is_active"),
        Index("idx_user_sessions_expires_at", "expires_at"),
        Index("idx_user_sessions_platform", "platform"),
        Index("idx_user_sessions_last_activity", "last_activity"),
        Index("idx_user_sessions_active_pharmacy", "active_pharmacy_id"),
        Index("idx_user_sessions_active_branch", "active_branch_id"),
    )
    
    def __repr__(self) -> str:
        return f"<UserSession {self.session_id} - {self.platform}>"
    
    def is_expired(self) -> bool:
        """Vérifie si la session est expirée"""
        return datetime.utcnow() > self.expires_at
    
    def extend_session(self, days: int = 30) -> None:
        """Prolonge la session"""
        from datetime import timedelta
        self.expires_at = datetime.utcnow() + timedelta(days=days)
        self.last_activity = datetime.utcnow()
    
    def update_activity(self) -> None:
        """Met à jour la dernière activité"""
        self.last_activity = datetime.utcnow()
    
    def set_active_context(self, pharmacy_id: uuid.UUID = None, branch_id: uuid.UUID = None) -> None:
        """Définit la pharmacie et branche actives pour la session"""
        if pharmacy_id:
            self.active_pharmacy_id = pharmacy_id
        if branch_id:
            self.active_branch_id = branch_id
        self.update_activity()
    
    def to_dict(self) -> dict:
        """Convertit en dictionnaire"""
        return {
            "id": str(self.id),
            "session_id": self.session_id,
            "platform": self.platform,
            "device_type": self.device_type,
            "device_name": self.device_name,
            "ip_address": self.ip_address,
            "location_city": self.location_city,
            "location_country": self.location_country,
            "is_active": self.is_active,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "active_pharmacy_id": str(self.active_pharmacy_id) if self.active_pharmacy_id else None,
            "active_branch_id": str(self.active_branch_id) if self.active_branch_id else None,
        }
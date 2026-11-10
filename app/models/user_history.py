# app/models/user_history.py
"""
Modèle pour l'historique des actions utilisateurs
Permet de tracer toutes les actions effectuées par les utilisateurs
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Index, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class UserHistory(Base):
    """
    Historique complet des actions des utilisateurs
    Permet à l'admin de contrôler qui a fait quoi, quand, et sur quoi
    """
    __tablename__ = "user_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Type d'action
    action_type = Column(
        String(50),
        nullable=False,
        index=True,
        comment="create, update, delete, restore, view, export, login, logout, etc."
    )
    
    # Module concerné
    module = Column(
        String(50),
        nullable=False,
        index=True,
        comment="product, sale, user, client, supplier, stock, etc."
    )
    
    # Identifiant de l'entité concernée
    entity_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    entity_reference = Column(String(100), nullable=True, comment="Référence lisible de l'entité")
    entity_name = Column(String(255), nullable=True, comment="Nom de l'entité")
    
    # Détails de l'action
    action_description = Column(Text, nullable=True, comment="Description textuelle de l'action")
    
    # Avant / Après (pour les modifications)
    old_data = Column(JSON, nullable=True, comment="Données avant modification")
    new_data = Column(JSON, nullable=True, comment="Données après modification")
    
    # Contexte
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    session_id = Column(String(255), nullable=True, index=True)
    
    # Statut
    status = Column(
        String(20),
        nullable=False,
        default="success",
        comment="success, error, warning, info"
    )
    error_message = Column(Text, nullable=True, comment="Message d'erreur si l'action a échoué")
    
    # Métadonnées supplémentaires
    user_metadata = Column(JSON, nullable=True, default=dict, comment="Métadonnées additionnelles")
    
    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    
    # Relations
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    user = relationship("User", foreign_keys=[user_id])
    
    __table_args__ = (
        Index("idx_user_history_tenant_user", "tenant_id", "user_id"),
        Index("idx_user_history_tenant_module", "tenant_id", "module"),
        Index("idx_user_history_tenant_action", "tenant_id", "action_type"),
        Index("idx_user_history_entity", "tenant_id", "module", "entity_id"),
        Index("idx_user_history_created_at", "tenant_id", "created_at"),
        Index("idx_user_history_session", "session_id"),
    )
    
    def __repr__(self) -> str:
        return f"<UserHistory {self.user_id} | {self.action_type} | {self.module} | {self.created_at}>"
    
    def to_dict(self) -> dict:
        """Convertit l'historique en dictionnaire"""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "user_name": self.user.nom_complet if self.user else None,
            "user_email": self.user.email if self.user else None,
            "user_role": self.user.role if self.user else None,
            "action_type": self.action_type,
            "module": self.module,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "entity_reference": self.entity_reference,
            "entity_name": self.entity_name,
            "action_description": self.action_description,
            "old_data": self.old_data,
            "new_data": self.new_data,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "session_id": self.session_id,
            "status": self.status,
            "error_message": self.error_message,
            "user_metadata": self.metadata or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }



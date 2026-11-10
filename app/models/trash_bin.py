# app/models/trash_bin.py
"""
Modèle pour la corbeille
Gère les éléments supprimés avec possibilité de restauration
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Index, JSON, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class TrashBin(Base):
    """
    Corbeille pour les éléments supprimés
    Permet de restaurer les éléments supprimés et de garder une trace
    """
    __tablename__ = "trash_bin"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=True, index=True)
    
    # Type d'élément supprimé
    item_type = Column(
        String(50),
        nullable=False,
        index=True,
        comment="product, sale, user, client, supplier, stock, etc."
    )
    
    # Identifiant original de l'élément supprimé
    original_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    original_reference = Column(String(100), nullable=True, comment="Référence originale")
    original_name = Column(String(255), nullable=True, comment="Nom original")
    
    # Données complètes de l'élément supprimé
    data = Column(JSON, nullable=False, comment="Données complètes de l'élément au moment de la suppression")
    
    # Informations sur la suppression
    deleted_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    deleted_by_name = Column(String(100), nullable=True)
    deleted_by_email = Column(String(100), nullable=True)
    deleted_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    deletion_reason = Column(Text, nullable=True, comment="Raison de la suppression")
    
    # Pour la restauration
    restored_at = Column(DateTime, nullable=True)
    restored_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    restored_by_name = Column(String(100), nullable=True)
    
    # Expiration automatique (pour suppression définitive)
    auto_delete_at = Column(DateTime, nullable=True, comment="Date de suppression automatique définitive")
    
    # Statut
    is_restored = Column(Boolean, nullable=False, default=False, index=True)
    
    # Métadonnées
    trash_metadata = Column(JSON, nullable=True, default=dict)
    
    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relations
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    pharmacy = relationship("Pharmacy", foreign_keys=[pharmacy_id])
    deleted_by = relationship("User", foreign_keys=[deleted_by_id])
    restored_by = relationship("User", foreign_keys=[restored_by_id])
    
    __table_args__ = (
        Index("idx_trash_bin_tenant_type", "tenant_id", "item_type"),
        Index("idx_trash_bin_original", "tenant_id", "item_type", "original_id"),
        Index("idx_trash_bin_deleted_by", "deleted_by_id"),
        Index("idx_trash_bin_deleted_at", "tenant_id", "deleted_at"),
        Index("idx_trash_bin_restored", "tenant_id", "is_restored"),
        Index("idx_trash_bin_auto_delete", "auto_delete_at"),
        Index("idx_trash_bin_pharmacy", "pharmacy_id", "item_type"),
    )
    
    def __repr__(self) -> str:
        return f"<TrashBin {self.item_type}: {self.original_name} | deleted by {self.deleted_by_name} at {self.deleted_at}>"
    
    def to_dict(self, include_data: bool = True) -> dict:
        """Convertit l'entrée corbeille en dictionnaire"""
        result = {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "pharmacy_id": str(self.pharmacy_id) if self.pharmacy_id else None,
            "item_type": self.item_type,
            "original_id": str(self.original_id) if self.original_id else None,
            "original_reference": self.original_reference,
            "original_name": self.original_name,
            "deleted_by_id": str(self.deleted_by_id) if self.deleted_by_id else None,
            "deleted_by_name": self.deleted_by_name,
            "deleted_by_email": self.deleted_by_email,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "deletion_reason": self.deletion_reason,
            "restored_at": self.restored_at.isoformat() if self.restored_at else None,
            "restored_by_id": str(self.restored_by_id) if self.restored_by_id else None,
            "restored_by_name": self.restored_by_name,
            "auto_delete_at": self.auto_delete_at.isoformat() if self.auto_delete_at else None,
            "is_restored": self.is_restored,
            "trash_metadata": self.metadata or {},
        }
        
        if include_data:
            result["data"] = self.data
        
        return result
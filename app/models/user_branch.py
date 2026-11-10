# app/models/user_branch.py
"""
Modèle d'association entre les utilisateurs et les branches
Permet d'assigner un utilisateur à une ou plusieurs branches avec des droits spécifiques
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, Boolean, DateTime, ForeignKey, String, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base import Base


class UserBranch(Base):
    """
    Table d'association User-Branch
    Permet de gérer les utilisateurs assignés à chaque branche
    """
    __tablename__ = "user_branches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Relations
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id", ondelete="CASCADE"), nullable=False)
    
    # Rôle spécifique dans la branche (peut différer du rôle global)
    role_in_branch = Column(String(50), nullable=True)  # manager, cashier, pharmacist, viewer
    
    # Permissions spécifiques à la branche
    permissions = Column(JSON, default=lambda: {
        "can_sell": True,
        "can_manage_stock": False,
        "can_manage_expenses": False,
        "can_view_reports": True,
        "can_manage_users": False
    })
    
    # Flag pour indiquer si c'est la branche principale de l'utilisateur
    is_primary = Column(Boolean, default=False)
    
    # Statut
    is_active = Column(Boolean, default=True)
    
    # Métadonnées
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # Relations
    user = relationship("User", back_populates="branch_associations", foreign_keys=[user_id])
    branch = relationship("Branch", back_populates="user_associations")
    creator = relationship("User", foreign_keys=[created_by])
    
    def __repr__(self):
        return f"<UserBranch user={self.user_id} branch={self.branch_id}>"
    
    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "branch_id": str(self.branch_id),
            "role_in_branch": self.role_in_branch,
            "permissions": self.permissions,
            "is_primary": self.is_primary,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
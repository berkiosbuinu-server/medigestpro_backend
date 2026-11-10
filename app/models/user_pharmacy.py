# app/models/user_pharmacy.py
from sqlalchemy import Column, ForeignKey, Integer, Boolean, String, DateTime, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.base import Base
from sqlalchemy.dialects.postgresql import UUID
import uuid

class UserPharmacy(Base):
    """Modèle pour la relation Many-to-Many entre utilisateurs et pharmacies"""
    __tablename__ = 'user_pharmacy'
    
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id", ondelete="CASCADE"))
    is_primary = Column(Boolean, default=False)
    role_in_pharmacy = Column(String(50), default='employee')
    can_manage = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # RELATIONS : On utilise des noms de classe en string pour éviter les imports circulaires
    user = relationship(
        "User", 
        back_populates="pharmacy_associations", 
        overlaps="pharmacies,users"
    )
    
    pharmacy = relationship(
        "Pharmacy", 
        back_populates="user_associations", 
        overlaps="pharmacies,users"
    )
    
    __table_args__ = (
        UniqueConstraint('user_id', 'pharmacy_id', name='uq_user_pharmacy'),
        Index('idx_user_pharmacy_user', 'user_id'),
        Index('idx_user_pharmacy_pharmacy', 'pharmacy_id'),
    )
    
    def __repr__(self):
        return f"<UserPharmacy user={self.user_id} pharmacy={self.pharmacy_id}>"
# app/models/admin_sync.py
"""
Modèle pour la synchronisation des données admin offline
Permet de tracker l'état de synchronisation des données entre les tenants/branches et l'admin
"""

from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, 
    ForeignKey, JSON, Enum, Text, BigInteger, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base
import enum


class SyncEntityType(str, enum.Enum):
    """Types d'entités synchronisables"""
    TENANT = "tenant"
    BRANCH = "branch"
    USER = "user"
    PRODUCT = "product"
    SALE = "sale"
    INVOICE = "invoice"
    PURCHASE = "purchase"
    DEBT = "debt"
    CAPITAL = "capital"
    EXPENSE = "expense"
    STOCK = "stock"
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    TRANSFER = "transfer"
    ORDER = "order"
    PAYMENT = "payment"
    FINANCIAL_TRANSACTION = "financial_transaction"
    AUDIT_LOG = "audit_log"
    CATEGORY = "category"
    STOCK_ADJUSTMENT = "stock_adjustment"


class SyncOperation(str, enum.Enum):
    """Types d'opérations de synchronisation"""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    MERGE = "merge"


class SyncStatus(str, enum.Enum):
    """Statuts de synchronisation"""
    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"
    CONFLICT = "conflict"
    IGNORED = "ignored"


class AdminSyncLog(Base):
    """Log de synchronisation admin"""
    __tablename__ = "admin_sync_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Identifiants
    source_tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    source_branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    entity_type = Column(Enum(SyncEntityType), nullable=False)
    entity_id = Column(Integer, nullable=False)  # ID dans le système source
    
    # Version et timestamp
    entity_version = Column(String(255))  # Version hash ou timestamp
    entity_data = Column(JSON, nullable=False)  # Données complètes de l'entité
    
    # Métadonnées de sync
    operation = Column(Enum(SyncOperation), default=SyncOperation.CREATE)
    sync_status = Column(Enum(SyncStatus), default=SyncStatus.PENDING)
    
    # Admin target (l'admin qui a fait la sync)
    admin_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    synced_at = Column(DateTime(timezone=True), nullable=True)
    last_modified_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Pour gestion des conflits
    conflict_resolution = Column(Text, nullable=True)
    previous_version_hash = Column(String(255), nullable=True)
    
    # Métriques
    sync_duration_ms = Column(Integer, nullable=True)  # Durée en millisecondes
    data_size_bytes = Column(Integer, nullable=True)  # Taille des données
    
    # Relations
    tenant = relationship("Tenant", foreign_keys=[source_tenant_id])
    branch = relationship("Branch", foreign_keys=[source_branch_id])
    admin_user = relationship("User", foreign_keys=[admin_user_id])
    
    # Index pour performances
    __table_args__ = (
        Index('idx_admin_sync_tenant_entity', 'source_tenant_id', 'entity_type', 'entity_id'),
        Index('idx_admin_sync_status', 'sync_status'),
        Index('idx_admin_sync_created', 'created_at'),
        Index('idx_admin_sync_admin', 'admin_user_id'),
    )


class AdminSyncCheckpoint(Base):
    """Points de contrôle pour la synchronisation incrémentale"""
    __tablename__ = "admin_sync_checkpoints"
    
    id = Column(Integer, primary_key=True, index=True)
    
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    entity_type = Column(Enum(SyncEntityType), nullable=False)
    
    # Dernier timestamp synchronisé
    last_sync_timestamp = Column(DateTime(timezone=True), nullable=False)
    last_sync_id = Column(Integer, nullable=True)  # Dernier ID synchronisé
    
    # Métadonnées
    total_synced_count = Column(BigInteger, default=0)
    last_sync_duration = Column(Integer, nullable=True)  # en secondes
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relations
    tenant = relationship("Tenant")
    branch = relationship("Branch")
    
    __table_args__ = (
        Index('idx_admin_checkpoint_tenant_branch', 'tenant_id', 'branch_id', 'entity_type'),
    )


class AdminSyncBatch(Base):
    """Lots de synchronisation pour l'export/import groupé"""
    __tablename__ = "admin_sync_batches"
    
    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String(255), unique=True, nullable=False, index=True)
    
    # Source
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=True)
    
    # Métadonnées
    entity_types = Column(JSON)  # Liste des types d'entités incluses
    total_entities = Column(Integer, default=0)
    total_size_bytes = Column(BigInteger, default=0)
    
    # Statut
    status = Column(Enum(SyncStatus), default=SyncStatus.PENDING)
    error_message = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Exporté par
    exported_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    # Relations
    tenant = relationship("Tenant")
    branch = relationship("Branch")
    user = relationship("User", foreign_keys=[exported_by])
    
    __table_args__ = (
        Index('idx_admin_batch_status', 'status'),
        Index('idx_admin_batch_created', 'created_at'),
    )


class AdminSyncFilter(Base):
    """Filtres personnalisés pour la synchronisation admin"""
    __tablename__ = "admin_sync_filters"
    
    id = Column(Integer, primary_key=True, index=True)
    
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # Filtres
    entity_types = Column(JSON)  # Types d'entités à inclure
    tenant_ids = Column(JSON)  # Liste des tenants
    branch_ids = Column(JSON)  # Liste des branches
    date_range_start = Column(DateTime(timezone=True), nullable=True)
    date_range_end = Column(DateTime(timezone=True), nullable=True)
    
    # Critères avancés (JSON flexible)
    custom_filters = Column(JSON, nullable=True)
    
    # Propriétaire du filtre
    admin_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relations
    admin_user = relationship("User")
    
    __table_args__ = (
        Index('idx_admin_filter_user', 'admin_user_id'),
    )
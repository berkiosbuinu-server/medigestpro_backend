# app/models/mixins.py - Nouveau fichier
from sqlalchemy import Column, String, DateTime
from datetime import datetime


class SyncMixin:
    """Mixin pour ajouter les colonnes de synchronisation"""
    
    sync_status = Column(String(20), default="pending", comment="pending|synced|failed")
    sync_date = Column(DateTime, nullable=True)
    last_modified = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TenantSyncMixin(SyncMixin):
    """Mixin avec tenant_id pour la synchronisation"""
    pass
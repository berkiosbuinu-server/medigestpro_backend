import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, JSON, ForeignKey
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base

class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    table_name = Column(String, nullable=False)
    action = Column(String, nullable=False)  # CREATE, UPDATE, DELETE
    data = Column(JSON, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

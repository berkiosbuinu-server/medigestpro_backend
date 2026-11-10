import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from sqlalchemy import Column, String, Text, DateTime, JSON, ForeignKey, Integer, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base

logger = logging.getLogger(__name__)

class AuditLog(Base):
    """Log d'audit système avec support multi-tenant et traçabilité avancée"""
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    # Action (Requis par la DB et pour compatibilité)
    action = Column(String(100), nullable=False, index=True)
    action_type = Column(String(50), nullable=False, default="OTHER", index=True)
    action_category = Column(String(50), nullable=False, default="system", index=True)
    
    # Niveaux (Simplification pour éviter la confusion)
    action_level = Column(String(20), default="INFO", index=True) # DEBUG, INFO, WARNING, ERROR
    severity = Column(String(20), nullable=False, default="info", index=True)

    entity_type = Column(String(100), nullable=True, index=True)
    entity_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    entity_name = Column(String(200), nullable=True)

    description = Column(Text, nullable=False)
    details = Column(JSON, nullable=True, default=dict)
    
    changes_before = Column(JSON, nullable=True, default=dict)
    changes_after = Column(JSON, nullable=True, default=dict)
    changes_summary = Column(Text, nullable=True)

    ip_address = Column(String(50), nullable=True, index=True)
    user_agent = Column(Text, nullable=True)
    device_type = Column(String(50), nullable=True)
    browser = Column(String(100), nullable=True)
    operating_system = Column(String(100), nullable=True)

    duration_ms = Column(Integer, nullable=True)
    status_code = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    stack_trace = Column(Text, nullable=True)

    reference_number = Column(String(100), nullable=True, index=True)
    batch_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    parent_log_id = Column(UUID(as_uuid=True), ForeignKey("audit_logs.id", ondelete="SET NULL"), nullable=True)

    source_module = Column(String(100), nullable=True)
    request_id = Column(String(100), nullable=True, index=True)
    session_id = Column(String(100), nullable=True, index=True)

    # Remplacement de utcnow() par une méthode compatible fuseaux horaires
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relations
    user = relationship("User", foreign_keys=[user_id], lazy="select")
    # parent_log et tenant sont définis via les ForeignKey

    @classmethod
    def create_log(cls, db, **kwargs):
        """Méthode sécurisée pour injecter un log depuis n'importe quel point du code"""
        try:
            # Sécurité sur les champs obligatoires
            if not kwargs.get("action"):
                kwargs["action"] = kwargs.get("action_type", "UNKNOWN")
            
            # Nettoyage des données JSON pour éviter les erreurs de sérialisation
            for field in ["details", "changes_before", "changes_after"]:
                if field in kwargs and kwargs[field] is not None:
                    kwargs[field] = cls._ensure_json_serializable(kwargs[field])

            # Extraction des infos User-Agent
            if kwargs.get("user_agent"):
                ua_info = cls._parse_user_agent(kwargs["user_agent"])
                kwargs.update({
                    "device_type": ua_info["device_type"],
                    "browser": ua_info["browser"],
                    "operating_system": ua_info["os"]
                })

            log = cls(**kwargs)
            db.add(log)
            db.flush()
            return log
        except Exception as e:
            logger.error(f"Échec critique create_log: {str(e)}")
            return None

    @staticmethod
    def _ensure_json_serializable(obj):
        """Force la conversion en dictionnaire compatible JSON"""
        try:
            return json.loads(json.dumps(obj, default=str))
        except:
            return {"error": "Non-serializable data"}
    
    @staticmethod
    def _parse_user_agent(user_agent: Optional[str]) -> Dict[str, str]:
        """
        Analyse le User-Agent pour extraire des informations
        """
        if not user_agent:
            return {"device_type": "unknown", "browser": "unknown", "os": "unknown"}
        
        result = {
            "device_type": "desktop",
            "browser": "unknown",
            "os": "unknown"
        }
        
        user_agent = user_agent.lower()
        
        # Détection du type d'appareil
        if "mobile" in user_agent:
            result["device_type"] = "mobile"
        elif "tablet" in user_agent or "ipad" in user_agent:
            result["device_type"] = "tablet"
        
        # Détection du navigateur
        if "chrome" in user_agent and "chromium" not in user_agent:
            result["browser"] = "chrome"
        elif "firefox" in user_agent:
            result["browser"] = "firefox"
        elif "safari" in user_agent and "chrome" not in user_agent:
            result["browser"] = "safari"
        elif "edge" in user_agent:
            result["browser"] = "edge"
        elif "opera" in user_agent:
            result["browser"] = "opera"
        
        # Détection du système d'exploitation
        if "windows" in user_agent:
            result["os"] = "windows"
        elif "mac os" in user_agent or "macos" in user_agent:
            result["os"] = "macos"
        elif "linux" in user_agent:
            result["os"] = "linux"
        elif "android" in user_agent:
            result["os"] = "android"
        elif "ios" in user_agent or "iphone" in user_agent or "ipad" in user_agent:
            result["os"] = "ios"
        
        return result
    
    @staticmethod
    def _generate_changes_summary(before: Optional[Dict], after: Optional[Dict]) -> Optional[str]:
        """
        Génère un résumé lisible des changements
        """
        if not before or not after:
            return None
        
        changes = []
        
        for key in set(before.keys()) | set(after.keys()):
            old_value = before.get(key)
            new_value = after.get(key)
            
            if old_value != new_value:
                if isinstance(old_value, dict) and isinstance(new_value, dict):
                    # Pour les objets complexes, juste mentionner qu'il y a eu changement
                    changes.append(f"{key}: [object modifié]")
                else:
                    # Tronquer les valeurs longues
                    old_str = str(old_value)[:50] + ("..." if len(str(old_value)) > 50 else "")
                    new_str = str(new_value)[:50] + ("..." if len(str(new_value)) > 50 else "")
                    changes.append(f"{key}: {old_str} → {new_str}")
        
        if not changes:
            return "Aucun changement détecté"
        
        return "; ".join(changes[:10]) + (f"... (+{len(changes)-10} autres)" if len(changes) > 10 else "")
    
    # =======================
    # Propriétés calculées
    # =======================
    @property
    def is_successful(self) -> bool:
        """Vérifie si l'action a réussi"""
        return self.severity not in ["error", "critical"] and self.action_level not in ["ERROR"]
    
    @property
    def duration_seconds(self) -> Optional[float]:
        """Durée en secondes"""
        if self.duration_ms:
            return self.duration_ms / 1000.0
        return None
    
    @property
    def location_info(self) -> Optional[str]:
        """Informations de localisation formatées"""
        if self.city and self.country:
            return f"{self.city}, {self.country}"
        elif self.country:
            return self.country
        return None
    
    @property
    def device_info(self) -> str:
        """Informations sur l'appareil formatées"""
        parts = []
        if self.device_type:
            parts.append(self.device_type)
        if self.browser:
            parts.append(self.browser)
        if self.operating_system:
            parts.append(self.operating_system)
        return " / ".join(parts) if parts else "Inconnu"
    
    # =======================
    # Méthodes de sérialisation
    # =======================
    def to_dict(self, include_details: bool = True) -> Dict[str, Any]:
        """
        Convertit le log en dictionnaire
        
        Args:
            include_details: Inclure les détails JSON (peut être volumineux)
        """
        data = {
            "id": str(self.id),
            "user_id": str(self.user_id) if self.user_id else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "action": self.action,
            "action_type": self.action_type,
            "action_category": self.action_category,
            "action_level": self.action_level,
            "severity": self.severity,
            "entity_type": self.entity_type,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "entity_name": self.entity_name,
            "description": self.description,
            "changes_summary": self.changes_summary,
            "ip_address": self.ip_address,
            "device_info": self.device_info,
            "location_info": self.location_info,
            "reference_number": self.reference_number,
            "source_module": self.source_module,
            "duration_ms": self.duration_ms,
            "duration_seconds": self.duration_seconds,
            "status_code": self.status_code,
            "is_successful": self.is_successful,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        
        # Ajouter updated_at si disponible
        if hasattr(self, 'updated_at') and self.updated_at:
            data["updated_at"] = self.updated_at.isoformat()
        
        # Ajouter le nom d'utilisateur si la relation existe
        if hasattr(self, 'user') and self.user and hasattr(self.user, 'nom_complet'):
            data["user_name"] = self.user.nom_complet
        
        if include_details:
            data.update({
                "details": self.details,
                "changes_before": self.changes_before,
                "changes_after": self.changes_after,
                "user_agent": self.user_agent,
                "country": self.country,
                "region": self.region,
                "city": self.city,
                "latitude": self.latitude,
                "longitude": self.longitude,
                "error_message": self.error_message,
                "stack_trace": self.stack_trace,
                "request_id": self.request_id,
                "session_id": self.session_id,
                "batch_id": str(self.batch_id) if self.batch_id else None,
                "parent_log_id": str(self.parent_log_id) if self.parent_log_id else None,
            })
        
        return data
    
    def to_json(self, include_details: bool = True) -> str:
        """Convertit en JSON"""
        return json.dumps(self.to_dict(include_details), ensure_ascii=False, indent=2)
    
    # =======================
    # Méthodes de recherche
    # =======================
    @classmethod
    def search_logs(
        cls,
        db,
        tenant_id: UUID,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        user_id: Optional[UUID] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[UUID] = None,
        action: Optional[str] = None,
        action_type: Optional[str] = None,
        severity: Optional[str] = None,
        action_level: Optional[str] = None,
        ip_address: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ):
        """Recherche avancée dans les logs"""
        query = db.query(cls).filter(cls.tenant_id == tenant_id)
        
        # Filtres optionnels
        if start_date:
            query = query.filter(cls.created_at >= start_date)
        if end_date:
            query = query.filter(cls.created_at <= end_date)
        if user_id:
            query = query.filter(cls.user_id == user_id)
        if entity_type:
            query = query.filter(cls.entity_type == entity_type)
        if entity_id:
            query = query.filter(cls.entity_id == entity_id)
        if action:
            query = query.filter(cls.action == action)
        if action_type:
            query = query.filter(cls.action_type == action_type)
        if severity:
            query = query.filter(cls.severity == severity)
        if action_level:
            query = query.filter(cls.action_level == action_level)
        if ip_address:
            query = query.filter(cls.ip_address == ip_address)
        
        # Trier par date décroissante
        query = query.order_by(cls.created_at.desc())
        
        # Pagination
        total = query.count()
        logs = query.offset(offset).limit(limit).all()
        
        return {
            "logs": [log.to_dict(include_details=False) for log in logs],
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < total
        }
    
    # =======================
    # Méthodes de statistiques
    # =======================
    @classmethod
    def get_statistics(
        cls,
        db,
        tenant_id: UUID,
        period_days: int = 30
    ) -> Dict[str, Any]:
        """Récupère des statistiques sur les logs"""
        from datetime import datetime, timedelta
        from sqlalchemy import func
        
        start_date = datetime.utcnow() - timedelta(days=period_days)
        
        # Actions par type (compatibilité avec les deux versions)
        actions_by_type = db.query(
            cls.action_type,
            func.count(cls.id).label("count")
        ).filter(
            cls.tenant_id == tenant_id,
            cls.created_at >= start_date
        ).group_by(cls.action_type).all()
        
        # Actions (ancienne version)
        actions = db.query(
            cls.action,
            func.count(cls.id).label("count")
        ).filter(
            cls.tenant_id == tenant_id,
            cls.created_at >= start_date
        ).group_by(cls.action).all()
        
        # Actions par catégorie
        actions_by_category = db.query(
            cls.action_category,
            func.count(cls.id).label("count")
        ).filter(
            cls.tenant_id == tenant_id,
            cls.created_at >= start_date
        ).group_by(cls.action_category).all()
        
        # Actions par entité
        actions_by_entity = db.query(
            cls.entity_type,
            func.count(cls.id).label("count")
        ).filter(
            cls.tenant_id == tenant_id,
            cls.created_at >= start_date
        ).group_by(cls.entity_type).all()
        
        # Séverités (compatibilité avec les deux versions)
        severities = db.query(
            cls.severity,
            func.count(cls.id).label("count")
        ).filter(
            cls.tenant_id == tenant_id,
            cls.created_at >= start_date
        ).group_by(cls.severity).all()
        
        # Niveaux d'action (ancienne version)
        action_levels = db.query(
            cls.action_level,
            func.count(cls.id).label("count")
        ).filter(
            cls.tenant_id == tenant_id,
            cls.created_at >= start_date
        ).group_by(cls.action_level).all()
        
        # Top utilisateurs
        top_users = db.query(
            cls.user_id,
            func.count(cls.id).label("count")
        ).filter(
            cls.tenant_id == tenant_id,
            cls.created_at >= start_date,
            cls.user_id.isnot(None)
        ).group_by(cls.user_id).order_by(func.count(cls.id).desc()).limit(10).all()
        
        # Dernières erreurs
        recent_errors = db.query(cls).filter(
            cls.tenant_id == tenant_id,
            (cls.severity.in_(["error", "critical"])) | (cls.action_level == "ERROR"),
            cls.created_at >= start_date
        ).order_by(cls.created_at.desc()).limit(10).all()
        
        return {
            "period_days": period_days,
            "start_date": start_date.isoformat(),
            "total_actions": sum(count for _, count in actions_by_type),
            "actions": dict(actions),
            "actions_by_type": dict(actions_by_type),
            "actions_by_category": dict(actions_by_category),
            "actions_by_entity": dict(actions_by_entity),
            "severities": dict(severities),
            "action_levels": dict(action_levels),
            "top_users": [
                {"user_id": str(user_id), "count": count}
                for user_id, count in top_users
            ],
            "recent_errors": [log.to_dict() for log in recent_errors]
        }
    
    def __repr__(self):
        return f"<AuditLog {self.action} by {self.user_id} at {self.created_at}>"
from sqlalchemy.orm import Session
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timezone, timedelta
import logging
import uuid
from uuid import UUID
from typing import Optional, Dict, Any, Union, List
import json
from app.models.audit_log import AuditLog
from app.db.session import SessionLocal
from app.core.constants import SYSTEM_TENANT_ID

logger = logging.getLogger(__name__)

class AuditService:
    """Service d'audit expert - Gère la persistence des logs et la sécurité des types"""
    
    ACTION_CATEGORIES = {
        "user": "users", "tenant": "system", "product": "inventory",
        "inventory": "inventory", "sale": "sales", "purchase": "purchases",
        "client": "clients", "payment": "financial", "refund": "financial",
        "stock_movement": "inventory", "category": "inventory", "supplier": "purchases",
        "report": "data", "audit": "security", "permission": "security",
        "role": "security", "pharmacy": "system", "caisse": "financial",
        "super-admin": "system", "dashboard": "system", "system": "system"
    }

    def __init__(self):
        # Cache des colonnes du modèle pour éviter les 'invalid keyword argument'
        try:
            self._model_columns = {c.key for c in inspect(AuditLog).mapper.column_attrs}
        except Exception:
            # Fallback au cas où l'inspect échoue au démarrage
            self._model_columns = set()

    def log_action(
        self,
        db: Session,
        tenant_id: Union[str, UUID, None],
        user_id: Union[str, UUID, None],
        action: str,
        cible: str,
        description: str,
        ip: str = None,
        user_agent: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        entity_id: Optional[Union[str, UUID]] = None,
        severity: str = "info",
        **kwargs
    ) -> Optional[AuditLog]:
        """Journalise une action dans la table d'audit avec gestion du tenant système"""
        try:
            # 🔥 CORRECTION CRITIQUE : Si tenant_id est None, utiliser SYSTEM_TENANT_ID
            if tenant_id is None:
                tenant_id = SYSTEM_TENANT_ID
                logger.debug(f"Using SYSTEM_TENANT_ID for action: {action} on {cible}")
            
            # Normalisation des UUIDs
            t_id = self._normalize_uuid(tenant_id)
            u_id = self._normalize_uuid(user_id) if user_id else None
            e_id = self._normalize_uuid(entity_id) if entity_id else None
            
            # Déterminer la catégorie d'action
            action_category = self.ACTION_CATEGORIES.get(cible.lower(), "system")
            
            # Déterminer le niveau d'action et la sévérité
            action_level = self._determine_action_level(severity, kwargs.get('status_code'))
            
            # Création du payload du log
            log_payload = {
                "id": uuid.uuid4(),
                "tenant_id": t_id,
                "user_id": u_id,
                "action": action,
                "action_type": self._determine_action_type(action, cible),
                "action_category": action_category,
                "action_level": action_level,
                "entity_type": cible,
                "entity_id": e_id,
                "description": description,
                "details": self._serialize_complex(details),
                "ip_address": ip,
                "user_agent": user_agent,
                "severity": severity.lower(),
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)
            }

            # Injection sécurisée des kwargs supplémentaires
            for key, value in kwargs.items():
                if key in self._model_columns and key not in log_payload:
                    log_payload[key] = value
                elif key == 'status' and 'status_code' in self._model_columns:
                    log_payload['status_code'] = value
                elif key == 'pharmacy_id' and 'pharmacy_id' in self._model_columns:
                    log_payload['pharmacy_id'] = self._normalize_uuid(value) if value else None
                elif key == 'duration_ms' and 'duration_ms' in self._model_columns:
                    log_payload['duration_ms'] = value

            # Création et sauvegarde du log
            audit_log = AuditLog(**log_payload)
            db.add(audit_log)
            db.flush()
            
            # Log dans la console
            self._log_to_stdout(log_payload)
            return audit_log

        except Exception as e:
            logger.critical(f"CRITICAL AUDIT FAILURE: {str(e)}")
            self._log_fallback(action, cible, description, tenant_id)
            # Tenter de rollback la session
            try:
                db.rollback()
            except:
                pass
            return None

    def _determine_action_level(self, severity: str, status_code: Optional[int] = None) -> str:
        """Détermine le niveau d'action basé sur la sévérité et le code status"""
        if severity.lower() in ['error', 'critical']:
            return 'ERROR'
        if status_code and status_code >= 400:
            return 'ERROR'
        if status_code and status_code >= 300:
            return 'WARNING'
        return 'INFO'

    def _determine_action_type(self, action: str, cible: str) -> str:
        """Détermine un type d'action plus descriptif"""
        action_upper = action.upper()
        
        # Mapping pour les actions super admin
        if cible.lower() == 'super-admin':
            if 'CREATE' in action_upper or 'ADD' in action_upper:
                return 'SUPER_ADMIN_CREATE'
            elif 'UPDATE' in action_upper or 'EDIT' in action_upper:
                return 'SUPER_ADMIN_UPDATE'
            elif 'DELETE' in action_upper or 'REMOVE' in action_upper:
                return 'SUPER_ADMIN_DELETE'
            elif 'ACTIVATE' in action_upper:
                return 'SUPER_ADMIN_ACTIVATE'
            elif 'SUSPEND' in action_upper:
                return 'SUPER_ADMIN_SUSPEND'
            else:
                return 'SUPER_ADMIN_ACTION'
        
        # Mapping standard
        if 'CREATE' in action_upper or 'ADD' in action_upper:
            return 'CREATE'
        elif 'UPDATE' in action_upper or 'EDIT' in action_upper or 'MODIFY' in action_upper:
            return 'UPDATE'
        elif 'DELETE' in action_upper or 'REMOVE' in action_upper:
            return 'DELETE'
        elif 'VIEW' in action_upper or 'GET' in action_upper or 'LIST' in action_upper:
            return 'VIEW'
        elif 'LOGIN' in action_upper:
            return 'LOGIN'
        elif 'LOGOUT' in action_upper:
            return 'LOGOUT'
        elif 'EXPORT' in action_upper:
            return 'EXPORT'
        elif 'IMPORT' in action_upper:
            return 'IMPORT'
        else:
            return action_upper

    def _normalize_uuid(self, value: Any) -> Optional[UUID]:
        """Normalise une valeur en UUID"""
        if value is None:
            return None
        if isinstance(value, UUID): 
            return value
        if isinstance(value, int) and value == 0:
            # Si c'est un entier 0, retourner None (ne devrait plus arriver avec le tenant système)
            return None
        try: 
            return UUID(str(value))
        except (ValueError, AttributeError): 
            return None

    def _serialize_complex(self, data: Any) -> Optional[Dict]:
        """Sérialise les données complexes en JSON"""
        if not data:
            return {}
        if isinstance(data, str): 
            try: 
                return json.loads(data)
            except json.JSONDecodeError: 
                return {"info": data}
        try:
            # Fonction de sérialisation personnalisée
            def json_serializer(obj):
                if isinstance(obj, (datetime, UUID)):
                    return str(obj)
                if hasattr(obj, 'dict'):  # Pour les modèles Pydantic
                    return obj.dict()
                if hasattr(obj, '__dict__'):  # Pour les objets simples
                    return {k: str(v) for k, v in obj.__dict__.items() if not k.startswith('_')}
                return str(obj)
                
            return json.loads(
                json.dumps(data, default=json_serializer)
            )
        except Exception as e:
            logger.warning(f"Erreur de sérialisation: {e}")
            return {"error": "Serialization failed", "original_type": str(type(data))}

    def _log_to_stdout(self, data: Dict):
        """Log dans la console avec formatage"""
        level = data.get('severity', 'info').upper()
        action_type = data.get('action_type', 'UNKNOWN')
        entity_type = data.get('entity_type', 'unknown')
        description = data.get('description', '')
        tenant_id = data.get('tenant_id', '')
        
        # Indiquer si c'est le tenant système
        tenant_info = f"[System Tenant]" if tenant_id == SYSTEM_TENANT_ID else f"[Tenant: {tenant_id}]"
        
        msg = f"AUDIT [{level}] {action_type} | {entity_type} | {tenant_info} | {description}"
        if level in ['ERROR', 'CRITICAL']:
            logger.error(msg)
        else:
            logger.info(msg)

    def _log_fallback(self, action, cible, desc, t_id):
        """Fallback en cas d'échec critique"""
        tenant_info = f"System Tenant" if t_id is None else f"Tenant: {t_id}"
        logger.warning(f"!!! FALLBACK AUDIT !!! -> {action} ON {cible} | {tenant_info} | Desc: {desc}")
    
    def prune_old_logs(self, db: Session, days: int = 30, preserve_system: bool = True) -> int:
        """
        Supprime les logs obsolètes
        
        Args:
            db: Session de base de données
            days: Nombre de jours de conservation
            preserve_system: Conserver les logs du tenant système
        """
        try:
            threshold_date = datetime.now(timezone.utc) - timedelta(days=days)
            query = db.query(AuditLog).filter(AuditLog.created_at < threshold_date)
            
            # Conserver les logs du tenant système si demandé
            if preserve_system:
                query = query.filter(AuditLog.tenant_id != SYSTEM_TENANT_ID)
            
            deleted_count = query.delete(synchronize_session=False)
            
            db.commit()
            if deleted_count > 0:
                logger.info(f"CLEANUP: {deleted_count} audit logs deleted (older than {days} days).")
            return deleted_count
        except Exception as e:
            db.rollback()
            logger.error(f"Error cleaning audit logs: {str(e)}")
            return 0

    def get_logs_by_tenant(
        self, 
        db: Session, 
        tenant_id: Union[str, UUID, None], 
        skip: int = 0, 
        limit: int = 100,
        entity_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        include_system: bool = False
    ) -> List[AuditLog]:
        """
        Récupère les logs d'audit pour un tenant avec filtres optionnels
        
        Args:
            db: Session de base de données
            tenant_id: ID du tenant (None pour super admin)
            skip: Nombre de logs à sauter
            limit: Nombre maximum de logs à retourner
            entity_type: Filtrer par type d'entité
            start_date: Date de début
            end_date: Date de fin
            include_system: Inclure les logs du tenant système (pour super admin)
        """
        try:
            # Si tenant_id est None, on utilise SYSTEM_TENANT_ID pour la requête
            if tenant_id is None:
                if include_system:
                    # Pour super admin, on peut voir tous les logs
                    query = db.query(AuditLog)
                else:
                    # Sinon, on utilise SYSTEM_TENANT_ID
                    query = db.query(AuditLog).filter(AuditLog.tenant_id == SYSTEM_TENANT_ID)
            else:
                t_id = self._normalize_uuid(tenant_id)
                query = db.query(AuditLog).filter(AuditLog.tenant_id == t_id)
            
            if entity_type:
                query = query.filter(AuditLog.entity_type == entity_type)
            
            if start_date:
                query = query.filter(AuditLog.created_at >= start_date)
            
            if end_date:
                query = query.filter(AuditLog.created_at <= end_date)
            
            return query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit).all()
            
        except Exception as e:
            logger.error(f"Error retrieving logs: {e}")
            return []

    def get_statistics(
        self, 
        db: Session, 
        tenant_id: Union[str, UUID, None] = None,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Récupère des statistiques sur les logs
        
        Args:
            db: Session de base de données
            tenant_id: ID du tenant (None pour tous les tenants)
            days: Période en jours
        """
        from sqlalchemy import func
        
        try:
            start_date = datetime.now(timezone.utc) - timedelta(days=days)
            
            # Construire la requête de base
            if tenant_id is None:
                query = db.query(AuditLog)
            else:
                t_id = self._normalize_uuid(tenant_id)
                query = db.query(AuditLog).filter(AuditLog.tenant_id == t_id)
            
            query = query.filter(AuditLog.created_at >= start_date)
            
            # Statistiques par type d'action
            actions_by_type = query.with_entities(
                AuditLog.action_type,
                func.count(AuditLog.id).label("count")
            ).group_by(AuditLog.action_type).all()
            
            # Statistiques par catégorie
            actions_by_category = query.with_entities(
                AuditLog.action_category,
                func.count(AuditLog.id).label("count")
            ).group_by(AuditLog.action_category).all()
            
            # Statistiques par sévérité
            severities = query.with_entities(
                AuditLog.severity,
                func.count(AuditLog.id).label("count")
            ).group_by(AuditLog.severity).all()
            
            return {
                "period_days": days,
                "start_date": start_date.isoformat(),
                "total_actions": sum(count for _, count in actions_by_type),
                "actions_by_type": dict(actions_by_type),
                "actions_by_category": dict(actions_by_category),
                "severities": dict(severities)
            }
            
        except Exception as e:
            logger.error(f"Error getting statistics: {e}")
            return {}

# --- Instance Unique ---
audit_service = AuditService()

# --- Fonctions à appeler depuis l'extérieur ---

def log_action(db: Session, **kwargs):
    """Wrapper pour la fonction de log avec gestion automatique du tenant système"""
    return audit_service.log_action(db, **kwargs)

def run_auto_prune():
    """Fonction utilitaire pour le nettoyage (à appeler dans main.py ou via un cron)"""
    db = SessionLocal()
    try:
        audit_service.prune_old_logs(db, days=30, preserve_system=True)
    finally:
        db.close()
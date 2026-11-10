# app/services/history_service.py
"""
Service pour l'enregistrement automatique des actions utilisateurs
"""
import logging
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from sqlalchemy.orm import Session

from app.models.user_history import UserHistory
from app.models.user import User

logger = logging.getLogger(__name__)


class HistoryService:
    """
    Service pour gérer l'historique des actions utilisateurs
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def log_action(
        self,
        tenant_id: UUID,
        user_id: UUID,
        action_type: str,
        module: str,
        entity_id: Optional[UUID] = None,
        entity_reference: Optional[str] = None,
        entity_name: Optional[str] = None,
        action_description: str = "",
        old_data: Optional[Dict] = None,
        new_data: Optional[Dict] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        session_id: Optional[str] = None,
        status: str = "success",
        error_message: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> UserHistory:
        """
        Enregistre une action dans l'historique
        """
        try:
            history = UserHistory(
                tenant_id=tenant_id,
                user_id=user_id,
                action_type=action_type,
                module=module,
                entity_id=entity_id,
                entity_reference=entity_reference,
                entity_name=entity_name,
                action_description=action_description,
                old_data=old_data,
                new_data=new_data,
                ip_address=ip_address,
                user_agent=user_agent,
                session_id=session_id,
                status=status,
                error_message=error_message,
                metadata=metadata or {}
            )
            self.db.add(history)
            self.db.flush()
            return history
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement de l'historique: {str(e)}")
            raise
    
    def log_create(
        self,
        user: User,
        module: str,
        entity_id: UUID,
        entity_reference: str,
        entity_name: str,
        data: Dict,
        **kwargs
    ) -> UserHistory:
        """Enregistre une création"""
        return self.log_action(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action_type="create",
            module=module,
            entity_id=entity_id,
            entity_reference=entity_reference,
            entity_name=entity_name,
            action_description=f"Création de {module}: {entity_name}",
            new_data=data,
            **kwargs
        )
    
    def log_update(
        self,
        user: User,
        module: str,
        entity_id: UUID,
        entity_reference: str,
        entity_name: str,
        old_data: Dict,
        new_data: Dict,
        **kwargs
    ) -> UserHistory:
        """Enregistre une modification"""
        return self.log_action(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action_type="update",
            module=module,
            entity_id=entity_id,
            entity_reference=entity_reference,
            entity_name=entity_name,
            action_description=f"Modification de {module}: {entity_name}",
            old_data=old_data,
            new_data=new_data,
            **kwargs
        )
    
    def log_delete(
        self,
        user: User,
        module: str,
        entity_id: UUID,
        entity_reference: str,
        entity_name: str,
        data: Dict,
        deletion_reason: Optional[str] = None,
        **kwargs
    ) -> UserHistory:
        """Enregistre une suppression (mise en corbeille)"""
        return self.log_action(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action_type="delete",
            module=module,
            entity_id=entity_id,
            entity_reference=entity_reference,
            entity_name=entity_name,
            action_description=f"Suppression de {module}: {entity_name}" + (f" - Raison: {deletion_reason}" if deletion_reason else ""),
            old_data=data,
            metadata={"deletion_reason": deletion_reason} if deletion_reason else {},
            **kwargs
        )
    
    def log_restore(
        self,
        user: User,
        module: str,
        entity_id: UUID,
        entity_reference: str,
        entity_name: str,
        **kwargs
    ) -> UserHistory:
        """Enregistre une restauration depuis la corbeille"""
        return self.log_action(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action_type="restore",
            module=module,
            entity_id=entity_id,
            entity_reference=entity_reference,
            entity_name=entity_name,
            action_description=f"Restauration de {module}: {entity_name} depuis la corbeille",
            **kwargs
        )
    
    def log_login(
        self,
        user: User,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        **kwargs
    ) -> UserHistory:
        """Enregistre une connexion"""
        return self.log_action(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action_type="login",
            module="auth",
            action_description=f"Connexion de l'utilisateur {user.email}",
            ip_address=ip_address,
            user_agent=user_agent,
            **kwargs
        )
    
    def log_logout(
        self,
        user: User,
        **kwargs
    ) -> UserHistory:
        """Enregistre une déconnexion"""
        return self.log_action(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action_type="logout",
            module="auth",
            action_description=f"Déconnexion de l'utilisateur {user.email}",
            **kwargs
        )
    
    def log_error(
        self,
        user: User,
        module: str,
        action: str,
        error_message: str,
        **kwargs
    ) -> UserHistory:
        """Enregistre une erreur"""
        return self.log_action(
            tenant_id=user.tenant_id,
            user_id=user.id,
            action_type=action,
            module=module,
            action_description=f"Erreur lors de {action} dans {module}",
            status="error",
            error_message=error_message,
            **kwargs
        )
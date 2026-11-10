# app/core/audit_service.py
import logging
import time
from typing import Dict, Any, Optional, Callable
from uuid import UUID
from datetime import datetime
from functools import wraps
from contextlib import contextmanager

from sqlalchemy.orm import Session
from fastapi import Request

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


class AuditService:
    """Service centralisé pour la gestion de l'audit"""
    
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id
        self.current_batch_id: Optional[UUID] = None
    
    @contextmanager
    def batch_audit(self, batch_description: str = None):
        import uuid
        batch_id = uuid.uuid4()
        self.current_batch_id = batch_id
        try:
            yield batch_id
        finally:
            if batch_description:
                self.log(
                    user_id=None, # À adapter
                    action_type="BATCH_COMPLETE",
                    action_category="system",
                    entity_type="batch",
                    entity_id=batch_id,
                    description=batch_description
                )
            self.current_batch_id = None # On reset après le log
    
    def log(
        self,
        user_id: Optional[UUID],
        action_type: str,
        action_category: str,
        entity_type: str,
        entity_id: Optional[UUID] = None,
        entity_name: Optional[str] = None,
        description: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        changes_before: Optional[Dict[str, Any]] = None,
        changes_after: Optional[Dict[str, Any]] = None,
        request: Optional[Request] = None,
        severity: str = "info",
        **kwargs
    ):
        """Enregistre un log d'audit"""
        try:
            ip_address = None
            user_agent = None
            
            if request:
                ip_address = request.client.host if request.client else None
                user_agent = request.headers.get("user-agent")

            # --- CORRECTION ICI ---
            # On s'assure que 'action' n'est jamais NULL pour la DB
            # On récupère 'action' dans kwargs ou on construit une valeur par défaut
            action = kwargs.get("action") or f"{action_type}_{entity_type}".upper()
            
            log = AuditLog.create_log(
                db=self.db,
                tenant_id=self.tenant_id,
                user_id=user_id,
                action=action, # <--- Passage explicite de la valeur action
                action_type=action_type,
                action_category=action_category,
                entity_type=entity_type,
                entity_id=entity_id,
                entity_name=entity_name,
                description=description,
                details=details,
                changes_before=changes_before,
                changes_after=changes_after,
                ip_address=ip_address,
                user_agent=user_agent,
                severity=severity,
                batch_id=self.current_batch_id,
                **kwargs
            )
            
            self.db.add(log)
            self.db.flush() 
            
            logger.debug(f"Audit log créé: {action_type} sur {entity_type}")
            return log
            
        except Exception as e:
            # On log l'erreur exacte pour le debug
            logger.error(f"Erreur création log audit (DB): {str(e)}")
            return None
    
    def log_with_timing(self, **kwargs):
        """Décorateur pour mesurer le temps d'exécution"""
        def decorator(func: Callable):
            @wraps(func)
            def wrapper(*args, **inner_kwargs):
                start_time = time.time()
                
                try:
                    result = func(*args, **inner_kwargs)
                    duration_ms = int((time.time() - start_time) * 1000)
                    
                    # Ajouter la durée au log
                    kwargs["duration_ms"] = duration_ms
                    kwargs["severity"] = "info"
                    
                    self.log(**kwargs)
                    
                    return result
                    
                except Exception as e:
                    duration_ms = int((time.time() - start_time) * 1000)
                    
                    # Log de l'erreur
                    kwargs.update({
                        "duration_ms": duration_ms,
                        "severity": "error",
                        "error_message": str(e),
                        "status_code": 500
                    })
                    
                    self.log(**kwargs)
                    raise
                    
            return wrapper
        return decorator
    
    def log_http_request(
        self,
        request: Request,
        user_id: Optional[UUID],
        response_status: int,
        duration_ms: int
    ):
        """Log une requête HTTP"""
        action_type = "HTTP_REQUEST"
        
        # Déterminer la catégorie basée sur le chemin
        path = request.url.path
        if "/api/v1/sales" in path:
            action_category = "sales"
        elif "/api/v1/inventory" in path:
            action_category = "inventory"
        elif "/api/v1/purchases" in path:
            action_category = "purchases"
        else:
            action_category = "system"
        
        # Déterminer le type d'entité
        entity_type = "http_request"
        
        # Déterminer la sévérité basée sur le code de réponse
        if response_status >= 500:
            severity = "error"
        elif response_status >= 400:
            severity = "warning"
        else:
            severity = "info"
        
        self.log(
            user_id=user_id,
            action_type=action_type,
            action_category=action_category,
            entity_type=entity_type,
            entity_name=f"{request.method} {path}",
            description=f"HTTP {request.method} {path} - {response_status}",
            details={
                "method": request.method,
                "path": path,
                "query_params": dict(request.query_params),
                "response_status": response_status,
                "content_type": request.headers.get("content-type")
            },
            request=request,
            severity=severity,
            duration_ms=duration_ms,
            status_code=response_status,
            source_module="fastapi"
        )
    
    def log_login(self, user_id: UUID, success: bool, request: Request):
        """Log une tentative de connexion"""
        self.log(
            user_id=user_id,
            action_type="LOGIN" if success else "LOGIN_FAILED",
            action_category="security",
            entity_type="user",
            entity_id=user_id,
            description=f"Connexion {'réussie' if success else 'échouée'}",
            details={"success": success},
            request=request,
            severity="warning" if not success else "info"
        )
    
    def log_data_change(
        self,
        user_id: UUID,
        entity_type: str,
        entity_id: UUID,
        action_type: str,
        changes_before: Dict[str, Any],
        changes_after: Dict[str, Any],
        description: str = None
    ):
        """Log un changement de données"""
        self.log(
            user_id=user_id,
            action_type=action_type,
            action_category="data",
            entity_type=entity_type,
            entity_id=entity_id,
            description=description or f"{action_type} de {entity_type}",
            changes_before=changes_before,
            changes_after=changes_after,
            severity="info"
        )


# Middleware FastAPI pour l'audit automatique
async def audit_middleware(request: Request, call_next):
    """Middleware pour auditer automatiquement les requêtes HTTP"""
    from app.db.session import get_db
    from app.api.deps import get_current_tenant, get_current_user
    
    start_time = time.time()
    
    try:
        response = await call_next(request)
        
        # Récupérer le tenant et l'utilisateur si disponible
        try:
            db = next(get_db())
            
            # Essayer de récupérer le tenant depuis les headers ou la session
            tenant_id = None
            user_id = None
            
            # Logique pour extraire tenant_id et user_id
            # À adapter selon votre implémentation
            
            if tenant_id:
                audit_service = AuditService(db, tenant_id)
                duration_ms = int((time.time() - start_time) * 1000)
                
                audit_service.log_http_request(
                    request=request,
                    user_id=user_id,
                    response_status=response.status_code,
                    duration_ms=duration_ms
                )
                
                db.commit()
                
        except Exception as e:
            logger.error(f"Erreur middleware audit: {str(e)}")
        
        return response
        
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(f"Erreur requête {request.url.path}: {str(e)}")
        raise
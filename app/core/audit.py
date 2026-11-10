# app/core/audit.py
import logging
import json
from typing import Dict, List, Optional, Any
from uuid import UUID
from datetime import datetime
from pathlib import Path
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class InventoryAudit:
    """Classe pour l'audit des inventaires"""
    
    @staticmethod
    def track_inventory_changes(
        db: Session,
        inventory_id: UUID,
        user_id: UUID,
        action: str,
        changes: Dict[str, Any] = None,
        ip_address: str = None,
        user_agent: str = None
    ):
        """
        Journalise toutes les modifications d'inventaire
        
        Args:
            db: Session de base de données
            inventory_id: ID de l'inventaire
            user_id: ID de l'utilisateur
            action: Action effectuée (create, start, count, complete, validate, cancel)
            changes: Détails des changements
            ip_address: Adresse IP de l'utilisateur
            user_agent: User-Agent du navigateur
        """
        try:
            from app.models.audit_log import AuditLog
            
            # Créer l'entrée d'audit
            audit_log = AuditLog(
                tenant_id=getattr(db.query("SELECT tenant_id FROM physical_inventories WHERE id = :id")
                                  .params(id=str(inventory_id)).scalar(), 'tenant_id', None),
                entity_type="inventory",
                entity_id=inventory_id,
                user_id=user_id,
                action=action,
                changes=changes or {},
                ip_address=ip_address,
                user_agent=user_agent,
                created_at=datetime.utcnow()
            )
            
            db.add(audit_log)
            db.commit()
            
            logger.info(f"Audit enregistré: {action} sur inventaire {inventory_id}")
            
        except Exception as e:
            logger.error(f"Erreur enregistrement audit inventaire: {str(e)}")
            db.rollback()
    
    @staticmethod
    def generate_audit_trail(
        db: Session,
        inventory_id: UUID,
        export_format: str = "json"
    ) -> Dict[str, Any]:
        """
        Génère une piste d'audit complète pour un inventaire
        
        Args:
            db: Session de base de données
            inventory_id: ID de l'inventaire
            export_format: Format d'export (json, html, pdf)
        
        Returns:
            Dictionnaire avec la piste d'audit
        """
        try:
            from app.models.audit_log import AuditLog
            from app.models.inventory import PhysicalInventory
            from app.models.user import User
            
            # Récupérer l'inventaire
            inventory = db.query(PhysicalInventory).filter(
                PhysicalInventory.id == inventory_id
            ).first()
            
            if not inventory:
                raise ValueError(f"Inventaire {inventory_id} non trouvé")
            
            # Récupérer les logs d'audit
            audit_logs = db.query(AuditLog).filter(
                AuditLog.entity_type == "inventory",
                AuditLog.entity_id == inventory_id
            ).order_by(AuditLog.created_at).all()
            
            # Récupérer les informations des utilisateurs
            user_ids = set(log.user_id for log in audit_logs if log.user_id)
            users = {}
            if user_ids:
                users_list = db.query(User).filter(User.id.in_(user_ids)).all()
                users = {str(user.id): user.nom_complet for user in users_list}
            
            # Construire la piste d'audit
            audit_trail = {
                "inventory_id": str(inventory_id),
                "inventory_number": inventory.inventory_number,
                "inventory_type": inventory.inventory_type,
                "status": inventory.status,
                "created_at": inventory.created_at.isoformat() if inventory.created_at else None,
                "completed_at": inventory.end_date.isoformat() if inventory.end_date else None,
                "total_events": len(audit_logs),
                "timeline": [],
                "summary": {
                    "creations": 0,
                    "modifications": 0,
                    "validations": 0,
                    "adjustments": 0
                }
            }
            
            # Analyser les événements
            for log in audit_logs:
                event = {
                    "timestamp": log.created_at.isoformat() if log.created_at else None,
                    "action": log.action,
                    "user_id": str(log.user_id) if log.user_id else None,
                    "user_name": users.get(str(log.user_id), "Unknown"),
                    "changes": log.changes,
                    "ip_address": log.ip_address,
                    "user_agent": log.user_agent
                }
                audit_trail["timeline"].append(event)
                
                # Mettre à jour le résumé
                if log.action == "create":
                    audit_trail["summary"]["creations"] += 1
                elif log.action in ["update", "modify"]:
                    audit_trail["summary"]["modifications"] += 1
                elif log.action == "validate":
                    audit_trail["summary"]["validations"] += 1
                elif log.action == "adjust":
                    audit_trail["summary"]["adjustments"] += 1
            
            # Générer le rapport selon le format demandé
            if export_format == "json":
                return audit_trail
            elif export_format == "html":
                return InventoryAudit._generate_html_report(audit_trail, inventory)
            elif export_format == "pdf":
                return InventoryAudit._generate_pdf_report(audit_trail, inventory)
            else:
                return audit_trail
                
        except Exception as e:
            logger.error(f"Erreur génération piste d'audit: {str(e)}")
            raise
    
    @staticmethod
    def _generate_html_report(audit_trail: Dict[str, Any], inventory) -> str:
        """Génère un rapport HTML"""
        html_template = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Piste d'Audit - Inventaire {inventory_number}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .header {{ background: #f5f5f5; padding: 20px; border-radius: 5px; }}
                .timeline {{ margin-top: 30px; }}
                .event {{ border-left: 3px solid #007bff; padding: 10px 20px; margin: 10px 0; background: #f8f9fa; }}
                .event-header {{ font-weight: bold; color: #007bff; }}
                .changes {{ background: white; padding: 10px; border: 1px solid #dee2e6; margin-top: 5px; }}
                .summary {{ display: flex; gap: 20px; margin-top: 20px; }}
                .summary-item {{ background: #e9ecef; padding: 15px; border-radius: 5px; flex: 1; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>Piste d'Audit - Inventaire {inventory_number}</h1>
                <p>ID: {inventory_id} | Type: {inventory_type} | Statut: {status}</p>
                <p>Créé le: {created_at} | Terminé le: {completed_at}</p>
                <p>Total événements: {total_events}</p>
            </div>
            
            <div class="summary">
                <div class="summary-item">
                    <h3>Créations</h3>
                    <p>{creations}</p>
                </div>
                <div class="summary-item">
                    <h3>Modifications</h3>
                    <p>{modifications}</p>
                </div>
                <div class="summary-item">
                    <h3>Validations</h3>
                    <p>{validations}</p>
                </div>
                <div class="summary-item">
                    <h3>Ajustements</h3>
                    <p>{adjustments}</p>
                </div>
            </div>
            
            <div class="timeline">
                <h2>Chronologie des événements</h2>
                {events}
            </div>
        </body>
        </html>
        """
        
        # Générer les événements HTML
        events_html = ""
        for event in audit_trail.get("timeline", []):
            changes_str = json.dumps(event.get("changes", {}), indent=2, ensure_ascii=False)
            events_html += f"""
                <div class="event">
                    <div class="event-header">
                        {event['timestamp']} - {event['action'].upper()} par {event['user_name']}
                    </div>
                    <p>IP: {event.get('ip_address', 'N/A')}</p>
                    <div class="changes">
                        <pre>{changes_str}</pre>
                    </div>
                </div>
            """
        
        # Remplir le template
        html_content = html_template.format(
            inventory_number=audit_trail.get("inventory_number", ""),
            inventory_id=audit_trail.get("inventory_id", ""),
            inventory_type=audit_trail.get("inventory_type", ""),
            status=audit_trail.get("status", ""),
            created_at=audit_trail.get("created_at", "N/A"),
            completed_at=audit_trail.get("completed_at", "N/A"),
            total_events=audit_trail.get("total_events", 0),
            creations=audit_trail.get("summary", {}).get("creations", 0),
            modifications=audit_trail.get("summary", {}).get("modifications", 0),
            validations=audit_trail.get("summary", {}).get("validations", 0),
            adjustments=audit_trail.get("summary", {}).get("adjustments", 0),
            events=events_html
        )
        
        return html_content
    
    @staticmethod
    def _generate_pdf_report(audit_trail: Dict[str, Any], inventory) -> str:
        """Génère un rapport PDF (implémentation basique)"""
        # Pour une vraie implémentation, utilisez reportlab ou weasyprint
        # Ici on retourne juste le JSON formaté
        return json.dumps(audit_trail, indent=2, ensure_ascii=False)
    
    @staticmethod
    def export_audit_trail_to_file(
        audit_trail: Dict[str, Any],
        output_dir: Path = None
    ) -> Path:
        """
        Exporte la piste d'audit dans un fichier
        
        Args:
            audit_trail: Données de la piste d'audit
            output_dir: Répertoire de sortie
        
        Returns:
            Chemin du fichier généré
        """
        if output_dir is None:
            output_dir = Path("audit_trails")
        
        output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"audit_trail_{audit_trail.get('inventory_number', 'unknown')}_{timestamp}.json"
        filepath = output_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(audit_trail, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Piste d'audit exportée: {filepath}")
        return filepath


class StockMovementAudit:
    """Classe pour l'audit des mouvements de stock"""
    
    @staticmethod
    def track_stock_movement(
        db: Session,
        movement_data: Dict[str, Any],
        user_id: UUID
    ):
        """
        Journalise un mouvement de stock
        
        Args:
            db: Session de base de données
            movement_data: Données du mouvement
            user_id: ID de l'utilisateur responsable
        """
        try:
            from app.models.audit_log import AuditLog
            
            audit_log = AuditLog(
                tenant_id=movement_data.get("tenant_id"),
                entity_type="stock_movement",
                entity_id=movement_data.get("id"),
                user_id=user_id,
                action="create",
                changes=movement_data,
                created_at=datetime.utcnow()
            )
            
            db.add(audit_log)
            db.commit()
            
        except Exception as e:
            logger.error(f"Erreur enregistrement audit mouvement stock: {str(e)}")
            db.rollback()


# =======================
# Mise à jour de inventory.py avec audit
# =======================

# Dans app/api/routes/inventory.py, ajoutez ces imports :
"""
from app.core.audit import InventoryAudit, StockMovementAudit
"""

# Modifiez ces fonctions pour inclure l'audit :

"""
@router.post("/", response_model=InventoryInDB)
@require_permission("inventory_manage")
def create_inventory(
    inventory_data: InventoryCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    request: Request = None  # Ajoutez ce paramètre
):
    try:
        # ... code existant ...
        
        db.commit()
        db.refresh(inventory)
        
        # AUDIT: Enregistrer la création
        InventoryAudit.track_inventory_changes(
            db=db,
            inventory_id=inventory.id,
            user_id=current_user.id,
            action="create",
            changes={"inventory_data": inventory_data.dict()},
            ip_address=request.client.host if request else None,
            user_agent=request.headers.get("user-agent") if request else None
        )
        
        logger.info(f"Inventaire créé: {inventory_number} par {current_user.nom_complet}")
        
        return inventory
        
    except Exception as e:
        # ... code existant ...
"""

"""
@router.post("/{inventory_id}/complete")
@require_permission("inventory_manage")
def complete_inventory(
    inventory_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    request: Request = None  # Ajoutez ce paramètre
):
    try:
        # ... code existant ...
        
        db.commit()
        
        # AUDIT: Enregistrer la complétion
        InventoryAudit.track_inventory_changes(
            db=db,
            inventory_id=inventory_id,
            user_id=current_user.id,
            action="complete",
            changes={
                "variance_value": float(inventory.variance_value),
                "variance_percentage": float(inventory.variance_percentage),
                "items_counted": inventory.items_counted,
                "total_items": inventory.total_items
            },
            ip_address=request.client.host if request else None,
            user_agent=request.headers.get("user-agent") if request else None
        )
        
        # Lancer la génération du rapport en arrière-plan
        background_tasks.add_task(
            generate_inventory_report,
            inventory_id=inventory_id,
            tenant_id=current_tenant.id
        )
        
        logger.info(f"Inventaire terminé: {inventory.inventory_number}")
        
        return {
            "message": "Inventaire terminé avec succès",
            "variance_value": inventory.variance_value,
            "variance_percentage": inventory.variance_percentage
        }
        
    except Exception as e:
        # ... code existant ...
"""

# Ajoutez cette nouvelle route pour l'audit :

"""
@router.get("/{inventory_id}/audit-trail")
@require_permission("inventory_audit")
def get_inventory_audit_trail(
    inventory_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    format: str = Query("json", pattern="^(json|html|pdf)$")
):
    \"\"\"
    Récupère la piste d'audit complète d'un inventaire
    \"\"\"
    inventory = db.query(PhysicalInventory).filter(
        PhysicalInventory.id == inventory_id,
        PhysicalInventory.tenant_id == current_tenant.id
    ).first()
    
    if not inventory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventaire non trouvé"
        )
    
    try:
        audit_trail = InventoryAudit.generate_audit_trail(
            db=db,
            inventory_id=inventory_id,
            export_format=format
        )
        
        if format == "json":
            from fastapi.responses import JSONResponse
            return JSONResponse(content=audit_trail)
        
        elif format == "html":
            from fastapi.responses import HTMLResponse
            html_content = InventoryAudit._generate_html_report(audit_trail, inventory)
            return HTMLResponse(content=html_content)
        
        elif format == "pdf":
            from fastapi.responses import FileResponse
            # Générer le PDF et retourner le fichier
            pdf_path = InventoryAudit.export_audit_trail_to_file(audit_trail)
            return FileResponse(
                path=pdf_path,
                filename=f"audit_trail_{inventory.inventory_number}.pdf",
                media_type="application/pdf"
            )
    
    except Exception as e:
        logger.error(f"Erreur génération piste d'audit: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération de la piste d'audit"
        )
"""


# =======================
# Modèle AuditLog (à créer)
# =======================

"""
# app/models/audit_log.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Index, JSON
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base

class AuditLog(Base):
    \"\"\"
    Modèle pour les logs d'audit
    \"\"\"
    __tablename__ = "audit_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    
    # Entité concernée
    entity_type = Column(String(50), nullable=False, index=True)
    entity_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    
    # Utilisateur responsable
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # Action
    action = Column(String(50), nullable=False, index=True)
    
    # Détails
    changes = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    
    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    __table_args__ = (
        Index("ix_audit_logs_tenant_entity", "tenant_id", "entity_type", "entity_id"),
        Index("ix_audit_logs_tenant_user", "tenant_id", "user_id"),
        Index("ix_audit_logs_tenant_date", "tenant_id", "created_at"),
    )
    
    def to_dict(self):
        return {
            "id": str(self.id),
            "entity_type": self.entity_type,
            "entity_id": str(self.entity_id),
            "user_id": str(self.user_id) if self.user_id else None,
            "action": self.action,
            "changes": self.changes,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
    
    def __repr__(self):
        return f"<AuditLog {self.entity_type}.{self.action} on {self.entity_id}>"
"""


# =======================
# Utilisation dans les services
# =======================

"""
# Dans app/services/inventory.py, modifiez update_stock :

def update_stock(self, product_id: UUID, quantity_change: int, 
                reason: str, reference: Optional[str] = None,
                reference_type: Optional[str] = None,
                user_id: Optional[UUID] = None) -> Dict[str, Any]:
    
    # ... code existant ...
    
    # AUDIT: Enregistrer le mouvement
    movement_data = {
        "id": str(movement.id),
        "tenant_id": str(self.tenant_id),
        "product_id": str(product_id),
        "product_name": product.name,
        "quantity_before": old_quantity,
        "quantity_after": new_quantity,
        "quantity_change": quantity_change,
        "movement_type": movement_type,
        "reason": reason,
        "reference": reference,
        "user_id": str(user_id) if user_id else None
    }
    
    if user_id:
        StockMovementAudit.track_stock_movement(
            db=self.db,
            movement_data=movement_data,
            user_id=user_id
        )
    
    logger.info(f"Stock mis à jour: {product.code} - {old_quantity} -> {new_quantity}")
    
    return {
        "product_id": product_id,
        "product_name": product.name,
        "old_quantity": old_quantity,
        "new_quantity": new_quantity,
        "movement_id": movement.id
    }
"""
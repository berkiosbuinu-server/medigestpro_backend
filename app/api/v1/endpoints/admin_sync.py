# app/api/v1/endpoints/admin_sync.py
"""
Endpoints API pour la synchronisation admin offline
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from pydantic import BaseModel, Field

from app.db.session import get_db
from app.services.admin_sync_service import AdminSyncService
from app.models.admin_sync import (
    AdminSyncLog, AdminSyncFilter, AdminSyncBatch,
    SyncEntityType, SyncStatus, SyncOperation
)
from app.api.deps import get_current_admin_user
from app.models.user import User

router = APIRouter(prefix="/admin/sync", tags=["Admin Synchronization"])


# ==================== SCHEMAS ====================

class ExportRequest(BaseModel):
    tenant_id: Any
    branch_ids: Optional[List[int]] = None
    entity_types: Optional[List[str]] = None
    since: Optional[datetime] = None
    include_deleted: bool = False
    
    class Config:
        json_schema_extra = {
            "example": {
                "tenant_id": 1,
                "branch_ids": [1, 2],
                "entity_types": ["product", "sale", "debt"],
                "since": "2024-01-01T00:00:00Z",
                "include_deleted": False
            }
        }


class ImportRequest(BaseModel):
    data: Dict[str, Any]
    strategy: str = Field("merge", description="merge|overwrite|skip")
    
    class Config:
        json_schema_extra = {
            "example": {
                "data": {
                    "metadata": {},
                    "data": {
                        "products": [],
                        "sales": []
                    }
                },
                "strategy": "merge"
            }
        }


class SyncFilterCreate(BaseModel):
    name: str
    description: Optional[str] = None
    entity_types: List[str]
    tenant_ids: Optional[List[int]] = None
    branch_ids: Optional[List[int]] = None
    date_range_start: Optional[datetime] = None
    date_range_end: Optional[datetime] = None
    custom_filters: Optional[Dict[str, Any]] = None


class SyncStatusResponse(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    status: str
    operation: str
    synced_at: Optional[str]
    duration_ms: Optional[int]
    data_size_bytes: Optional[int]


# ==================== ENDPOINTS ====================

@router.post("/export")
async def export_data(
    request: ExportRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Exporte toutes les données d'un tenant pour synchronisation offline admin
    (Accès réservé aux super-admins)
    """
    if current_user.role not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    service = AdminSyncService(db)
    
    # Convertir les entity_types strings en Enum
    entity_types = None
    if request.entity_types:
        entity_types = [SyncEntityType(et) for et in request.entity_types]
    
    try:
        # Exécuter l'export (peut être long, donc en background)
        def export_task():
            export_data = service.export_all_tenant_data(
                tenant_id=request.tenant_id,
                branch_ids=request.branch_ids,
                entity_types=entity_types,
                since=request.since,
                include_deleted=request.include_deleted
            )
            # Sauvegarder dans un fichier ou retourner
            # Ici on peut stocker dans Redis/FileSystem pour récupération
            pass
        
        background_tasks.add_task(export_task)
        
        return {
            "message": "Export démarré en arrière-plan",
            "tenant_id": request.tenant_id,
            "entity_types": request.entity_types,
            "status": "processing"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/export/complete")
async def export_complete_data(
    request: ExportRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Export complet (synchrone) - pour petits volumes
    """
    if current_user.role not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    service = AdminSyncService(db)
    
    entity_types = None
    if request.entity_types:
        entity_types = [SyncEntityType(et) for et in request.entity_types]
    
    try:
        export_data = service.export_all_tenant_data(
            tenant_id=request.tenant_id,
            branch_ids=request.branch_ids,
            entity_types=entity_types,
            since=request.since,
            include_deleted=request.include_deleted
        )
        
        return export_data
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/import")
async def import_data(
    request: ImportRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Importe les données modifiées par l'admin vers les tenants/branches
    """
    if current_user.role not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    service = AdminSyncService(db)
    
    try:
        result = service.import_admin_data(
            admin_user_id=current_user.id,
            import_data=request.data,
            strategy=request.strategy
        )
        
        return {
            "message": "Import terminé",
            "results": result
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status")
async def get_sync_status(
    tenant_id: Optional[int] = Query(None),
    branch_id: Optional[int] = Query(None),
    entity_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
) -> List[SyncStatusResponse]:
    """
    Récupère l'historique des synchronisations
    """
    if current_user.role not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    service = AdminSyncService(db)
    
    entity_type_enum = SyncEntityType(entity_type) if entity_type else None
    
    status_list = service.get_sync_status(
        tenant_id=tenant_id,
        branch_id=branch_id,
        entity_type=entity_type_enum,
        limit=limit
    )
    
    return status_list


@router.post("/batch")
async def create_sync_batch(
    tenant_id: int,
    branch_ids: Optional[List[int]] = None,
    entity_types: Optional[List[str]] = None,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Crée un lot de synchronisation pour export groupé
    """
    if current_user.role not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    service = AdminSyncService(db)
    
    entity_types_enum = None
    if entity_types:
        entity_types_enum = [SyncEntityType(et) for et in entity_types]
    
    batch = service.create_sync_batch(
        tenant_id=tenant_id,
        branch_ids=branch_ids,
        entity_types=entity_types_enum,
        admin_user_id=current_user.id
    )
    
    return {
        "batch_id": batch.batch_id,
        "tenant_id": batch.tenant_id,
        "status": batch.status.value,
        "created_at": batch.created_at.isoformat()
    }


@router.get("/dashboard/stats")
async def get_sync_dashboard_stats(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Statistiques globales de synchronisation pour le dashboard admin
    """
    if current_user.role not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    # Statistiques par type d'entité
    entity_stats = db.query(
        AdminSyncLog.entity_type,
        func.count(AdminSyncLog.id).label('total'),
        func.sum(case((AdminSyncLog.sync_status == SyncStatus.SYNCED, 1), else_=0)).label('synced'),
        func.sum(case((AdminSyncLog.sync_status == SyncStatus.FAILED, 1), else_=0)).label('failed'),
        func.sum(case((AdminSyncLog.sync_status == SyncStatus.CONFLICT, 1), else_=0)).label('conflicts')
    ).group_by(AdminSyncLog.entity_type).all()
    
    # Dernières synchronisations
    last_syncs = db.query(AdminSyncLog).order_by(
        AdminSyncLog.created_at.desc()
    ).limit(10).all()
    
    # Calculer le total des données
    total_size_bytes = db.query(func.sum(AdminSyncLog.data_size_bytes)).scalar()
    total_data_size_mb = (total_size_bytes / (1024 * 1024)) if total_size_bytes else 0
    
    return {
        "entity_statistics": [
            {
                "entity_type": stat.entity_type.value,
                "total": stat.total,
                "synced": stat.synced or 0,
                "failed": stat.failed or 0,
                "conflicts": stat.conflicts or 0
            }
            for stat in entity_stats
        ],
        "last_syncs": [
            {
                "id": sync.id,
                "entity_type": sync.entity_type.value,
                "status": sync.sync_status.value,
                "created_at": sync.created_at.isoformat(),
                "duration_ms": sync.sync_duration_ms
            }
            for sync in last_syncs
        ],
        "total_syncs": db.query(func.count(AdminSyncLog.id)).scalar(),
        "total_data_size_mb": round(total_data_size_mb, 2)
    }


@router.post("/filters")
async def create_sync_filter(
    filter_data: SyncFilterCreate,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Crée un filtre personnalisé pour les synchronisations
    """
    if current_user.role not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    sync_filter = AdminSyncFilter(
        name=filter_data.name,
        description=filter_data.description,
        entity_types=filter_data.entity_types,
        tenant_ids=filter_data.tenant_ids,
        branch_ids=filter_data.branch_ids,
        date_range_start=filter_data.date_range_start,
        date_range_end=filter_data.date_range_end,
        custom_filters=filter_data.custom_filters,
        admin_user_id=current_user.id
    )
    
    db.add(sync_filter)
    db.commit()
    db.refresh(sync_filter)
    
    return {
        "id": sync_filter.id,
        "name": sync_filter.name,
        "created_at": sync_filter.created_at.isoformat()
    }


@router.get("/filters")
async def get_sync_filters(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Récupère tous les filtres de l'admin
    """
    if current_user.role not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    filters = db.query(AdminSyncFilter).filter(
        AdminSyncFilter.admin_user_id == current_user.id
    ).all()
    
    return [
        {
            "id": f.id,
            "name": f.name,
            "description": f.description,
            "entity_types": f.entity_types,
            "tenant_ids": f.tenant_ids,
            "branch_ids": f.branch_ids,
            "created_at": f.created_at.isoformat()
        }
        for f in filters
    ]


@router.delete("/filters/{filter_id}")
async def delete_sync_filter(
    filter_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Supprime un filtre de synchronisation
    """
    if current_user.role not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    sync_filter = db.query(AdminSyncFilter).filter(
        AdminSyncFilter.id == filter_id,
        AdminSyncFilter.admin_user_id == current_user.id
    ).first()
    
    if not sync_filter:
        raise HTTPException(status_code=404, detail="Filtre non trouvé")
    
    db.delete(sync_filter)
    db.commit()
    
    return {"message": "Filtre supprimé avec succès"}


@router.get("/health")
async def sync_health_check(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Vérifie la santé du système de synchronisation
    """
    if current_user.role not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    # Vérifier les synchronisations en échec récentes
    recent_failed = db.query(AdminSyncLog).filter(
        AdminSyncLog.sync_status == SyncStatus.FAILED,
        AdminSyncLog.created_at >= datetime.utcnow() - timedelta(hours=24)
    ).count()
    
    # Vérifier les conflits non résolus
    unresolved_conflicts = db.query(AdminSyncLog).filter(
        AdminSyncLog.sync_status == SyncStatus.CONFLICT
    ).count()
    
    # Vérifier les synchronisations en cours
    pending_syncs = db.query(AdminSyncLog).filter(
        AdminSyncLog.sync_status == SyncStatus.PENDING
    ).count()
    
    # Dernière synchronisation réussie
    last_success = db.query(AdminSyncLog).filter(
        AdminSyncLog.sync_status == SyncStatus.SYNCED
    ).order_by(AdminSyncLog.created_at.desc()).first()
    
    return {
        "status": "healthy" if recent_failed < 10 else "degraded",
        "metrics": {
            "recent_failures_24h": recent_failed,
            "unresolved_conflicts": unresolved_conflicts,
            "pending_syncs": pending_syncs,
            "total_sync_logs": db.query(func.count(AdminSyncLog.id)).scalar(),
            "last_success_sync": last_success.created_at.isoformat() if last_success else None
        }
    }


@router.get("/batches")
async def get_sync_batches(
    tenant_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Récupère l'historique des lots de synchronisation
    """
    if current_user.role not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    query = db.query(AdminSyncBatch)
    
    if tenant_id:
        query = query.filter(AdminSyncBatch.tenant_id == tenant_id)
    
    if status:
        query = query.filter(AdminSyncBatch.status == status)
    
    batches = query.order_by(AdminSyncBatch.created_at.desc()).limit(limit).all()
    
    return [
        {
            "id": batch.id,
            "batch_id": batch.batch_id,
            "tenant_id": batch.tenant_id,
            "branch_ids": batch.branch_ids,
            "entity_types": batch.entity_types,
            "total_entities": batch.total_entities,
            "total_size_bytes": batch.total_size_bytes,
            "status": batch.status.value,
            "error_message": batch.error_message,
            "created_at": batch.created_at.isoformat(),
            "completed_at": batch.completed_at.isoformat() if batch.completed_at else None
        }
        for batch in batches
    ]


@router.post("/batches/{batch_id}/complete")
async def complete_sync_batch(
    batch_id: str,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Marque un lot de synchronisation comme complété
    """
    if current_user.role not in ["super_admin", "admin"]:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    batch = db.query(AdminSyncBatch).filter(
        AdminSyncBatch.batch_id == batch_id
    ).first()
    
    if not batch:
        raise HTTPException(status_code=404, detail="Lot non trouvé")
    
    batch.status = SyncStatus.SYNCED
    batch.completed_at = datetime.utcnow()
    
    db.commit()
    
    return {
        "message": "Lot marqué comme complété",
        "batch_id": batch.batch_id,
        "completed_at": batch.completed_at.isoformat()
    }
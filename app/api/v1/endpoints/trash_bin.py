# app/api/v1/endpoints/trash_bin.py
"""
Endpoints pour la corbeille
Gère les éléments supprimés avec possibilité de restauration
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, timedelta
import logging

from app.db.session import get_db
from app.models.trash_bin import TrashBin
from app.models.product import Product
from app.models.user import User
from app.models.tenant import Tenant
from app.models.pharmacy import Pharmacy
from app.models.user_history import UserHistory
from app.api.deps import (
    get_current_tenant,
    get_current_user,
    get_current_active_user,
    get_current_pharmacy_entity
)

router = APIRouter(prefix="/trash", tags=["Corbeille"])
logger = logging.getLogger(__name__)


# =======================
# Fonctions utilitaires
# =======================

def log_to_user_history(
    db: Session,
    tenant_id: UUID,
    user_id: UUID,
    action_type: str,
    module: str,
    entity_id: Optional[UUID],
    entity_reference: Optional[str],
    entity_name: Optional[str],
    action_description: str,
    old_data: Optional[Dict] = None,
    new_data: Optional[Dict] = None,
    ip_address: Optional[str] = None,
    status: str = "success",
    error_message: Optional[str] = None
):
    """Enregistre une action dans l'historique utilisateur"""
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
            status=status,
            error_message=error_message
        )
        db.add(history)
        db.flush()
    except Exception as e:
        logger.error(f"Erreur lors de l'enregistrement dans l'historique: {str(e)}")


def move_to_trash(
    db: Session,
    tenant_id: UUID,
    pharmacy_id: Optional[UUID],
    item_type: str,
    original_id: UUID,
    original_reference: Optional[str],
    original_name: Optional[str],
    data: Dict,
    deleted_by_id: UUID,
    deleted_by_name: str,
    deleted_by_email: str,
    deletion_reason: Optional[str] = None,
    auto_delete_days: int = 30
) -> TrashBin:
    """
    Déplace un élément supprimé dans la corbeille
    """
    trash_item = TrashBin(
        tenant_id=tenant_id,
        pharmacy_id=pharmacy_id,
        item_type=item_type,
        original_id=original_id,
        original_reference=original_reference,
        original_name=original_name,
        data=data,
        deleted_by_id=deleted_by_id,
        deleted_by_name=deleted_by_name,
        deleted_by_email=deleted_by_email,
        deletion_reason=deletion_reason,
        auto_delete_at=datetime.utcnow() + timedelta(days=auto_delete_days) if auto_delete_days > 0 else None,
        is_restored=False
    )
    db.add(trash_item)
    db.flush()
    return trash_item


# =======================
# Endpoints corbeille
# =======================

@router.get("/", status_code=status.HTTP_200_OK)
async def list_trash_items(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    item_type: Optional[str] = Query(None, description="Type d'élément (product, sale, etc.)"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    search: Optional[str] = Query(None, description="Recherche par nom ou référence"),
    start_date: Optional[datetime] = Query(None, description="Date de suppression début"),
    end_date: Optional[datetime] = Query(None, description="Date de suppression fin"),
    is_restored: Optional[bool] = Query(None, description="Éléments restaurés ou non")
):
    """
    Liste les éléments dans la corbeille
    """
    if current_user.role not in ["admin", "super_admin", "superadmin", "gestionnaire"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Seuls les administrateurs peuvent consulter la corbeille."
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    # Déterminer la pharmacie
    effective_pharmacy_id = pharmacy_id or (current_pharmacy.id if current_pharmacy else None)
    
    query = db.query(TrashBin).filter(
        TrashBin.tenant_id == tenant_id,
        TrashBin.is_restored == (is_restored if is_restored is not None else False)
    )
    
    if effective_pharmacy_id:
        query = query.filter(TrashBin.pharmacy_id == effective_pharmacy_id)
    
    if item_type:
        query = query.filter(TrashBin.item_type == item_type)
    
    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(
            or_(
                func.lower(TrashBin.original_name).like(search_term),
                func.lower(TrashBin.original_reference).like(search_term)
            )
        )
    
    if start_date:
        query = query.filter(TrashBin.deleted_at >= start_date)
    
    if end_date:
        query = query.filter(TrashBin.deleted_at <= end_date)
    
    # Pagination
    total = query.count()
    offset = (page - 1) * limit
    
    trash_items = query.order_by(desc(TrashBin.deleted_at)).offset(offset).limit(limit).all()
    
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit if total > 0 else 0,
        "items": [item.to_dict() for item in trash_items]
    }


@router.get("/{trash_id}", status_code=status.HTTP_200_OK)
async def get_trash_item(
    trash_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère un élément spécifique de la corbeille
    """
    if current_user.role not in ["admin", "super_admin", "superadmin", "gestionnaire"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    trash_item = db.query(TrashBin).filter(
        TrashBin.id == trash_id,
        TrashBin.tenant_id == tenant_id
    ).first()
    
    if not trash_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Élément non trouvé dans la corbeille"
        )
    
    return trash_item.to_dict(include_data=True)


@router.post("/{trash_id}/restore", status_code=status.HTTP_200_OK)
async def restore_trash_item(
    trash_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Restaure un élément depuis la corbeille
    """
    if current_user.role not in ["admin", "super_admin", "superadmin", "gestionnaire"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Seuls les administrateurs peuvent restaurer des éléments."
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    trash_item = db.query(TrashBin).filter(
        TrashBin.id == trash_id,
        TrashBin.tenant_id == tenant_id,
        TrashBin.is_restored == False
    ).first()
    
    if not trash_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Élément non trouvé ou déjà restauré"
        )
    
    try:
        # Restaurer selon le type d'élément
        if trash_item.item_type == "product":
            # Restaurer un produit
            product_data = trash_item.data
            
            # Vérifier si le produit existe déjà
            existing_product = db.query(Product).filter(
                Product.id == trash_item.original_id,
                Product.tenant_id == tenant_id
            ).first()
            
            if existing_product:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Le produit existe déjà. Impossible de restaurer."
                )
            
            # Créer un nouveau produit avec les données restaurées
            new_product = Product(
                id=trash_item.original_id,
                tenant_id=tenant_id,
                pharmacy_id=trash_item.pharmacy_id or product_data.get("pharmacy_id"),
                code=product_data.get("code"),
                barcode=product_data.get("barcode"),
                name=product_data.get("name"),
                description=product_data.get("description"),
                category=product_data.get("category"),
                product_type=product_data.get("product_type"),
                quantity=product_data.get("quantity", 0),
                unit=product_data.get("unit", "unité"),
                purchase_price=product_data.get("purchase_price", 0),
                selling_price=product_data.get("selling_price", 0),
                tva_rate=product_data.get("tva_rate", 0),
                has_tva=product_data.get("has_tva", False),
                is_active=True,
                created_at=product_data.get("created_at", datetime.utcnow()),
                updated_at=datetime.utcnow()
            )
            db.add(new_product)
            
        # Ajouter d'autres types d'éléments ici (sale, client, supplier, etc.)
        # elif trash_item.item_type == "sale":
        #     # Logique de restauration d'une vente
        #     pass
        
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Restauration non supportée pour le type: {trash_item.item_type}"
            )
        
        # Marquer l'élément comme restauré
        trash_item.is_restored = True
        trash_item.restored_at = datetime.utcnow()
        trash_item.restored_by_id = current_user.id
        trash_item.restored_by_name = current_user.nom_complet
        
        db.commit()
        
        # Logger l'action
        log_to_user_history(
            db=db,
            tenant_id=tenant_id,
            user_id=current_user.id,
            action_type="restore",
            module=trash_item.item_type,
            entity_id=trash_item.original_id,
            entity_reference=trash_item.original_reference,
            entity_name=trash_item.original_name,
            action_description=f"Restauration de {trash_item.item_type}: {trash_item.original_name}",
            ip_address=request.client.host if request.client else None
        )
        
        return {
            "message": f"Élément restauré avec succès",
            "item_type": trash_item.item_type,
            "item_id": str(trash_item.original_id),
            "item_name": trash_item.original_name
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la restauration: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la restauration: {str(e)}"
        )


@router.delete("/{trash_id}", status_code=status.HTTP_200_OK)
async def delete_trash_item_permanently(
    trash_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Supprime définitivement un élément de la corbeille
    """
    if current_user.role not in ["super_admin", "superadmin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Seuls les super-administrateurs peuvent supprimer définitivement."
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    trash_item = db.query(TrashBin).filter(
        TrashBin.id == trash_id,
        TrashBin.tenant_id == tenant_id
    ).first()
    
    if not trash_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Élément non trouvé"
        )
    
    item_info = {
        "type": trash_item.item_type,
        "name": trash_item.original_name,
        "deleted_by": trash_item.deleted_by_name
    }
    
    db.delete(trash_item)
    db.commit()
    
    # Logger l'action
    log_to_user_history(
        db=db,
        tenant_id=tenant_id,
        user_id=current_user.id,
        action_type="permanent_delete",
        module=trash_item.item_type,
        entity_id=trash_item.original_id,
        entity_reference=trash_item.original_reference,
        entity_name=trash_item.original_name,
        action_description=f"Suppression définitive de {trash_item.item_type}: {trash_item.original_name}",
        ip_address=request.client.host if request.client else None
    )
    
    return {
        "message": "Élément supprimé définitivement",
        "item": item_info
    }


@router.delete("/cleanup/expired", status_code=status.HTTP_200_OK)
async def cleanup_expired_trash(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Supprime automatiquement les éléments expirés de la corbeille
    """
    if current_user.role not in ["admin", "super_admin", "superadmin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    expired_items = db.query(TrashBin).filter(
        TrashBin.tenant_id == tenant_id,
        TrashBin.auto_delete_at <= datetime.utcnow(),
        TrashBin.is_restored == False
    ).all()
    
    deleted_count = len(expired_items)
    
    for item in expired_items:
        db.delete(item)
    
    db.commit()
    
    # Logger l'action
    log_to_user_history(
        db=db,
        tenant_id=tenant_id,
        user_id=current_user.id,
        action_type="cleanup",
        module="trash",
        entity_id=None,
        entity_reference=None,
        entity_name=None,
        action_description=f"Nettoyage automatique: {deleted_count} éléments supprimés de la corbeille",
        ip_address=request.client.host if request.client else None
    )
    
    return {
        "message": f"Nettoyage effectué avec succès",
        "deleted_count": deleted_count
    }


@router.get("/stats/overview", status_code=status.HTTP_200_OK)
async def get_trash_stats(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère les statistiques de la corbeille
    """
    if current_user.role not in ["admin", "super_admin", "superadmin", "gestionnaire"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    # Total par type
    items_by_type = db.query(
        TrashBin.item_type,
        func.count(TrashBin.id).label("count")
    ).filter(
        TrashBin.tenant_id == tenant_id,
        TrashBin.is_restored == False
    ).group_by(TrashBin.item_type).all()
    
    # Total par utilisateur ayant supprimé
    items_by_deleter = db.query(
        TrashBin.deleted_by_id,
        TrashBin.deleted_by_name,
        func.count(TrashBin.id).label("count")
    ).filter(
        TrashBin.tenant_id == tenant_id,
        TrashBin.is_restored == False
    ).group_by(TrashBin.deleted_by_id, TrashBin.deleted_by_name).order_by(desc("count")).limit(10).all()
    
    # Total par pharmacie
    items_by_pharmacy = db.query(
        TrashBin.pharmacy_id,
        func.count(TrashBin.id).label("count")
    ).filter(
        TrashBin.tenant_id == tenant_id,
        TrashBin.is_restored == False
    ).group_by(TrashBin.pharmacy_id).all()
    
    # Nombre total
    total = db.query(func.count(TrashBin.id)).filter(
        TrashBin.tenant_id == tenant_id,
        TrashBin.is_restored == False
    ).scalar() or 0
    
    return {
        "total_items": total,
        "items_by_type": [{"type": t, "count": c} for t, c in items_by_type],
        "top_deleters": [
            {
                "user_id": str(user_id) if user_id else None,
                "user_name": name,
                "deleted_count": count
            }
            for user_id, name, count in items_by_deleter
        ],
        "items_by_pharmacy": [
            {"pharmacy_id": str(pid) if pid else None, "count": c}
            for pid, c in items_by_pharmacy
        ],
        "timestamp": datetime.utcnow().isoformat()
    }
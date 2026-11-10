# app/api/v1/endpoints/user_history.py
"""
Endpoints pour l'historique des actions utilisateurs
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, date, timedelta
import logging

from app.db.session import get_db
from app.models.user_history import UserHistory
from app.models.user import User
from app.models.tenant import Tenant
from app.api.deps import (
    get_current_tenant,
    get_current_user,
    get_current_active_user,
    require_role,
    require_permission
)

router = APIRouter(prefix="/user-history", tags=["Historique Utilisateurs"])
logger = logging.getLogger(__name__)


# =======================
# Endpoints d'historique
# =======================

@router.get("/", status_code=status.HTTP_200_OK)
async def list_user_history(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    page: int = Query(1, ge=1, description="Numéro de page"),
    limit: int = Query(50, ge=1, le=500, description="Nombre d'éléments par page"),
    user_id: Optional[UUID] = Query(None, description="Filtrer par utilisateur"),
    action_type: Optional[str] = Query(None, description="Filtrer par type d'action"),
    module: Optional[str] = Query(None, description="Filtrer par module"),
    entity_id: Optional[UUID] = Query(None, description="Filtrer par ID d'entité"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    status: Optional[str] = Query(None, description="Filtrer par statut (success, error, etc.)"),
    search: Optional[str] = Query(None, description="Recherche textuelle"),
    sort_by: str = Query("created_at", description="Tri par colonne"),
    sort_order: str = Query("desc", description="Ordre de tri (asc/desc)")
):
    """
    Liste l'historique des actions utilisateurs
    Accès réservé aux admins et gestionnaires
    """
    # Vérifier les permissions
    if current_user.role not in ["admin", "super_admin", "superadmin", "gestionnaire"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Seuls les administrateurs et gestionnaires peuvent consulter l'historique."
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    # Construire la requête
    query = db.query(UserHistory).filter(UserHistory.tenant_id == tenant_id)
    
    # Appliquer les filtres
    if user_id:
        query = query.filter(UserHistory.user_id == user_id)
    
    if action_type:
        query = query.filter(UserHistory.action_type == action_type)
    
    if module:
        query = query.filter(UserHistory.module == module)
    
    if entity_id:
        query = query.filter(UserHistory.entity_id == entity_id)
    
    if start_date:
        query = query.filter(func.date(UserHistory.created_at) >= start_date)
    
    if end_date:
        query = query.filter(func.date(UserHistory.created_at) <= end_date)
    
    if status:
        query = query.filter(UserHistory.status == status)
    
    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(
            or_(
                UserHistory.action_description.ilike(search_term),
                UserHistory.entity_reference.ilike(search_term),
                UserHistory.entity_name.ilike(search_term)
            )
        )
    
    # Pagination
    total = query.count()
    offset = (page - 1) * limit
    
    # Tri
    if sort_by in ["created_at", "action_type", "module", "status"]:
        sort_column = getattr(UserHistory, sort_by)
        if sort_order == "desc":
            sort_column = desc(sort_column)
        query = query.order_by(sort_column)
    else:
        query = query.order_by(desc(UserHistory.created_at))
    
    # Récupérer les résultats
    history_items = query.offset(offset).limit(limit).all()
    
    # Ajouter les infos utilisateur
    results = []
    for item in history_items:
        item_dict = item.to_dict()
        results.append(item_dict)
    
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit if total > 0 else 0,
        "items": results
    }


@router.get("/users/{user_id}", status_code=status.HTTP_200_OK)
async def get_user_history(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None)
):
    """
    Récupère l'historique d'un utilisateur spécifique
    """
    if current_user.role not in ["admin", "super_admin", "superadmin", "gestionnaire"]:
        # Un utilisateur peut voir son propre historique
        if str(current_user.id) != str(user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous ne pouvez voir que votre propre historique"
            )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    # Vérifier que l'utilisateur existe dans le tenant
    target_user = db.query(User).filter(
        User.id == user_id,
        User.tenant_id == tenant_id
    ).first()
    
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur non trouvé"
        )
    
    # Construire la requête
    query = db.query(UserHistory).filter(
        UserHistory.tenant_id == tenant_id,
        UserHistory.user_id == user_id
    )
    
    if start_date:
        query = query.filter(func.date(UserHistory.created_at) >= start_date)
    
    if end_date:
        query = query.filter(func.date(UserHistory.created_at) <= end_date)
    
    # Pagination
    total = query.count()
    offset = (page - 1) * limit
    
    history_items = query.order_by(desc(UserHistory.created_at)).offset(offset).limit(limit).all()
    
    return {
        "user_id": str(user_id),
        "user_name": target_user.nom_complet,
        "user_email": target_user.email,
        "user_role": target_user.role,
        "total_actions": total,
        "page": page,
        "limit": limit,
        "items": [item.to_dict() for item in history_items]
    }


@router.get("/modules", status_code=status.HTTP_200_OK)
async def get_available_modules(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère la liste des modules disponibles dans l'historique
    """
    if current_user.role not in ["admin", "super_admin", "superadmin", "gestionnaire"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    modules = db.query(UserHistory.module).filter(
        UserHistory.tenant_id == tenant_id
    ).distinct().all()
    
    return {"modules": [m[0] for m in modules if m[0]]}


@router.get("/action-types", status_code=status.HTTP_200_OK)
async def get_available_action_types(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère la liste des types d'actions disponibles
    """
    if current_user.role not in ["admin", "super_admin", "superadmin", "gestionnaire"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    action_types = db.query(UserHistory.action_type).filter(
        UserHistory.tenant_id == tenant_id
    ).distinct().all()
    
    return {"action_types": [a[0] for a in action_types if a[0]]}


@router.get("/stats", status_code=status.HTTP_200_OK)
async def get_user_history_stats(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    days: int = Query(30, ge=1, le=365, description="Nombre de jours")
):
    """
    Récupère les statistiques de l'historique
    """
    if current_user.role not in ["admin", "super_admin", "superadmin", "gestionnaire"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    start_date = datetime.utcnow() - timedelta(days=days)
    
    # Total par type d'action
    actions_by_type = db.query(
        UserHistory.action_type,
        func.count(UserHistory.id).label("count")
    ).filter(
        UserHistory.tenant_id == tenant_id,
        UserHistory.created_at >= start_date
    ).group_by(UserHistory.action_type).all()
    
    # Total par module
    actions_by_module = db.query(
        UserHistory.module,
        func.count(UserHistory.id).label("count")
    ).filter(
        UserHistory.tenant_id == tenant_id,
        UserHistory.created_at >= start_date
    ).group_by(UserHistory.module).all()
    
    # Top utilisateurs actifs
    top_users = db.query(
        UserHistory.user_id,
        func.count(UserHistory.id).label("count")
    ).filter(
        UserHistory.tenant_id == tenant_id,
        UserHistory.created_at >= start_date
    ).group_by(UserHistory.user_id).order_by(desc("count")).limit(10).all()
    
    # Récupérer les noms des utilisateurs
    top_users_data = []
    for user_id, count in top_users:
        user = db.query(User).filter(User.id == user_id).first()
        top_users_data.append({
            "user_id": str(user_id),
            "user_name": user.nom_complet if user else "Inconnu",
            "user_email": user.email if user else "Inconnu",
            "actions_count": count
        })
    
    # Activité par jour
    daily_activity = db.query(
        func.date(UserHistory.created_at).label("date"),
        func.count(UserHistory.id).label("count")
    ).filter(
        UserHistory.tenant_id == tenant_id,
        UserHistory.created_at >= start_date
    ).group_by(func.date(UserHistory.created_at)).order_by("date").all()
    
    return {
        "period_days": days,
        "start_date": start_date.isoformat(),
        "end_date": datetime.utcnow().isoformat(),
        "total_actions": sum(a[1] for a in actions_by_type),
        "actions_by_type": [{"type": t, "count": c} for t, c in actions_by_type],
        "actions_by_module": [{"module": m, "count": c} for m, c in actions_by_module],
        "top_active_users": top_users_data,
        "daily_activity": [{"date": d.isoformat(), "count": c} for d, c in daily_activity]
    }


@router.delete("/cleanup", status_code=status.HTTP_200_OK)
async def cleanup_old_history(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    days: int = Query(90, ge=30, le=730, description="Nombre de jours à conserver")
):
    """
    Nettoie l'historique plus ancien que le nombre de jours spécifié
    Accès réservé aux super-admins
    """
    if current_user.role not in ["super_admin", "superadmin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Seuls les super-administrateurs peuvent nettoyer l'historique."
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    
    deleted_count = db.query(UserHistory).filter(
        UserHistory.tenant_id == tenant_id,
        UserHistory.created_at < cutoff_date
    ).delete()
    
    db.commit()
    
    # Logger l'action
    logger.info(f"Nettoyage historique: {deleted_count} entrées supprimées (plus de {days} jours)")
    
    return {
        "message": f"Nettoyage effectué avec succès",
        "deleted_count": deleted_count,
        "retention_days": days,
        "cutoff_date": cutoff_date.isoformat()
    }



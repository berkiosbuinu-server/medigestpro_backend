# app/api/v1/endpoints/sellers.py
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
import logging

from app.db.session import get_db
from app.models.user import User
from app.models.branch import Branch
from app.models.user_branch import UserBranch
from app.models.tenant import Tenant
from app.api.deps import get_current_tenant, get_current_active_user

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/sellers")
async def get_sellers(
    # Utiliser str pour éviter les erreurs de validation UUID
    branch_id: Optional[str] = Query(None, description="Filtrer par branche (UUID)"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """
    Récupère la liste des vendeurs/caissiers.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Convertir branch_id en UUID si fourni
        branch_uuid = None
        if branch_id:
            try:
                branch_uuid = UUID(branch_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Format d'ID de branche invalide: {branch_id}"
                )
        
        # Rôles autorisés pour la vente
        allowed_roles = ["vendeur", "caissier", "gerant", "admin", "super_admin", "superadmin"]
        
        # Requête de base
        query = db.query(User).filter(
            User.actif == True,
            User.role.in_(allowed_roles)
        )
        
        if tenant_id:
            query = query.filter(User.tenant_id == tenant_id)
        
        # Filtrer par branche si spécifiée
        if branch_uuid:
            # Vérifier que la branche existe
            branch = db.query(Branch).filter(Branch.id == branch_uuid).first()
            if not branch:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Branche non trouvée: {branch_id}"
                )
            
            # L'utilisateur peut être associé à la branche de deux façons:
            # 1. Directement via branch_id
            # 2. Via la table d'association user_branches
            query = query.filter(
                or_(
                    User.branch_id == branch_uuid,
                    User.id.in_(
                        db.query(UserBranch.user_id).filter(
                            UserBranch.branch_id == branch_uuid,
                            UserBranch.is_active == True
                        )
                    )
                )
            )
        
        users = query.order_by(User.nom_complet, User.email).all()
        
        # Formater la réponse
        sellers_list = []
        for user in users:
            sellers_list.append({
                "id": str(user.id),
                "email": user.email,
                "nom_complet": user.nom_complet,
                "name": user.nom_complet,
                "role": user.role,
                "is_active": user.actif,
                "branch_id": str(user.branch_id) if user.branch_id else None,
            })
        
        return {
            "success": True,
            "users": sellers_list,
            "sellers": sellers_list,
            "total": len(sellers_list)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération vendeurs: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération vendeurs: {str(e)}"
        )
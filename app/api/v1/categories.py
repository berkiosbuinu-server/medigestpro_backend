# app/api/v1/categories.py
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
import logging

from app.db.session import get_db
from app.models.category import Category
from app.models.user import User
from app.api.deps import get_current_active_user, get_current_tenant
from app.models.tenant import Tenant
from app.schemas.category import CategoryCreate, CategoryUpdate, CategoryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/categories", tags=["Categories"])


# Ajoutez ces endpoints avec et sans slash
@router.get("", status_code=status.HTTP_200_OK, response_model=List[CategoryResponse])
@router.get("/", status_code=status.HTTP_200_OK, response_model=List[CategoryResponse])
def list_categories(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    active_only: bool = Query(True, description="Filtrer les catégories actives uniquement")
):
    """
    Liste toutes les catégories du tenant.
    """
    query = db.query(Category).filter(Category.tenant_id == current_tenant.id)
    
    if active_only:
        query = query.filter(Category.is_active == True)
    
    categories = query.order_by(Category.name).all()
    
    return [
        CategoryResponse(
            id=cat.id,
            name=cat.name,
            description=cat.description,
            icon=cat.icon,
            color=cat.color,
            is_active=cat.is_active,
            parent_id=cat.parent_id,
            created_at=cat.created_at,
            updated_at=cat.updated_at
        )
        for cat in categories
    ]

@router.get("/{category_id}", status_code=status.HTTP_200_OK, response_model=CategoryResponse)
def get_category(
    category_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère les détails d'une catégorie spécifique.
    """
    category = db.query(Category).filter(
        Category.id == category_id,
        Category.tenant_id == current_tenant.id
    ).first()
    
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catégorie non trouvée"
        )
    
    return CategoryResponse(
        id=category.id,
        name=category.name,
        description=category.description,
        icon=category.icon,
        color=category.color,
        is_active=category.is_active,
        parent_id=category.parent_id,
        created_at=category.created_at,
        updated_at=category.updated_at
    )


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=CategoryResponse)
def create_category(
    category_data: CategoryCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Crée une nouvelle catégorie.
    """
    # Vérifier les permissions
    if current_user.role not in ["admin", "super_admin", "gestionnaire"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Seuls les administrateurs peuvent créer des catégories."
        )
    
    # Vérifier si une catégorie avec le même nom existe déjà
    existing = db.query(Category).filter(
        Category.name == category_data.name,
        Category.tenant_id == current_tenant.id
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Une catégorie avec ce nom existe déjà"
        )
    
    # Vérifier la catégorie parente si spécifiée
    parent_category = None
    if category_data.parent_id:
        parent_category = db.query(Category).filter(
            Category.id == category_data.parent_id,
            Category.tenant_id == current_tenant.id
        ).first()
        
        if not parent_category:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Catégorie parente non trouvée"
            )
    
    # Créer la catégorie
    new_category = Category(
        tenant_id=current_tenant.id,
        name=category_data.name,
        description=category_data.description,
        icon=category_data.icon,
        color=category_data.color,
        is_active=category_data.is_active,
        parent_id=category_data.parent_id
    )
    
    db.add(new_category)
    db.commit()
    db.refresh(new_category)
    
    logger.info(f"Catégorie créée: {new_category.name} par {current_user.email}")
    
    return CategoryResponse(
        id=new_category.id,
        name=new_category.name,
        description=new_category.description,
        icon=new_category.icon,
        color=new_category.color,
        is_active=new_category.is_active,
        parent_id=new_category.parent_id,
        created_at=new_category.created_at,
        updated_at=new_category.updated_at
    )


@router.put("/{category_id}", status_code=status.HTTP_200_OK, response_model=CategoryResponse)
def update_category(
    category_id: UUID,
    category_data: CategoryUpdate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Met à jour une catégorie existante.
    """
    # Vérifier les permissions
    if current_user.role not in ["admin", "super_admin", "gestionnaire"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Seuls les administrateurs peuvent modifier des catégories."
        )
    
    category = db.query(Category).filter(
        Category.id == category_id,
        Category.tenant_id == current_tenant.id
    ).first()
    
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catégorie non trouvée"
        )
    
    # Vérifier le nom unique si modifié
    if category_data.name and category_data.name != category.name:
        existing = db.query(Category).filter(
            Category.name == category_data.name,
            Category.tenant_id == current_tenant.id,
            Category.id != category_id
        ).first()
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Une catégorie avec ce nom existe déjà"
            )
    
    # Mettre à jour les champs
    if category_data.name is not None:
        category.name = category_data.name
    if category_data.description is not None:
        category.description = category_data.description
    if category_data.icon is not None:
        category.icon = category_data.icon
    if category_data.color is not None:
        category.color = category_data.color
    if category_data.is_active is not None:
        category.is_active = category_data.is_active
    if category_data.parent_id is not None:
        # Vérifier qu'on ne crée pas une boucle
        if category_data.parent_id == category_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Une catégorie ne peut pas être son propre parent"
            )
        
        if category_data.parent_id:
            parent = db.query(Category).filter(
                Category.id == category_data.parent_id,
                Category.tenant_id == current_tenant.id
            ).first()
            
            if not parent:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Catégorie parente non trouvée"
                )
        
        category.parent_id = category_data.parent_id
    
    db.commit()
    db.refresh(category)
    
    logger.info(f"Catégorie mise à jour: {category.name} par {current_user.email}")
    
    return CategoryResponse(
        id=category.id,
        name=category.name,
        description=category.description,
        icon=category.icon,
        color=category.color,
        is_active=category.is_active,
        parent_id=category.parent_id,
        created_at=category.created_at,
        updated_at=category.updated_at
    )


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Supprime une catégorie (uniquement si elle n'a pas de produits associés).
    """
    # Vérifier les permissions
    if current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Seuls les administrateurs peuvent supprimer des catégories."
        )
    
    category = db.query(Category).filter(
        Category.id == category_id,
        Category.tenant_id == current_tenant.id
    ).first()
    
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catégorie non trouvée"
        )
    
    # Vérifier s'il y a des produits associés
    from app.models.product import Product
    products_count = db.query(Product).filter(
        Product.category_id == category_id,
        Product.tenant_id == current_tenant.id
    ).count()
    
    if products_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Impossible de supprimer cette catégorie car elle contient {products_count} produits"
        )
    
    # Vérifier les sous-catégories
    subcategories = db.query(Category).filter(
        Category.parent_id == category_id,
        Category.tenant_id == current_tenant.id
    ).count()
    
    if subcategories > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Impossible de supprimer cette catégorie car elle contient {subcategories} sous-catégories"
        )
    
    db.delete(category)
    db.commit()
    
    logger.info(f"Catégorie supprimée: {category.name} par {current_user.email}")
    
    return None
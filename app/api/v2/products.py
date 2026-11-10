# app/api/v1/endpoints/products.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID
import logging

from app.db.session import get_db
from app.models.product import Product
from app.models.stock_movement import StockMovement
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.stock import (
    ProductCreate, ProductUpdate, ProductInDB, ProductResponse,
    ProductListResponse, ProductSearch
)
from app.api.deps import get_current_tenant, get_current_user, get_current_pharmacy
from app.core.security import require_permission

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/products", response_model=ProductListResponse, summary="Liste des produits")
@require_permission("view_stock")
async def list_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = None,
    category: Optional[str] = None,
    stock_status: Optional[str] = None,
    expiry_status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    current_pharmacy: dict = Depends(get_current_pharmacy)
):
    """
    Récupère la liste des produits avec pagination et filtres.
    Compatible avec l'interface existante.
    """
    try:
        # Construire la requête de base
        query = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        )
        
        # Filtrer par pharmacie
        if current_pharmacy:
            query = query.filter(Product.pharmacy_id == current_pharmacy.get('id'))
        
        # Appliquer les filtres
        if search:
            query = query.filter(
                (Product.name.ilike(f"%{search}%")) |
                (Product.code.ilike(f"%{search}%")) |
                (Product.barcode.ilike(f"%{search}%")) |
                (Product.commercial_name.ilike(f"%{search}%"))
            )
        
        if category:
            query = query.filter(Product.category_id == category)
        
        if stock_status:
            query = query.filter(Product.stock_status == stock_status)
        
        if expiry_status:
            query = query.filter(Product.expiry_status == expiry_status)
        
        # Compter le total
        total = query.count()
        
        # Récupérer les produits avec pagination
        products = query.order_by(Product.name).offset(skip).limit(limit).all()
        
        # Calculer les statistiques
        stats = {
            "total_products": total,
            "total_value_purchase": sum(p.purchase_value for p in products),
            "total_value_selling": sum(p.selling_value for p in products),
            "out_of_stock": len([p for p in products if p.stock_status == "out_of_stock"]),
            "low_stock": len([p for p in products if p.stock_status == "low_stock"]),
            "expired_soon": len([p for p in products if p.expiry_status in ["critical", "warning"]])
        }
        
        # Convertir en schéma Pydantic
        product_list = [ProductInDB.from_orm(p) for p in products]
        
        return ProductListResponse(
            total=total,
            page=skip // limit + 1 if limit > 0 else 1,
            limit=limit,
            products=product_list,
            summary=stats
        )
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des produits: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

@router.post("/products", response_model=ProductResponse, summary="Créer un produit")
@require_permission("manage_stock")
async def create_product(
    product_data: ProductCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    current_pharmacy: dict = Depends(get_current_pharmacy)
):
    """
    Crée un nouveau produit dans le stock.
    """
    try:
        # Vérifier si un produit avec le même code existe déjà
        if product_data.code:
            existing_product = db.query(Product).filter(
                Product.tenant_id == current_tenant.id,
                Product.code == product_data.code,
                Product.is_active == True
            ).first()
            
            if existing_product:
                raise HTTPException(
                    status_code=400,
                    detail=f"Un produit avec le code {product_data.code} existe déjà"
                )
        
        # Vérifier le code-barres
        if product_data.barcode:
            existing_barcode = db.query(Product).filter(
                Product.barcode == product_data.barcode,
                Product.tenant_id == current_tenant.id,
                Product.is_active == True
            ).first()
            
            if existing_barcode:
                raise HTTPException(
                    status_code=400,
                    detail=f"Le code-barres {product_data.barcode} est déjà utilisé"
                )
        
        # Créer le produit
        product = Product(
            **product_data.dict(exclude_unset=True),
            tenant_id=current_tenant.id,
            pharmacy_id=current_pharmacy.get('id') if current_pharmacy else None,
            available_quantity=product_data.quantity
        )
        
        # Mettre à jour les statuts
        product.update_stock_status()
        product.update_expiry_status()
        
        db.add(product)
        db.commit()
        db.refresh(product)
        
        # Créer un mouvement de stock initial
        movement = StockMovement(
            tenant_id=current_tenant.id,
            product_id=product.id,
            quantity_before=0,
            quantity_after=product.quantity,
            quantity_change=product.quantity,
            movement_type="initial",
            reason="Création du produit",
            created_by=current_user.id
        )
        db.add(movement)
        db.commit()
        
        logger.info(f"Produit créé: {product.name} par {current_user.email}")
        
        return ProductResponse(
            message="Produit créé avec succès",
            product=ProductInDB.from_orm(product)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création produit: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur: {str(e)}")

@router.get("/products/{product_id}", response_model=ProductInDB, summary="Détails d'un produit")
@require_permission("view_stock")
async def get_product(
    product_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les détails d'un produit spécifique.
    """
    product = db.query(Product).filter(
        Product.id == product_id,
        Product.tenant_id == current_tenant.id,
        Product.is_active == True
    ).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    
    return ProductInDB.from_orm(product)

@router.put("/products/{product_id}", response_model=ProductResponse, summary="Modifier un produit")
@require_permission("manage_stock")
async def update_product(
    product_id: UUID,
    product_data: ProductUpdate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Modifie un produit existant.
    """
    try:
        product = db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        ).first()
        
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        # Sauvegarder l'ancienne quantité
        old_quantity = product.quantity
        
        # Mettre à jour les champs
        update_data = product_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(product, field, value)
        
        # Mettre à jour les statuts
        product.update_stock_status()
        product.update_expiry_status()
        
        # Créer un mouvement de stock si la quantité a changé
        if 'quantity' in update_data and update_data['quantity'] != old_quantity:
            movement = StockMovement(
                tenant_id=current_tenant.id,
                product_id=product.id,
                quantity_before=old_quantity,
                quantity_after=product.quantity,
                quantity_change=product.quantity - old_quantity,
                movement_type="adjustment",
                reason="Mise à jour manuelle",
                created_by=current_user.id
            )
            db.add(movement)
        
        db.commit()
        db.refresh(product)
        
        logger.info(f"Produit modifié: {product.name} par {current_user.email}")
        
        return ProductResponse(
            message="Produit mis à jour avec succès",
            product=ProductInDB.from_orm(product)
        )
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur modification produit: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur: {str(e)}")

@router.delete("/products/{product_id}", summary="Supprimer un produit")
@require_permission("manage_stock")
async def delete_product(
    product_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Supprime un produit (soft delete).
    """
    try:
        product = db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == current_tenant.id
        ).first()
        
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        # Vérifier si le produit a des mouvements de stock
        has_movements = db.query(StockMovement).filter(
            StockMovement.product_id == product_id
        ).first() is not None
        
        if has_movements:
            # Soft delete seulement
            product.is_active = False
            product.is_available = False
            message = "Produit désactivé (a des mouvements associés)"
        else:
            # Soft delete également pour conserver l'historique
            product.is_active = False
            product.is_available = False
            message = "Produit désactivé"
        
        db.commit()
        
        logger.info(f"Produit supprimé/désactivé: {product.name} par {current_user.email}")
        
        return {"message": message, "product_id": str(product_id)}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur suppression produit: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur: {str(e)}")
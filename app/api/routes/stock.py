# app/api/routes/stock.py
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timedelta
import logging

from app.db.session import get_db
from app.models.product import Product
from app.models.stock_movement import StockMovement, InventoryCount
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.stock import (
    ProductCreate, ProductResponse, ProductInDB, ProductUpdate,
    ProductListResponse, StockAdjustment, StockMovementFilter,
    InventoryCountRequest, ProductSearch, StockStats, ProductMergeRequest,
    ExportFormat
)
from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission
from app.services.inventory import InventoryService
from app.services.reporting import ReportService

# Import de ExportService pour l'export de stock
try:
    from app.services.export import ExportService
except ImportError:
    # Si le module n'existe pas encore, définir un stub pour éviter les erreurs Pylance
    class ExportService:
        def __init__(self, tenant):
            self.tenant = tenant
        def export_stock(self, data, export_format, user_id):
            pass

router = APIRouter(prefix="/stock", tags=["Stock"])
logger = logging.getLogger(__name__)

# -----------------------------
# Création d’un produit
# -----------------------------
@router.post("/products", response_model=ProductResponse)
@require_permission("gestion_stock")
def create_product(
    product_data: ProductCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Crée un nouveau produit"""
    existing_product = db.query(Product).filter(
        Product.tenant_id == current_tenant.id,
        Product.code == product_data.code
    ).first()
    
    if existing_product:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Un produit avec le code {product_data.code} existe déjà"
        )
    
    if product_data.barcode:
        existing_barcode = db.query(Product).filter(
            Product.barcode == product_data.barcode
        ).first()
        if existing_barcode:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Le code-barres {product_data.barcode} est déjà utilisé"
            )
    
    try:
        product = Product(
            tenant_id=current_tenant.id,
            **product_data.dict(exclude={"expiry_date"})
        )
        if product_data.expiry_date:
            product.expiry_date = product_data.expiry_date
        
        db.add(product)
        db.commit()
        db.refresh(product)
        
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
        
        logger.info(f"Produit créé: {product.code} - {product.name} par {current_user.nom_complet}")
        return ProductResponse(message="Produit créé avec succès", product=product)
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création du produit: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la création du produit"
        )

# -----------------------------
# Liste des produits
# -----------------------------
@router.get("/products", response_model=ProductListResponse)
@require_permission("gestion_stock")
def list_products(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[ProductSearch] = None
):
    """Liste les produits avec filtres"""
    query = db.query(Product).filter(Product.tenant_id == current_tenant.id)
    
    if search:
        if search.query:
            query = query.filter(
                (Product.name.ilike(f"%{search.query}%")) |
                (Product.code.ilike(f"%{search.query}%")) |
                (Product.barcode == search.query) |
                (Product.commercial_name.ilike(f"%{search.query}%"))
            )
        if search.category:
            query = query.filter(Product.category == search.category)
        if search.supplier:
            query = query.filter(Product.main_supplier.ilike(f"%{search.supplier}%"))
        if search.stock_status:
            if search.stock_status == "out_of_stock":
                query = query.filter(Product.quantity <= 0)
            elif search.stock_status == "low_stock":
                query = query.filter((Product.quantity > 0) & (Product.quantity <= Product.alert_threshold))
        if search.expiry_status:
            today = datetime.utcnow().date()
            if search.expiry_status == "expired":
                query = query.filter(Product.expiry_date < today)
            elif search.expiry_status == "critical":
                query = query.filter((Product.expiry_date >= today) & (Product.expiry_date <= today + timedelta(days=7)))
            elif search.expiry_status == "warning":
                query = query.filter((Product.expiry_date >= today + timedelta(days=8)) & (Product.expiry_date <= today + timedelta(days=30)))
    
    total = query.count()
    products = query.order_by(Product.name).offset(skip).limit(limit).all()
    
    summary = {
        "total_products": total,
        "total_value_purchase": sum(p.purchase_value for p in products),
        "total_value_selling": sum(p.selling_value for p in products),
        "out_of_stock": len([p for p in products if p.stock_status == "out_of_stock"]),
        "low_stock": len([p for p in products if p.stock_status == "low_stock"]),
        "expired_soon": len([p for p in products if p.expiry_status in ["critical", "warning"]])
    }
    
    return ProductListResponse(total=total, page=skip // limit + 1, limit=limit, products=products, summary=summary)

# -----------------------------
# Récupération, mise à jour et suppression d’un produit
# -----------------------------
@router.get("/products/{product_id}", response_model=ProductInDB)
@require_permission("gestion_stock")
def get_product(product_id: UUID, db: Session = Depends(get_db),
                current_tenant: Tenant = Depends(get_current_tenant),
                current_user: User = Depends(get_current_user)):
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == current_tenant.id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produit non trouvé")
    return product

@router.put("/products/{product_id}", response_model=ProductResponse)
@require_permission("gestion_stock")
def update_product(product_id: UUID, product_update: ProductUpdate, db: Session = Depends(get_db),
                   current_tenant: Tenant = Depends(get_current_tenant),
                   current_user: User = Depends(get_current_user)):
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == current_tenant.id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produit non trouvé")
    
    try:
        old_quantity = product.quantity
        update_data = product_update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(product, field, value)
        
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
        logger.info(f"Produit mis à jour: {product.code} par {current_user.nom_complet}")
        return ProductResponse(message="Produit mis à jour avec succès", product=product)
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la mise à jour du produit: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erreur lors de la mise à jour du produit")

@router.delete("/products/{product_id}")
@require_permission("gestion_stock")
def delete_product(product_id: UUID, db: Session = Depends(get_db),
                   current_tenant: Tenant = Depends(get_current_tenant),
                   current_user: User = Depends(get_current_user)):
    product = db.query(Product).filter(Product.id == product_id, Product.tenant_id == current_tenant.id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produit non trouvé")
    
    has_movements = db.query(StockMovement).filter(StockMovement.product_id == product_id).first() is not None
    if has_movements:
        product.is_active = False
        db.commit()
        logger.info(f"Produit désactivé: {product.code} par {current_user.nom_complet}")
        return {"message": "Produit désactivé (impossible de supprimer un produit avec historique)"}
    else:
        db.delete(product)
        db.commit()
        logger.info(f"Produit supprimé: {product.code} par {current_user.nom_complet}")
        return {"message": "Produit supprimé avec succès"}

# -----------------------------
# Export du stock
# -----------------------------
@router.post("/export")
@require_permission("gestion_stock")
def export_stock(
    export_format: ExportFormat = ExportFormat.EXCEL,
    search: Optional[ProductSearch] = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    background_tasks: BackgroundTasks = None
):
    """Exporte le stock dans différents formats"""
    query = db.query(Product).filter(Product.tenant_id == current_tenant.id, Product.is_active == True)
    if search:
        if search.query:
            query = query.filter((Product.name.ilike(f"%{search.query}%")) | (Product.code.ilike(f"%{search.query}%")))
        if search.category:
            query = query.filter(Product.category == search.category)
        if search.stock_status:
            if search.stock_status == "out_of_stock":
                query = query.filter(Product.quantity <= 0)
            elif search.stock_status == "low_stock":
                query = query.filter((Product.quantity > 0) & (Product.quantity <= Product.alert_threshold))
    
    products = query.order_by(Product.name).all()
    export_data = [
        {
            "code": p.code,
            "barcode": p.barcode or "",
            "name": p.name,
            "commercial_name": p.commercial_name or "",
            "category": p.category or "",
            "quantity": p.quantity,
            "unit": p.unit,
            "purchase_price": p.purchase_price,
            "selling_price": p.selling_price,
            "purchase_value": p.purchase_value,
            "selling_value": p.selling_value,
            "margin_rate": p.margin_rate,
            "expiry_date": p.expiry_date.strftime("%Y-%m-%d") if p.expiry_date else "",
            "batch_number": p.batch_number or "",
            "main_supplier": p.main_supplier or "",
            "location": p.location or "",
            "stock_status": p.stock_status,
            "expiry_status": p.expiry_status
        } for p in products
    ]
    
    if background_tasks:
        export_service = ExportService(current_tenant)
        background_tasks.add_task(export_service.export_stock, data=export_data, export_format=export_format, user_id=current_user.id)
        return {"message": "Export démarré en arrière-plan", "format": export_format.value, "item_count": len(export_data)}
    
    return {"data": export_data, "format": export_format.value, "count": len(export_data)}

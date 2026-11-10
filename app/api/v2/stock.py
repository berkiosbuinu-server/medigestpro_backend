# app/api/v1/stock.py
# app/api/v1/stock.py
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID
from app.db.session import get_db
from app.models.product import Product
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.stock import ProductSearch, ExportFormat
from app.api.deps import get_current_tenant, get_current_user
from app.services.export import ExportService
import logging
from functools import wraps

router = APIRouter(prefix="/stock", tags=["Stock"])
logger = logging.getLogger(__name__)

# --- Décorateur de permission corrigé ---
def require_permission(permission_name: str):
    """
    Décorateur pour vérifier les permissions sur les routes FastAPI
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(
            *args,
            current_user: User = Depends(get_current_user),
            **kwargs
        ):
            # Vérification de permission
            # Adaptez cette logique selon votre modèle de permissions
            user_permissions = getattr(current_user, "permissions", [])
            
            if permission_name not in user_permissions:
                raise HTTPException(
                    status_code=403, 
                    detail=f"Permission refusée: {permission_name} requise"
                )
            return await func(*args, current_user=current_user, **kwargs)
        return wrapper
    return decorator

# --- Routes ---
@router.get("/test")
def test_stock():
    return {"message": "Stock API fonctionne !"}

@router.post("/export")
@require_permission("gestion_stock")
async def export_stock(
    export_format: ExportFormat = ExportFormat.EXCEL,
    search: Optional[ProductSearch] = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    background_tasks: BackgroundTasks = None,
):
    """
    Exporte le stock dans différents formats
    """
    try:
        # Construire la requête
        query = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        )

        # Appliquer les filtres de recherche
        if search and search.query:
            search_query = f"%{search.query}%"
            query = query.filter(
                (Product.name.ilike(search_query)) |
                (Product.code.ilike(search_query))
            )

        # Récupérer les produits
        products = query.order_by(Product.name).all()

        # Préparer les données d'export
        export_data = [
            {
                "code": p.code or "",
                "name": p.name or "",
                "quantity": p.quantity or 0,
                "purchase_price": float(p.purchase_price) if p.purchase_price else 0.0,
                "selling_price": float(p.selling_price) if p.selling_price else 0.0,
                "category": p.category or "",
                "expiry_date": p.expiry_date.isoformat() if p.expiry_date else ""
            }
            for p in products
        ]

        # Gérer l'export en arrière-plan ou immédiat
        if background_tasks:
            export_service = ExportService()
            background_tasks.add_task(
                export_service.export_stock,
                data=export_data,
                export_format=export_format,
                user_id=current_user.id,
                tenant_id=current_tenant.id
            )
            return {
                "message": "Export démarré en arrière-plan",
                "format": export_format.value,
                "item_count": len(export_data),
                "tenant": current_tenant.name
            }

        # Retour direct des données
        return {
            "data": export_data,
            "format": export_format.value,
            "count": len(export_data),
            "tenant": current_tenant.name
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de l'export du stock: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de l'export: {str(e)}"
        )

@router.get("/products")
@require_permission("view_stock")
async def get_products(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère la liste des produits avec pagination et filtres
    """
    try:
        query = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        )
        
        # Appliquer les filtres
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                (Product.name.ilike(search_term)) |
                (Product.code.ilike(search_term)) |
                (Product.description.ilike(search_term))
            )
        
        if category:
            query = query.filter(Product.category == category)
        
        # Pagination
        total = query.count()
        products = query.offset(skip).limit(limit).all()
        
        return {
            "products": [
                {
                    "id": p.id,
                    "code": p.code,
                    "name": p.name,
                    "quantity": p.quantity,
                    "purchase_price": p.purchase_price,
                    "selling_price": p.selling_price,
                    "category": p.category,
                    "is_active": p.is_active
                }
                for p in products
            ],
            "total": total,
            "skip": skip,
            "limit": limit
        }
        
    except Exception as e:
        logger.error(f"Erreur récupération produits: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")
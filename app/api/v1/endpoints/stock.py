# app/api/v1/stock.py
"""
API de gestion du stock avec intégration complète des ventes
Version unifiée - Remplace products.py
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks, Request, Form
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, and_, or_, distinct, case
from typing import List, Optional, Dict, Any, Union
from uuid import UUID
import uuid
from datetime import datetime, timedelta, date
import logging
from decimal import Decimal
from fastapi import File, UploadFile 
from app.db.session import get_db
from app.models.product import Product, ProductStock
from app.models.stock_movement import StockMovement, InventoryCount, InventoryCountItem
from app.models.tenant import Tenant
from app.models.user import User
from app.models.pharmacy import Pharmacy
from app.models.pharmacy import PharmacyConfig
from app.models.sale import Sale, SaleItem
from app.models.user_pharmacy import UserPharmacy
from app.models.category import Category
from app.models.branch import Branch
from app.schemas.stock import (
    ProductCreate, ProductResponse, ProductInDB, ProductUpdate,
    ProductListResponse, StockAdjustment, StockMovementFilter,
    InventoryCountRequest, ProductSearch, StockStats, ProductMergeRequest,
    ExportFormat, StockMovementResponse, SalesImpactResponse
)
from app.schemas.category import (CategoryCreate, CategoryResponse, CategoryUpdate, CategoryListResponse)
from app.api.deps import (
    get_current_tenant, 
    get_current_user, 
    get_current_active_user,
    get_current_pharmacy_entity,
    get_current_branch_entity,
    require_permission,
    can_user_access_pharmacy,
    verify_branch_access
)

# Import des services
try:
    from app.services.inventory import InventoryService
except ImportError:
    # Service d'inventaire intégré
    class InventoryService:
        def __init__(self, db: Session, tenant_id: UUID):
            self.db = db
            self.tenant_id = tenant_id
        
        def get_low_stock_products(self, pharmacy_id=None, threshold_percentage=0.3):
            query = self.db.query(Product).filter(
                Product.tenant_id == self.tenant_id,
                Product.is_active == True,
                Product.quantity > 0,
                Product.quantity <= Product.alert_threshold
            )
            if pharmacy_id:
                query = query.filter(Product.pharmacy_id == pharmacy_id)
            
            return [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "code": p.code,
                    "quantity": p.quantity,
                    "alert_threshold": p.alert_threshold
                }
                for p in query.all()
            ]
        
        def get_expiring_products(self, pharmacy_id=None, days=30):
            today = date.today()
            expiry_limit = today + timedelta(days=days)
            
            query = self.db.query(Product).filter(
                Product.tenant_id == self.tenant_id,
                Product.is_active == True,
                Product.expiry_date.isnot(None),
                Product.expiry_date <= expiry_limit,
                Product.quantity > 0
            )
            if pharmacy_id:
                query = query.filter(Product.pharmacy_id == pharmacy_id)
            
            return [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "code": p.code,
                    "expiry_date": p.expiry_date.isoformat(),
                    "days_until_expiry": (p.expiry_date - today).days,
                    "quantity": p.quantity,
                    "status": "expired" if p.expiry_date < today else "critical" if (p.expiry_date - today).days <= 7 else "warning"
                }
                for p in query.all()
            ]
        
        def calculate_stock_value(self, pharmacy_id=None, valuation_method="purchase"):
            query = self.db.query(Product).filter(
                Product.tenant_id == self.tenant_id,
                Product.is_active == True
            )
            if pharmacy_id:
                query = query.filter(Product.pharmacy_id == pharmacy_id)
            
            total_purchase_value = 0.0
            total_selling_value = 0.0
            
            for p in query.all():
                total_purchase_value += float(p.purchase_price or 0) * (p.quantity or 0)
                total_selling_value += float(p.selling_price or 0) * (p.quantity or 0)
            
            return {
                "total_purchase_value": total_purchase_value,
                "total_selling_value": total_selling_value,
                "total_profit": total_selling_value - total_purchase_value
            }
        
        def get_stock_alerts(self, pharmacy_id=None):
            branch: Branch = Depends(verify_branch_access),
            db: Session = Depends(get_db),
            alerts = {"critical_alerts": [], "warning_alerts": []}
            
            # Rupture de stock
            out_of_stock = self.db.query(Product).filter(
                Product.tenant_id == self.tenant_id,
                Product.is_active == True,
                Product.quantity == 0
            )
            if pharmacy_id:
                out_of_stock = out_of_stock.filter(Product.pharmacy_id == pharmacy_id)
            
            for p in out_of_stock.all():
                alerts["critical_alerts"].append({
                    "id": str(p.id),
                    "name": p.name,
                    "code": p.code,
                    "type": "out_of_stock",
                    "message": f"Produit en rupture de stock"
                })
            
            # Stock faible
            low_stock = self.db.query(Product).filter(
                Product.tenant_id == self.tenant_id,
                Product.is_active == True,
                Product.quantity > 0,
                Product.quantity <= Product.alert_threshold
            )
            if pharmacy_id:
                low_stock = low_stock.filter(Product.pharmacy_id == pharmacy_id)
            
            for p in low_stock.all():
                alerts["warning_alerts"].append({
                    "id": str(p.id),
                    "name": p.name,
                    "code": p.code,
                    "type": "low_stock",
                    "current_stock": p.quantity,
                    "threshold": p.alert_threshold,
                    "message": f"Stock faible: {p.quantity} unités restantes"
                })
            
            return alerts
        
        def get_reorder_suggestions(self, pharmacy_id=None, safety_days=30):
            suggestions = []
            today = date.today()
            
            query = self.db.query(Product).filter(
                Product.tenant_id == self.tenant_id,
                Product.is_active == True
            )
            if pharmacy_id:
                query = query.filter(Product.pharmacy_id == pharmacy_id)
            
            for p in query.all():
                # Calculer la consommation moyenne (ventes des 30 derniers jours)
                thirty_days_ago = datetime.utcnow() - timedelta(days=30)
                total_sold = self.db.query(func.coalesce(func.sum(SaleItem.quantity), 0)).join(
                    Sale, Sale.id == SaleItem.sale_id
                ).filter(
                    SaleItem.product_id == p.id,
                    Sale.status == "completed",
                    Sale.created_at >= thirty_days_ago
                ).scalar() or 0
                
                daily_avg = total_sold / 30.0
                reorder_quantity = int(daily_avg * safety_days)
                
                if p.quantity <= p.minimum_stock:
                    suggestions.append({
                        "product_id": str(p.id),
                        "product_name": p.name,
                        "product_code": p.code,
                        "current_stock": p.quantity,
                        "minimum_stock": p.minimum_stock,
                        "daily_consumption": daily_avg,
                        "suggested_order": max(reorder_quantity, p.minimum_stock * 2),
                        "priority": "high" if p.quantity <= p.alert_threshold else "medium"
                    })
            
            return sorted(suggestions, key=lambda x: x["priority"] == "high", reverse=True)
        
        def get_stock_turnover_rate(self, product_id=None, pharmacy_id=None, days=365):
            turnover_data = []
            start_date = datetime.utcnow() - timedelta(days=days)
            
            query = self.db.query(
                Product.id,
                Product.name,
                Product.code,
                Product.quantity
            ).filter(
                Product.tenant_id == self.tenant_id,
                Product.is_active == True
            )
            if product_id:
                query = query.filter(Product.id == product_id)
            if pharmacy_id:
                query = query.filter(Product.pharmacy_id == pharmacy_id)
            
            for product in query.all():
                total_sold = self.db.query(func.coalesce(func.sum(SaleItem.quantity), 0)).join(
                    Sale, Sale.id == SaleItem.sale_id
                ).filter(
                    SaleItem.product_id == product.id,
                    Sale.status == "completed",
                    Sale.created_at >= start_date
                ).scalar() or 0
                
                avg_inventory = product.quantity or 1
                turnover_rate = total_sold / avg_inventory if avg_inventory > 0 else 0
                
                turnover_data.append({
                    "product_id": str(product.id),
                    "product_name": product.name,
                    "product_code": product.code,
                    "total_sold": int(total_sold),
                    "avg_inventory": int(avg_inventory),
                    "turnover_rate": round(turnover_rate, 2)
                })
            
            avg_turnover = sum(t["turnover_rate"] for t in turnover_data) / len(turnover_data) if turnover_data else 0
            
            return {
                "average_turnover_rate": round(avg_turnover, 2),
                "period_days": days,
                "products": turnover_data
            }
        
        def get_stock_movements_report(self, pharmacy_id=None, start_date=None, end_date=None, 
                                        movement_type=None, product_id=None, limit=500):
            query = self.db.query(StockMovement).filter(
                StockMovement.tenant_id == self.tenant_id
            )
            
            if pharmacy_id:
                query = query.filter(StockMovement.pharmacy_id == pharmacy_id)
            if start_date:
                query = query.filter(func.date(StockMovement.created_at) >= start_date)
            if end_date:
                query = query.filter(func.date(StockMovement.created_at) <= end_date)
            if movement_type:
                query = query.filter(StockMovement.movement_type == movement_type)
            if product_id:
                query = query.filter(StockMovement.product_id == product_id)
            
            return query.order_by(desc(StockMovement.created_at)).limit(limit).all()
        
        def update_stock(self, product_id, pharmacy_id, quantity_change, reason, **kwargs):
            product = self.db.query(Product).filter(
                Product.id == product_id,
                Product.tenant_id == self.tenant_id
            ).first()
            
            if not product:
                raise ValueError("Produit non trouvé")
            
            if pharmacy_id and product.pharmacy_id != pharmacy_id:
                raise ValueError("Le produit n'appartient pas à cette pharmacie")
            
            old_quantity = product.quantity
            new_quantity = old_quantity + quantity_change
            
            if new_quantity < 0:
                raise ValueError("Quantité insuffisante en stock")
            
            product.quantity = new_quantity
            product.available_quantity = max(0, new_quantity - (product.reserved_quantity or 0))
            product.last_adjustment_date = datetime.utcnow()
            product.refresh_statuses()
            
            # Créer le mouvement de stock
            movement = StockMovement(
                tenant_id=self.tenant_id,
                product_id=product.id,
                pharmacy_id=product.pharmacy_id,
                quantity_before=old_quantity,
                quantity_after=new_quantity,
                quantity_change=quantity_change,
                movement_type=kwargs.get("movement_type", "adjustment"),
                reason=reason,
                reference=kwargs.get("reference"),
                batch_number=kwargs.get("batch_number"),
                created_by=kwargs.get("user_id")
            )
            self.db.add(movement)
            self.db.commit()
            
            return product

try:
    from app.services.reporting import ReportService
except ImportError:
    class ReportService:
        def __init__(self, db, tenant):
            self.db = db
            self.tenant = tenant
        
        def generate_stock_report(self, pharmacy_id=None, format="json"):
            return {"message": "Report service not fully implemented"}

try:
    from app.services.export import ExportService
except ImportError:
    class ExportService:
        def __init__(self, tenant):
            self.tenant = tenant
        def export_stock(self, data, export_format, user_id):
            return {"message": "Export service not implemented"}
        def generate_import_template(self):
            return []

router = APIRouter(prefix="/stock", tags=["Stock"])
logger = logging.getLogger(__name__)


# =======================
# Helpers
# =======================

def get_inventory_service(db: Session, tenant_id: UUID) -> InventoryService:
    """Factory pour obtenir une instance du service d'inventaire"""
    return InventoryService(db, tenant_id)


def get_user_accessible_pharmacies(db: Session, user_id: UUID, tenant_id: Optional[UUID] = None) -> List[UUID]:
    """Récupère la liste des pharmacies accessibles par l'utilisateur"""
    if not user_id:
        return []
    
    query = db.query(UserPharmacy.pharmacy_id).filter(UserPharmacy.user_id == user_id)
    
    if tenant_id:
        query = query.join(Pharmacy).filter(Pharmacy.tenant_id == tenant_id)
    
    return [p.pharmacy_id for p in query.all()]


def _tenant_get_config(tenant: Optional[Tenant], key: str, default: Any = None) -> Any:
    """Récupère une configuration du tenant."""
    if tenant and hasattr(tenant, 'config') and tenant.config:
        return tenant.config.get(key, default)
    return default


def _to_float(value: Any, default: float = 0.0) -> float:
    """Convertit une valeur en float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Convertit une valeur en Decimal."""
    try:
        if value is None:
            return default
        return Decimal(str(value))
    except Exception:
        return default


def _safe_update_product_status(product: Product) -> None:
    """Met à jour le statut du produit."""
    product.refresh_statuses()


def _serialize_product(product: Product, include_details: bool = False) -> Dict[str, Any]:
    """Sérialise un produit en dictionnaire."""
    return product.to_dict(include_details=include_details)


def _base_product_query(db: Session, tenant_id: UUID, pharmacy_id: Optional[UUID] = None, branch_id: Optional[UUID] = None):
    """Construit la requête de base pour les produits."""
    query = db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.is_active.is_(True),
    )
    
    if pharmacy_id:
        query = query.filter(Product.pharmacy_id == pharmacy_id)
    
    if branch_id:
        query = query.filter(Product.branch_id == branch_id)
    
    return query

def _ensure_pharmacy_in_tenant(current_tenant: Optional[Tenant], current_pharmacy: Optional[Pharmacy]) -> Pharmacy:
    """Vérifie que la pharmacie appartient bien au tenant courant."""
    if current_pharmacy is None:
        raise HTTPException(status_code=400, detail="Aucune pharmacie active sélectionnée")
    
    if current_tenant and getattr(current_pharmacy, "tenant_id", None) != current_tenant.id:
        raise HTTPException(status_code=403, detail="La pharmacie sélectionnée n'appartient pas au tenant courant")
    
    return current_pharmacy


def _safe_calculate_prices(product: Product, margin: float, tva_rate: float) -> None:
    """Calcule les prix du produit."""
    product.calculate_prices(margin, tva_rate)


def _get_pharmacy_ids_from_user(
    db: Session, 
    current_user: User, 
    tenant_id: Optional[UUID], 
    pharmacy_id: Optional[UUID] = None
) -> List[UUID]:
    """Récupère les IDs des pharmacies accessibles par l'utilisateur."""
    if current_user.role in ["super_admin", "superadmin", "admin"]:
        pharmacies_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
        if tenant_id:
            pharmacies_query = pharmacies_query.filter(Pharmacy.tenant_id == tenant_id)
        if pharmacy_id:
            pharmacies_query = pharmacies_query.filter(Pharmacy.id == pharmacy_id)
        return [p.id for p in pharmacies_query.all()]
    else:
        pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
        if pharmacy_id and pharmacy_id not in pharmacy_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé à cette pharmacie"
            )
        return [pharmacy_id] if pharmacy_id else pharmacy_ids


def _check_permission(current_user: User, required_roles: List[str]) -> None:
    """Vérifie si l'utilisateur a les permissions nécessaires."""
    user_role = current_user.role.lower() if current_user.role else ""
    if user_role not in [r.lower() for r in required_roles]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès non autorisé"
        )
    
# =======================
# ROUTES POUR LES CATÉGORIES
# =======================

@router.get("/categories", response_model=CategoryListResponse, summary="Liste des catégories")
async def list_categories(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    parent_id: Optional[UUID] = Query(None, description="Filtrer par catégorie parente"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Liste toutes les catégories de produits.
    """
    _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
    
    tenant_id = current_tenant.id if current_tenant else None
    
    query = db.query(Category).filter(Category.is_active == True)
    
    if tenant_id:
        query = query.filter(Category.tenant_id == tenant_id)
    
    if parent_id:
        query = query.filter(Category.parent_id == parent_id)
    else:
        query = query.filter(Category.parent_id.is_(None))
    
    total = query.count()
    categories = query.order_by(Category.name).offset(skip).limit(limit).all()
    
    return CategoryListResponse(
        total=total,
        skip=skip,
        limit=limit,
        categories=[CategoryResponse.from_orm(c) for c in categories]
    )


@router.post("/categories", response_model=CategoryResponse, summary="Créer une catégorie")
async def create_category(
    category_data: CategoryCreate,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Crée une nouvelle catégorie de produits.
    """
    _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
    
    tenant_id = current_tenant.id if current_tenant else None
    
    # Vérifier si une catégorie avec le même nom existe
    existing = db.query(Category).filter(
        Category.tenant_id == tenant_id,
        Category.name == category_data.name,
        Category.is_active == True
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Une catégorie avec le nom '{category_data.name}' existe déjà"
        )
    
    category = Category(
        **category_data.model_dump(exclude_unset=True),
        tenant_id=tenant_id
    )
    
    db.add(category)
    db.commit()
    db.refresh(category)
    
    logger.info(f"Catégorie créée: {category.name} par {current_user.email}")
    
    return CategoryResponse.from_orm(category)


@router.put("/categories/{category_id}", response_model=CategoryResponse, summary="Modifier une catégorie")
async def update_category(
    category_id: UUID,
    category_data: CategoryUpdate,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Modifie une catégorie existante.
    """
    _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
    
    tenant_id = current_tenant.id if current_tenant else None
    
    category = db.query(Category).filter(
        Category.id == category_id,
        Category.tenant_id == tenant_id
    ).first()
    
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catégorie non trouvée"
        )
    
    update_data = category_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(category, field, value)
    
    db.commit()
    db.refresh(category)
    
    logger.info(f"Catégorie modifiée: {category.name} par {current_user.email}")
    
    return CategoryResponse.from_orm(category)


@router.delete("/categories/{category_id}", summary="Supprimer une catégorie")
async def delete_category(
    category_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Supprime une catégorie (soft delete).
    """
    _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
    
    tenant_id = current_tenant.id if current_tenant else None
    
    category = db.query(Category).filter(
        Category.id == category_id,
        Category.tenant_id == tenant_id
    ).first()
    
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catégorie non trouvée"
        )
    
    # Vérifier si des produits utilisent cette catégorie
    products_count = db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.category_id == category_id,
        Product.is_active == True
    ).count()
    
    if products_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Impossible de supprimer cette catégorie car elle est utilisée par {products_count} produits"
        )
    
    category.is_active = False
    db.commit()
    
    logger.info(f"Catégorie supprimée: {category.name} par {current_user.email}")
    
    return {"message": "Catégorie supprimée avec succès", "category_id": str(category_id)}


@router.get("/categories/simple", summary="Liste simple des catégories (pour selects)")
async def list_categories_simple(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Liste simple des catégories pour les formulaires (selects).
    """
    tenant_id = current_tenant.id if current_tenant else None
    
    categories = db.query(Category).filter(
        Category.tenant_id == tenant_id,
        Category.is_active == True
    ).order_by(Category.name).all()
    
    return [
        {"id": str(c.id), "name": c.name, "parent_id": str(c.parent_id) if c.parent_id else None}
        for c in categories
    ]

# =======================
# ROUTES DE TEST / UTILITAIRES
# =======================

@router.get("/test", summary="Test de l'API Stock")
async def test_stock(
    current_user: User = Depends(get_current_active_user)
):
    """Endpoint de test pour vérifier que l'API fonctionne."""
    return {
        "message": "Stock API fonctionne !",
        "version": "3.1",
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role
        }
    }

@router.get("/template", summary="Télécharger le template d'import")
async def get_import_template(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """Télécharge le template pour l'import de produits."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        export_service = ExportService(current_tenant)
        template = export_service.generate_import_template()
        
        return {"template": template}
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur génération template")
        raise HTTPException(status_code=500, detail=f"Erreur génération template: {exc}")

# =======================
# ROUTES DE BASE POUR LES PRODUITS
# =======================

@router.get("/", response_model=ProductListResponse, summary="Liste des produits")
async def list_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100000),
    get_all: bool = Query(False, description="Récupérer TOUS les produits (ignore skip/limit)"),
    search: Optional[str] = Query(None, description="Recherche par nom, code, code-barres ou nom commercial"),
    category_id: Optional[UUID] = Query(None, description="Filtrer par catégorie ID"),
    category: Optional[str] = Query(None, description="Filtrer par nom de catégorie (legacy)"),
    stock_status: Optional[str] = Query(None, description="Filtrer par statut de stock: out_of_stock, low_stock, normal"),
    expiry_status: Optional[str] = Query(None, description="Filtrer par statut d'expiration: expired, critical, warning"),
    product_type: Optional[str] = Query(None, description="Filtrer par type: medicament, parapharmacie, materiel, autre"),
    min_price: Optional[float] = Query(None, description="Prix minimum"),
    max_price: Optional[float] = Query(None, description="Prix maximum"),
    include_sales_stats: bool = Query(False, description="Inclure les statistiques de ventes"),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par succursale"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Liste tous les produits avec pagination et filtres optionnels.
    
    Paramètres:
    - get_all: Si True, récupère TOUS les produits sans limite de pagination
    - limit: Nombre maximum de produits par page (max 100000)
    - skip: Nombre de produits à sauter (pour pagination)
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien", "vendeur"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Gestion des permissions par branche
        is_admin = current_user.role in ["super_admin", "superadmin", "admin", "gerant"]
        
        if not is_admin:
            # Les non-admins ne voient que leur propre branche
            if branch_id and branch_id != current_user.active_branch_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Vous ne pouvez voir que le stock de votre branche"
                )
            effective_branch_id = current_user.active_branch_id
        else:
            # Les admins peuvent voir toutes les branches ou filtrer
            effective_branch_id = branch_id or (current_branch.id if current_branch else None)
        
        query = _base_product_query(db, tenant_id, pharmacy.id, effective_branch_id)
        
        # Filtre par catégorie
        if category_id:
            query = query.filter(Product.category_id == category_id)
        elif category:
            query = query.filter(Product.category == category)
        
        if search:
            query = query.filter(
                or_(
                    Product.name.ilike(f"%{search}%"),
                    Product.code.ilike(f"%{search}%"),
                    Product.barcode.ilike(f"%{search}%"),
                    Product.commercial_name.ilike(f"%{search}%"),
                )
            )
        
        if stock_status:
            query = query.filter(Product.stock_status == stock_status)
        
        if expiry_status:
            query = query.filter(Product.expiry_status == expiry_status)
        
        if product_type:
            query = query.filter(Product.product_type == product_type)
        
        if min_price is not None:
            query = query.filter(Product.selling_price >= min_price)
        
        if max_price is not None:
            query = query.filter(Product.selling_price <= max_price)
        
        total = query.count()
        
        # ============================================================
        # GESTION DE get_all : RÉCUPÉRATION DE TOUS LES PRODUITS
        # ============================================================
        if get_all:
            # Récupérer TOUS les produits sans limite de pagination
            logger.info(f"Récupération de TOUS les produits pour le tenant {tenant_id} (total: {total})")
            products = query.order_by(Product.name.asc()).all()
            actual_limit = total
            actual_skip = 0
        else:
            # Pagination normale
            products = query.order_by(Product.name.asc()).offset(skip).limit(limit).all()
            actual_limit = limit
            actual_skip = skip
        
        product_list = []
        for product in products:
            product_dict = _serialize_product(product, include_details=False)
            
            if include_sales_stats and tenant_id:
                thirty_days_ago = datetime.utcnow() - timedelta(days=30)
                sales_stats = db.query(
                    func.coalesce(func.sum(SaleItem.quantity), 0).label("total_sold"),
                    func.coalesce(func.sum(SaleItem.total), 0).label("total_revenue"),
                    func.count(distinct(Sale.id)).label("sale_count")
                ).join(Sale).filter(
                    SaleItem.product_id == product.id,
                    SaleItem.tenant_id == tenant_id,
                    Sale.status == "completed",
                    Sale.created_at >= thirty_days_ago
                ).first()
                
                product_dict["sales_stats"] = {
                    "last_30_days_sold": int(sales_stats.total_sold or 0),
                    "last_30_days_revenue": float(sales_stats.total_revenue or 0),
                    "last_30_days_sales_count": int(sales_stats.sale_count or 0)
                }
            
            product_list.append(product_dict)
        
        # Statistiques globales
        stats = {
            "total_products": total,
            "total_value_purchase": sum(float(p.get("purchase_value", 0)) for p in product_list),
            "total_value_selling": sum(float(p.get("selling_value", 0)) for p in product_list),
            "total_profit": sum(float(p.get("total_margin", 0)) for p in product_list),
            "out_of_stock": len([p for p in product_list if p.get("stock_status") == "out_of_stock"]),
            "low_stock": len([p for p in product_list if p.get("stock_status") == "low_stock"]),
            "expired_soon": len([p for p in product_list if p.get("expiry_status") in ["critical", "warning"]])
        }
        
        return ProductListResponse(
            total=total,
            page=(actual_skip // actual_limit) + 1 if actual_limit > 0 and not get_all else 1,
            limit=actual_limit,
            products=product_list,
            summary=stats,
        )
    
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur lors de la recuperation des produits")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")
    
@router.post("/", response_model=ProductResponse, summary="Créer un produit")
async def create_product(
    product_data: ProductCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Crée un nouveau produit."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Récupérer la configuration de la pharmacie
        config = db.query(PharmacyConfig).filter(
            PharmacyConfig.pharmacy_id == pharmacy.id,
            PharmacyConfig.is_active == True
        ).first()
        
        # Configuration des prix - utiliser getattr pour éviter les erreurs
        calcul_auto_prix = getattr(product_data, 'calcul_auto_prix', None)
        if calcul_auto_prix is None:
            calcul_auto_prix = config.calcul_auto_prix if config else True
        
        marge_par_defaut = getattr(product_data, 'marge_par_defaut', None)
        if marge_par_defaut is None:
            marge_par_defaut = config.marge_par_defaut if config else 30.0
        
        sales_type = getattr(product_data, 'sales_type', None)
        if sales_type is None:
            sales_type = config.sales_type if config else "both"
        
        # Vérifier si un produit avec le même code existe
        if product_data.code:
            existing_by_code = db.query(Product).filter(
                Product.tenant_id == tenant_id,
                Product.code == product_data.code,
                Product.is_active == True
            ).first()
            
            if existing_by_code:
                raise HTTPException(
                    status_code=400,
                    detail=f"Un produit avec le code {product_data.code} existe déjà"
                )
        
        # =========================================================
        # CONSTRUCTION DU PAYLOAD - NE PAS INCLURE calcul_auto_prix
        # =========================================================
        payload = {
            "tenant_id": tenant_id,
            "pharmacy_id": pharmacy.id,
            "branch_id": current_branch.id if current_branch else None,
            "code": product_data.code,
            "barcode": product_data.barcode,
            "name": product_data.name,
            "commercial_name": product_data.commercial_name,
            "description": product_data.description,
            "active_ingredient": product_data.active_ingredient,
            "dosage": product_data.dosage,
            "galenic_form": product_data.galenic_form,
            "laboratory": product_data.laboratory,
            "dci": product_data.dci,
            "category": product_data.category,
            "subcategory": product_data.subcategory,
            "therapeutic_class": product_data.therapeutic_class,
            "product_type": product_data.product_type,
            "quantity": product_data.quantity,
            "unit": product_data.unit,
            "alert_threshold": product_data.alert_threshold,
            "minimum_stock": product_data.minimum_stock,
            "maximum_stock": product_data.maximum_stock,
            "purchase_price": product_data.purchase_price,
            "wholesale_price": product_data.wholesale_price,
            "has_tva": product_data.has_tva,
            "tva_rate": product_data.tva_rate,
            "expiry_date": product_data.expiry_date,
            "batch_number": product_data.batch_number,
            "authorization_number": product_data.authorization_number,
            "packaging": product_data.packaging,
            "prescription_required": product_data.prescription_required,
            "regulatory_class": product_data.regulatory_class,
            "main_supplier": product_data.main_supplier,
            "location": product_data.location,
            "image_url": product_data.image_url,
            "leaflet_url": product_data.leaflet_url,
            "notes": product_data.notes,
            "meta_data": product_data.meta_data,
            "available_quantity": product_data.quantity,
            "reserved_quantity": 0,
            "is_active": True,
            "is_available": True
        }
        
        # Nettoyer les valeurs None (optionnel mais recommandé)
        payload = {k: v for k, v in payload.items() if v is not None}
        
        # Créer l'objet Product
        product = Product(**payload)
        
        # =========================================================
        # GESTION DES PRIX - AUCUN RECALCUL AUTOMATIQUE
        # =========================================================
        # Récupérer les prix spécifiques selon le type de vente
        selling_price_retail = getattr(product_data, 'selling_price_retail', None)
        selling_price_wholesale = getattr(product_data, 'selling_price_wholesale', None)
        
        # Utiliser TOUJOURS les prix fournis, jamais de calcul automatique
        if sales_type == "wholesale":
            product.selling_price = selling_price_wholesale or product_data.selling_price or 0
            product.selling_price_wholesale = product.selling_price
        elif sales_type == "retail":
            product.selling_price = selling_price_retail or product_data.selling_price or 0
            product.selling_price_retail = product.selling_price
        else:  # both
            product.selling_price = selling_price_retail or product_data.selling_price or 0
            product.selling_price_retail = product.selling_price
            product.selling_price_wholesale = selling_price_wholesale or product_data.selling_price or 0
        
        # Pas d'arrondissement automatique non plus
        # product.refresh_statuses() est suffisant pour mettre à jour les statuts
               
        db.add(product)
        db.flush()
        
        # Créer un mouvement de stock initial
        if product.quantity > 0:
            movement = StockMovement(
                tenant_id=tenant_id,
                product_id=product.id,
                pharmacy_id=pharmacy.id,
                branch_id=current_branch.id if current_branch else None,
                quantity_before=0,
                quantity_after=product.quantity,
                quantity_change=product.quantity,
                movement_type="initial",
                reason="Création du produit",
                created_by=current_user.id
            )
            db.add(movement)
        
        db.commit()
        db.refresh(product)
        
        return ProductResponse(
            message="Produit créé avec succès",
            product=_serialize_product(product),
        )
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur création produit")
        raise HTTPException(status_code=400, detail=f"Erreur création produit: {exc}")
        
@router.get("/export", summary="Exporter le stock")
async def export_stock(
    format: str = Query("excel", description="Format d'export: excel, csv, json"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    category_id: Optional[UUID] = Query(None, description="Filtrer par catégorie"),
    category: Optional[str] = Query(None, description="Filtrer par nom de catégorie"),
    search: Optional[str] = Query(None, description="Recherche textuelle"),
    stock_status: Optional[str] = Query(None, description="Filtrer par statut de stock"),
    expiry_status: Optional[str] = Query(None, description="Filtrer par statut d'expiration"),
    include_sales_stats: bool = Query(False, description="Inclure les statistiques de ventes"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Exporte le stock dans le format spécifié (Excel, CSV, JSON).
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien", "vendeur"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Construire la requête
        query = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.pharmacy_id == pharmacy.id,
            Product.is_active == True
        )
        
        if pharmacy_id:
            query = query.filter(Product.pharmacy_id == pharmacy_id)
        
        if category_id:
            query = query.filter(Product.category_id == category_id)
        elif category:
            query = query.filter(Product.category == category)
        
        if search:
            query = query.filter(
                or_(
                    Product.name.ilike(f"%{search}%"),
                    Product.code.ilike(f"%{search}%"),
                    Product.barcode.ilike(f"%{search}%")
                )
            )
        
        if stock_status:
            query = query.filter(Product.stock_status == stock_status)
        
        if expiry_status:
            query = query.filter(Product.expiry_status == expiry_status)
        
        products = query.order_by(Product.name).all()
        
        # Préparer les données
        export_data = []  # ← Variable renommée de "data" à "export_data"
        for product in products:
            product_data = {
                "ID": str(product.id),
                "Nom": product.name,
                "Code": product.code or "",
                "Code-barres": product.barcode or "",
                "Quantité": product.quantity or 0,
                "Prix d'achat": float(product.purchase_price or 0),
                "Prix de vente": float(product.selling_price or 0),
                "Valeur d'achat": float(product.purchase_value or 0),
                "Valeur de vente": float(product.selling_value or 0),
                "Marge totale": float(product.total_margin or 0),
                "Date d'expiration": product.expiry_date.isoformat() if product.expiry_date else "",
                "Catégorie": product.category or "",
                "Emplacement": product.location or "",
                "Fournisseur": product.supplier or "",
                "Numéro de lot": product.batch_number or "",
                "Statut stock": product.stock_status or "normal",
                "Statut expiration": product.expiry_status or "normal",
                "Seuil d'alerte": product.alert_threshold or 0,
                "Stock minimum": product.minimum_stock or 0,
                "Unité": product.unit or "unité",
                "TVA": f"{product.tva_rate}%" if product.has_tva else "0%",
                "Type": product.product_type or "medicament",
                "Nom commercial": product.commercial_name or "",
                "Forme galénique": product.galenic_form or "",
                "Dosage": product.dosage or "",
                "Principe actif": product.active_ingredient or "",
                "Créé le": product.created_at.isoformat() if product.created_at else "",
                "Dernière modification": product.updated_at.isoformat() if product.updated_at else ""
            }
            
            if include_sales_stats:
                thirty_days_ago = datetime.utcnow() - timedelta(days=30)
                sales_stats = db.query(
                    func.coalesce(func.sum(SaleItem.quantity), 0).label("total_sold"),
                    func.coalesce(func.sum(SaleItem.total), 0).label("total_revenue"),
                    func.count(distinct(Sale.id)).label("sale_count")
                ).join(Sale).filter(
                    SaleItem.product_id == product.id,
                    SaleItem.tenant_id == tenant_id,
                    Sale.status == "completed",
                    Sale.created_at >= thirty_days_ago
                ).first()
                
                product_data["Ventes 30j (quantité)"] = int(sales_stats.total_sold or 0)
                product_data["Ventes 30j (CA)"] = float(sales_stats.total_revenue or 0)
                product_data["Ventes 30j (nombre)"] = int(sales_stats.sale_count or 0)
                
                if product.quantity and product.quantity > 0:
                    turnover_rate = (sales_stats.total_sold or 0) / product.quantity
                    product_data["Taux de rotation"] = round(turnover_rate, 2)
                else:
                    product_data["Taux de rotation"] = 0
            
            export_data.append(product_data)
        
        # Exporter selon le format
        import pandas as pd
        import io
        import json
        
        df = pd.DataFrame(export_data)  # ← Utiliser export_data
        
        if format.lower() == "csv":
            output = io.StringIO()
            df.to_csv(output, index=False, encoding='utf-8-sig')
            content = output.getvalue().encode('utf-8')
            media_type = "text/csv"
            filename = f"stock_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        elif format.lower() == "json":
            content = json.dumps(export_data, indent=2, default=str).encode('utf-8')  # ← Utiliser export_data
            media_type = "application/json"
            filename = f"stock_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        else:  # excel par défaut
            output = io.BytesIO()
            df.to_excel(output, index=False, engine="openpyxl")
            content = output.getvalue()
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = f"stock_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        from fastapi.responses import Response
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur export stock")
        raise HTTPException(status_code=500, detail=f"Erreur export stock: {exc}")
    

@router.get("/{product_id}", response_model=ProductInDB, summary="Détails d'un produit")
async def get_product(
    product_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Retourne les détails d'un produit spécifique."""
    _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien", "vendeur"])
    
    pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
    tenant_id = current_tenant.id if current_tenant else None
    
    product_query = db.query(Product).filter(
        Product.id == product_id,
        Product.is_active.is_(True),
    )
    if tenant_id:
        product_query = product_query.filter(Product.tenant_id == tenant_id)
    if pharmacy:
        product_query = product_query.filter(Product.pharmacy_id == pharmacy.id)
    
    product = product_query.first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    
    return _serialize_product(product, include_details=True)


@router.put("/{product_id}", response_model=ProductResponse, summary="Modifier un produit")
async def update_product(
    product_id: UUID,
    product_data: ProductUpdate,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Modifie les informations d'un produit existant."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        product_query = db.query(Product).filter(
            Product.id == product_id,
            Product.is_active.is_(True),
        )
        if tenant_id:
            product_query = product_query.filter(Product.tenant_id == tenant_id)
        if pharmacy:
            product_query = product_query.filter(Product.pharmacy_id == pharmacy.id)
        
        product = product_query.first()
        
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        old_quantity = product.quantity
        update_data = product_data.model_dump(exclude_unset=True)
        
        for field, value in update_data.items():
            setattr(product, field, value)
        
        if "quantity" in update_data:
            product.available_quantity = max(
                0,
                int(getattr(product, "quantity", 0) or 0) - int(getattr(product, "reserved_quantity", 0) or 0),
            )
        
        #if "purchase_price" in update_data and bool(_tenant_get_config(current_tenant, "calcul_auto_prix", True)):
        #    margin = _to_float(_tenant_get_config(current_tenant, "marge_par_defaut", 30.0), 30.0)
        #    tva_rate = (
        #        _to_float(getattr(product, "tva_rate", 0.0), 0.0)
        #        if bool(getattr(product, "has_tva", False))
        #        else 0.0
        #    )
        #   _safe_calculate_prices(product, margin, tva_rate)
        
        product.refresh_statuses()
        
        # Créer un mouvement de stock si la quantité a changé
        if "quantity" in update_data and update_data["quantity"] != old_quantity:
            movement = StockMovement(
                tenant_id=tenant_id,
                product_id=product.id,
                pharmacy_id=pharmacy.id,
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
        
        logger.info("Produit modifié: %s par %s", getattr(product, "name", "N/A"), getattr(current_user, "email", None))
        
        return ProductResponse(
            message="Produit mis à jour avec succès",
            product=_serialize_product(product),
        )
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur modification produit")
        raise HTTPException(status_code=400, detail=f"Erreur modification produit: {exc}")

# =======================
# ROUTES POUR L'AJUSTEMENT DE STOCK
# =======================

@router.post("/adjust", summary="Ajuster le stock")
async def adjust_stock(
    adjustment: StockAdjustment,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Ajuste la quantité d'un produit en stock."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        product_query = db.query(Product).filter(
            Product.id == adjustment.product_id,
            Product.is_active.is_(True)
        )
        if tenant_id:
            product_query = product_query.filter(Product.tenant_id == tenant_id)
        if pharmacy:
            product_query = product_query.filter(Product.pharmacy_id == pharmacy.id)
        
        product = product_query.first()
        
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        lock_stock_modification = bool(_tenant_get_config(current_tenant, "lock_stock_modification", False))
        user_role = (getattr(current_user, "role", "") or "").lower()
        
        if lock_stock_modification and user_role not in {"admin", "administrateur", "super_admin", "superadmin"}:
            raise HTTPException(
                status_code=403,
                detail="La modification des stocks est verrouillée. Contactez un administrateur.",
            )
        
        old_quantity = int(getattr(product, "quantity", 0) or 0)
        reserved_quantity = int(getattr(product, "reserved_quantity", 0) or 0)
        
        product.quantity = adjustment.new_quantity
        product.available_quantity = max(0, adjustment.new_quantity - reserved_quantity)
        product.last_adjustment_date = datetime.now()
        
        product.refresh_statuses()
        
        # Créer un mouvement de stock
        movement = StockMovement(
            tenant_id=tenant_id,
            product_id=product.id,
            pharmacy_id=pharmacy.id,
            quantity_before=old_quantity,
            quantity_after=adjustment.new_quantity,
            quantity_change=adjustment.new_quantity - old_quantity,
            movement_type="adjustment",
            reason=adjustment.reason,
            notes=adjustment.notes,
            created_by=current_user.id
        )
        db.add(movement)
        
        db.commit()
        db.refresh(product)
        
        logger.info(
            "Stock ajusté: %s %s→%s par %s",
            getattr(product, "name", "N/A"),
            old_quantity,
            adjustment.new_quantity,
            getattr(current_user, "email", None),
        )
        
        return {
            "message": "Stock ajusté avec succès",
            "product": _serialize_product(product),
            "adjustment": {
                "old_quantity": old_quantity,
                "new_quantity": adjustment.new_quantity,
                "difference": adjustment.new_quantity - old_quantity,
                "reason": adjustment.reason,
                "notes": adjustment.notes,
            },
        }
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur ajustement stock")
        raise HTTPException(status_code=400, detail=f"Erreur ajustement stock: {exc}")


@router.post("/transfer", summary="Transférer du stock entre pharmacies ou succursales")
async def transfer_stock(
    product_id: UUID,
    quantity: int,
    from_pharmacy_id: UUID,
    to_pharmacy_id: UUID,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """Transfère du stock d'une pharmacie à une autre."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
        
        tenant_id = current_tenant.id if current_tenant else None
        
        # Vérifier les pharmacies
        from_pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == from_pharmacy_id,
            Pharmacy.tenant_id == tenant_id,
            Pharmacy.is_active == True
        ).first()
        
        to_pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == to_pharmacy_id,
            Pharmacy.tenant_id == tenant_id,
            Pharmacy.is_active == True
        ).first()
        
        if not from_pharmacy or not to_pharmacy:
            raise HTTPException(status_code=404, detail="Pharmacie source ou destination non trouvée")
        
        # Récupérer le produit source
        source_product = db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == tenant_id,
            Product.pharmacy_id == from_pharmacy_id,
            Product.is_active == True
        ).first()
        
        if not source_product:
            raise HTTPException(status_code=404, detail="Produit non trouvé dans la pharmacie source")
        
        if source_product.quantity < quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Quantité insuffisante en stock source. Disponible: {source_product.quantity}"
            )
        
        # Récupérer ou créer le produit destination
        target_product = db.query(Product).filter(
            Product.name == source_product.name,
            Product.tenant_id == tenant_id,
            Product.pharmacy_id == to_pharmacy_id,
            Product.is_active == True
        ).first()
        
        # Décrémenter la quantité source
        source_product.quantity -= quantity
        source_product.available_quantity = max(0, source_product.quantity - source_product.reserved_quantity)
        source_product.refresh_statuses()
        
        # Mouvement source (sortie)
        movement_out = StockMovement(
            tenant_id=tenant_id,
            product_id=source_product.id,
            pharmacy_id=from_pharmacy_id,
            quantity_before=source_product.quantity + quantity,
            quantity_after=source_product.quantity,
            quantity_change=-quantity,
            movement_type="transfer_out",
            reason=f"Transfert vers {to_pharmacy.name}" + (f" - {reason}" if reason else ""),
            reference=f"TRF-{from_pharmacy.code}-{to_pharmacy.code}",
            created_by=current_user.id
        )
        db.add(movement_out)
        
        if target_product:
            # Incrémenter la quantité destination
            target_product.quantity += quantity
            target_product.available_quantity = max(0, target_product.quantity - target_product.reserved_quantity)
            target_product.refresh_statuses()
            
            movement_in = StockMovement(
                tenant_id=tenant_id,
                product_id=target_product.id,
                pharmacy_id=to_pharmacy_id,
                quantity_before=target_product.quantity - quantity,
                quantity_after=target_product.quantity,
                quantity_change=quantity,
                movement_type="transfer_in",
                reason=f"Transfert depuis {from_pharmacy.name}" + (f" - {reason}" if reason else ""),
                reference=f"TRF-{from_pharmacy.code}-{to_pharmacy.code}",
                created_by=current_user.id
            )
            db.add(movement_in)
        else:
            # Créer un nouveau produit dans la destination
            new_product = Product(
                tenant_id=tenant_id,
                pharmacy_id=to_pharmacy_id,
                name=source_product.name,
                code=source_product.code,
                barcode=source_product.barcode,
                purchase_price=source_product.purchase_price,
                selling_price=source_product.selling_price,
                unit=source_product.unit,
                category=source_product.category,
                quantity=quantity,
                available_quantity=quantity,
                reserved_quantity=0,
                expiry_date=source_product.expiry_date,
                batch_number=source_product.batch_number,
                is_active=True
            )
            new_product.refresh_statuses()
            db.add(new_product)
            
            movement_in = StockMovement(
                tenant_id=tenant_id,
                product_id=new_product.id,
                pharmacy_id=to_pharmacy_id,
                quantity_before=0,
                quantity_after=quantity,
                quantity_change=quantity,
                movement_type="transfer_in",
                reason=f"Transfert depuis {from_pharmacy.name}" + (f" - {reason}" if reason else ""),
                reference=f"TRF-{from_pharmacy.code}-{to_pharmacy.code}",
                created_by=current_user.id
            )
            db.add(movement_in)
        
        db.commit()
        
        logger.info(
            "Transfert de stock: %d x %s de %s vers %s par %s",
            quantity,
            source_product.name,
            from_pharmacy.name,
            to_pharmacy.name,
            current_user.email
        )
        
        return {
            "message": "Transfert effectué avec succès",
            "product": source_product.name,
            "quantity": quantity,
            "from_pharmacy": from_pharmacy.name,
            "to_pharmacy": to_pharmacy.name,
            "source_remaining_stock": source_product.quantity
        }
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur transfert stock")
        raise HTTPException(status_code=400, detail=f"Erreur transfert stock: {exc}")


# =======================
# ROUTES POUR LES STATISTIQUES ET ALERTES
# =======================

@router.get("/stats/overview", response_model=StockStats, summary="Statistiques globales")
async def get_stock_stats(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Retourne des statistiques globales sur le stock."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien", "vendeur"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        products = _base_product_query(db, tenant_id, pharmacy.id).all()
        
        stats = {
            "total_products": len(products),
            "total_quantity": sum(float(getattr(p, "quantity", 0)) for p in products),
            "total_purchase_value": sum(float(p.purchase_value) for p in products),
            "total_selling_value": sum(float(p.selling_value) for p in products),
            "out_of_stock_count": len([p for p in products if p.stock_status == "out_of_stock"]),
            "low_stock_count": len([p for p in products if p.stock_status == "low_stock"]),
            "expired_count": len([p for p in products if p.expiry_status == "expired"]),
            "expiring_soon_count": len([p for p in products if p.expiry_status in ["critical", "warning"]]),
        }
        
        return StockStats(**stats)
    
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur statistiques stock")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/alerts/stock", summary="Alertes de stock")
async def get_stock_alerts(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Retourne les alertes de stock (rupture, stock faible)."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        
        inventory_service = get_inventory_service(db, current_tenant.id if current_tenant else None)
        alerts = inventory_service.get_stock_alerts(pharmacy.id)
        
        return {
            "out_of_stock": alerts.get("critical_alerts", []),
            "low_stock": alerts.get("warning_alerts", []),
            "over_stock": [],
            "counts": {
                "out_of_stock": len(alerts.get("critical_alerts", [])),
                "low_stock": len(alerts.get("warning_alerts", [])),
                "over_stock": 0,
            },
            "pharmacy_id": str(pharmacy.id) if pharmacy else None,
        }
    
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur alertes stock")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/alerts/expiry", summary="Alertes de péremption")
async def get_expiry_alerts(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Retourne les alertes de péremption (produits expirés ou proches de l'expiration)."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        
        inventory_service = get_inventory_service(db, current_tenant.id if current_tenant else None)
        expiring = inventory_service.get_expiring_products(pharmacy.id, days)
        
        # Séparer en expirés et expirant bientôt
        expired = [p for p in expiring if p.get("status") == "expired"]
        expiring_soon = [p for p in expiring if p.get("status") in ["critical", "warning"]]
        
        return {
            "expired": expired,
            "expiring_soon": expiring_soon,
            "counts": {
                "expired": len(expired),
                "expiring_soon": len(expiring_soon),
            },
            "days_threshold": days,
            "pharmacy_id": str(pharmacy.id) if pharmacy else None,
        }
    
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur alertes péremption")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/reorder-suggestions", summary="Suggestions de réapprovisionnement")
async def get_reorder_suggestions(
    safety_days: int = Query(30, ge=7, le=90),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Retourne des suggestions de réapprovisionnement basées sur l'historique des ventes."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        
        inventory_service = get_inventory_service(db, current_tenant.id if current_tenant else None)
        suggestions = inventory_service.get_reorder_suggestions(pharmacy.id, safety_days)
        
        return {
            "suggestions": suggestions,
            "count": len(suggestions),
            "safety_days": safety_days,
            "pharmacy_id": str(pharmacy.id) if pharmacy else None,
        }
    
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur suggestions réapprovisionnement")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


# =======================
# COMMUNICATION AVEC LE MODULE VENTES
# =======================

@router.get("/sales-impact", response_model=List[SalesImpactResponse])
async def get_stock_sales_impact(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    product_id: Optional[UUID] = Query(None, description="Filtrer par produit"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    include_stock_info: bool = Query(True, description="Inclure les informations de stock")
):
    """
    Récupère l'impact des ventes sur le stock
    Point de communication avec le module sales
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        pharmacy_ids = _get_pharmacy_ids_from_user(db, current_user, tenant_id, pharmacy_id)
        
        if not pharmacy_ids:
            return []
        
        # Requête pour l'impact des ventes
        query = db.query(
            Product.id.label("product_id"),
            Product.code,
            Product.name,
            Product.unit,
            func.coalesce(func.sum(SaleItem.quantity), 0).label("total_sold"),
            func.coalesce(func.sum(SaleItem.total), 0).label("total_revenue"),
            func.count(distinct(Sale.id)).label("sale_count"),
            func.avg(SaleItem.unit_price).label("average_price")
        ).join(
            SaleItem, SaleItem.product_id == Product.id
        ).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids)
        )
        
        if tenant_id:
            query = query.filter(Product.tenant_id == tenant_id)
            query = query.filter(Sale.tenant_id == tenant_id)
        
        if product_id:
            query = query.filter(Product.id == product_id)
        
        if start_date:
            query = query.filter(func.date(Sale.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(Sale.created_at) <= end_date)
        
        results = query.group_by(
            Product.id, Product.code, Product.name, Product.unit
        ).order_by(
            desc("total_sold")
        ).all()
        
        response = []
        for row in results:
            impact_data = {
                "product_id": row.product_id,
                "product_code": row.code,
                "product_name": row.name,
                "unit": row.unit,
                "total_sold": int(row.total_sold),
                "total_revenue": float(row.total_revenue),
                "sale_count": int(row.sale_count),
                "average_price": float(row.average_price)
            }
            
            if include_stock_info:
                # Récupérer le stock pour ce produit
                stock_query = db.query(ProductStock).filter(
                    ProductStock.product_id == row.product_id,
                    ProductStock.pharmacy_id.in_(pharmacy_ids)
                )
                if tenant_id:
                    stock_query = stock_query.filter(ProductStock.tenant_id == tenant_id)
                
                stock = stock_query.first()
                
                product_query = db.query(Product).filter(Product.id == row.product_id)
                if tenant_id:
                    product_query = product_query.filter(Product.tenant_id == tenant_id)
                
                product = product_query.first()
                
                current_stock = stock.quantity_available if stock else (product.quantity if product else 0)
                alert_threshold = product.alert_threshold if product else 0
                
                impact_data.update({
                    "current_stock": float(current_stock),
                    "alert_threshold": float(alert_threshold),
                    "stock_status": "out_of_stock" if current_stock <= 0 
                                    else "low_stock" if current_stock <= alert_threshold
                                    else "normal",
                    "stock_value": float(current_stock * (product.purchase_price if product else 0))
                })
            
            response.append(SalesImpactResponse(**impact_data))
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération impact ventes sur stock: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération impact ventes: {str(e)}"
        )


@router.get("/movements/from-sales", response_model=List[StockMovementResponse])
async def get_sales_movements(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    product_id: Optional[UUID] = Query(None, description="Filtrer par produit"),
    limit: int = Query(100, ge=1, le=1000)
):
    """
    Récupère tous les mouvements de stock générés par les ventes
    Point de communication avec le module sales
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        pharmacy_ids = _get_pharmacy_ids_from_user(db, current_user, tenant_id, pharmacy_id)
        
        if not pharmacy_ids:
            return []
        
        # Construire la requête
        query = db.query(StockMovement).filter(
            StockMovement.movement_type.in_(["sale", "sale_return"]),
            StockMovement.pharmacy_id.in_(pharmacy_ids)
        )
        
        if tenant_id:
            query = query.filter(StockMovement.tenant_id == tenant_id)
        
        if start_date:
            query = query.filter(func.date(StockMovement.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(StockMovement.created_at) <= end_date)
        
        if product_id:
            query = query.filter(StockMovement.product_id == product_id)
        
        # Trier par date décroissante
        movements = query.order_by(
            desc(StockMovement.created_at)
        ).limit(limit).all()
        
        return [
            StockMovementResponse(
                id=m.id,
                product_id=m.product_id,
                product_name=getattr(m.product, "name", None),
                product_code=getattr(m.product, "code", None),
                pharmacy_id=m.pharmacy_id,
                quantity_before=float(m.quantity_before or 0),
                quantity_after=float(m.quantity_after or 0),
                quantity_change=float(m.quantity_change or 0),
                movement_type=m.movement_type,
                reason=m.reason,
                reference=m.reference,
                batch_number=m.batch_number,
                cost_price=float(m.cost_price) if hasattr(m, "cost_price") else None,
                selling_price=float(m.selling_price) if hasattr(m, "selling_price") else None,
                sale_id=m.sale_id,
                sale_item_id=m.sale_item_id,
                created_at=m.created_at,
                created_by=m.created_by
            )
            for m in movements
        ]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération mouvements de ventes: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération mouvements: {str(e)}"
        )


@router.get("/product-sales-stats/{product_id}")
async def get_product_sales_stats(
    product_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Récupère les statistiques de vente pour un produit spécifique
    Utile pour la gestion du stock (prévisions, réapprovisionnement)
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Vérifier que le produit existe
        product_query = db.query(Product).filter(Product.id == product_id)
        if tenant_id:
            product_query = product_query.filter(Product.tenant_id == tenant_id)
        
        product = product_query.first()
        
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Produit non trouvé"
            )
        
        pharmacy_ids = _get_pharmacy_ids_from_user(db, current_user, tenant_id, pharmacy_id)
        
        if not pharmacy_ids:
            return {
                "product_id": str(product_id),
                "product_name": product.name,
                "product_code": product.code,
                "total_sold": 0,
                "total_revenue": 0,
                "sale_count": 0,
                "average_quantity_per_sale": 0,
                "daily_average": 0,
                "weekly_average": 0,
                "monthly_average": 0,
                "stock_turnover_rate": 0,
                "forecast": []
            }
        
        # Statistiques de base
        stats_query = db.query(
            func.coalesce(func.sum(SaleItem.quantity), 0).label("total_sold"),
            func.coalesce(func.sum(SaleItem.total), 0).label("total_revenue"),
            func.count(distinct(Sale.id)).label("sale_count"),
            func.avg(SaleItem.quantity).label("avg_quantity")
        ).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            SaleItem.product_id == product_id,
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids)
        )
        
        if tenant_id:
            stats_query = stats_query.filter(SaleItem.tenant_id == tenant_id)
            stats_query = stats_query.filter(Sale.tenant_id == tenant_id)
        
        if start_date:
            stats_query = stats_query.filter(func.date(Sale.created_at) >= start_date)
        if end_date:
            stats_query = stats_query.filter(func.date(Sale.created_at) <= end_date)
        
        stats = stats_query.first()
        
        # Moyennes sur différentes périodes
        now = datetime.now().date()
        
        # Moyenne journalière (30 derniers jours)
        daily_query = db.query(func.sum(SaleItem.quantity)).join(Sale).filter(
            SaleItem.product_id == product_id,
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids),
            func.date(Sale.created_at) >= now - timedelta(days=30)
        )
        if tenant_id:
            daily_query = daily_query.filter(SaleItem.tenant_id == tenant_id)
        daily_total = daily_query.scalar() or 0
        daily_average = daily_total / 30 if daily_total > 0 else 0
        
        # Moyenne hebdomadaire (8 dernières semaines)
        weekly_query = db.query(func.sum(SaleItem.quantity)).join(Sale).filter(
            SaleItem.product_id == product_id,
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids),
            func.date(Sale.created_at) >= now - timedelta(weeks=8)
        )
        if tenant_id:
            weekly_query = weekly_query.filter(SaleItem.tenant_id == tenant_id)
        weekly_total = weekly_query.scalar() or 0
        weekly_average = weekly_total / 8 if weekly_total > 0 else 0
        
        # Moyenne mensuelle (6 derniers mois)
        monthly_query = db.query(func.sum(SaleItem.quantity)).join(Sale).filter(
            SaleItem.product_id == product_id,
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids),
            func.date(Sale.created_at) >= now - timedelta(days=180)
        )
        if tenant_id:
            monthly_query = monthly_query.filter(SaleItem.tenant_id == tenant_id)
        monthly_total = monthly_query.scalar() or 0
        monthly_average = monthly_total / 6 if monthly_total > 0 else 0
        
        # Taux de rotation du stock
        stock = db.query(Product).filter(
            Product.id == product_id,
            Product.pharmacy_id.in_(pharmacy_ids)
        ).first()
        
        stock_turnover_rate = 0
        total_sold = stats.total_sold or 0
        if stock and stock.quantity > 0:
            stock_turnover_rate = total_sold / stock.quantity
        
        # Prévisions
        forecast = []
        for days in [7, 15, 30, 60, 90]:
            forecast.append({
                "period_days": days,
                "forecast_quantity": int(daily_average * days),
                "confidence": "medium"
            })
        
        return {
            "product_id": str(product_id),
            "product_name": product.name,
            "product_code": product.code,
            "total_sold": int(total_sold),
            "total_revenue": float(stats.total_revenue or 0),
            "sale_count": int(stats.sale_count or 0),
            "average_quantity_per_sale": float(stats.avg_quantity or 0),
            "daily_average": float(daily_average),
            "weekly_average": float(weekly_average),
            "monthly_average": float(monthly_average),
            "stock_turnover_rate": float(stock_turnover_rate),
            "forecast": forecast
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur statistiques de vente pour produit {product_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur statistiques de vente: {str(e)}"
        )


# =======================
# ROUTES POUR LES MOUVEMENTS DE STOCK
# =======================

@router.get("/movements", response_model=List[StockMovementResponse], summary="Mouvements de stock")
async def get_stock_movements(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    product_id: Optional[UUID] = Query(None, description="Filtrer par produit"),
    movement_type: Optional[str] = Query(None, description="Type de mouvement"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Récupère l'historique des mouvements de stock."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        query = db.query(StockMovement).filter(
            StockMovement.tenant_id == tenant_id,
            StockMovement.pharmacy_id == pharmacy.id
        )
        
        if product_id:
            query = query.filter(StockMovement.product_id == product_id)
        
        if movement_type:
            query = query.filter(StockMovement.movement_type == movement_type)
        
        if start_date:
            query = query.filter(func.date(StockMovement.created_at) >= start_date)
        
        if end_date:
            query = query.filter(func.date(StockMovement.created_at) <= end_date)
        
        movements = query.order_by(desc(StockMovement.created_at)).offset(skip).limit(limit).all()
        
        return [
            StockMovementResponse(
                id=m.id,
                product_id=m.product_id,
                product_name=getattr(m.product, "name", None),
                product_code=getattr(m.product, "code", None),
                pharmacy_id=m.pharmacy_id,
                quantity_before=float(m.quantity_before or 0),
                quantity_after=float(m.quantity_after or 0),
                quantity_change=float(m.quantity_change or 0),
                movement_type=m.movement_type,
                reason=m.reason,
                reference=m.reference,
                batch_number=m.batch_number,
                created_at=m.created_at,
                created_by=m.created_by
            )
            for m in movements
        ]
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur récupération mouvements stock")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


# =======================
# ROUTES POUR LES INVENTAIRES PHYSIQUES
# =======================

@router.post("/inventory-counts", summary="Créer un inventaire")
async def create_inventory_count(
    inventory_data: InventoryCountRequest,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Crée un nouvel inventaire physique."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Générer un numéro d'inventaire unique
        count_number = f"INV-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        
        # Compter le nombre total de produits
        total_products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.pharmacy_id == pharmacy.id,
            Product.is_active == True
        ).count()
        
        inventory = InventoryCount(
            tenant_id=tenant_id,
            count_number=count_number,
            count_date=inventory_data.count_date or date.today(),
            location=inventory_data.location,
            total_products=total_products,
            counted_products=0,
            status="pending",
            created_by=current_user.id,
            notes=inventory_data.notes
        )
        
        db.add(inventory)
        db.commit()
        db.refresh(inventory)
        
        logger.info(f"Inventaire créé: {inventory.count_number} par {current_user.email}")
        
        return {
            "message": "Inventaire créé avec succès",
            "inventory": inventory.to_dict()
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur création inventaire")
        raise HTTPException(status_code=400, detail=f"Erreur création inventaire: {exc}")


@router.get("/inventory-counts", summary="Liste des inventaires")
async def list_inventory_counts(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    status: Optional[str] = Query(None, description="Statut de l'inventaire"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Liste tous les inventaires physiques."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        query = db.query(InventoryCount).filter(
            InventoryCount.tenant_id == tenant_id
        )
        
        if status:
            query = query.filter(InventoryCount.status == status)
        
        total = query.count()
        inventories = query.order_by(desc(InventoryCount.created_at)).offset(skip).limit(limit).all()
        
        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "inventories": [inv.to_dict() for inv in inventories]
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur récupération inventaires")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/inventory-counts/{inventory_id}", summary="Détails d'un inventaire")
async def get_inventory_count(
    inventory_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """Récupère les détails d'un inventaire spécifique."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        tenant_id = current_tenant.id if current_tenant else None
        
        inventory = db.query(InventoryCount).filter(
            InventoryCount.id == inventory_id,
            InventoryCount.tenant_id == tenant_id
        ).first()
        
        if not inventory:
            raise HTTPException(status_code=404, detail="Inventaire non trouvé")
        
        return inventory.to_dict()
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur récupération inventaire")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.post("/inventory-counts/{inventory_id}/items", summary="Ajouter un item d'inventaire")
async def add_inventory_item(
    inventory_id: UUID,
    product_id: UUID,
    actual_quantity: int,
    comments: Optional[str] = None,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Ajoute un item compté à l'inventaire."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        tenant_id = current_tenant.id if current_tenant else None
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        
        # Récupérer l'inventaire
        inventory = db.query(InventoryCount).filter(
            InventoryCount.id == inventory_id,
            InventoryCount.tenant_id == tenant_id
        ).first()
        
        if not inventory:
            raise HTTPException(status_code=404, detail="Inventaire non trouvé")
        
        if inventory.status in ["completed", "validated", "cancelled"]:
            raise HTTPException(status_code=400, detail="Cet inventaire est déjà finalisé")
        
        # Récupérer le produit
        product = db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == tenant_id,
            Product.pharmacy_id == pharmacy.id,
            Product.is_active == True
        ).first()
        
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        # Vérifier si l'item existe déjà
        existing_item = db.query(InventoryCountItem).filter(
            InventoryCountItem.inventory_count_id == inventory_id,
            InventoryCountItem.product_id == product_id
        ).first()
        
        theoretical_quantity = product.quantity
        quantity_difference = actual_quantity - theoretical_quantity
        unit_price = float(product.purchase_price or 0)
        theoretical_value = theoretical_quantity * unit_price
        actual_value = actual_quantity * unit_price
        value_difference = actual_value - theoretical_value
        
        if existing_item:
            # Mettre à jour l'item existant
            existing_item.actual_quantity = actual_quantity
            existing_item.quantity_difference = quantity_difference
            existing_item.actual_value = actual_value
            existing_item.value_difference = value_difference
            existing_item.comments = comments or existing_item.comments
            existing_item.counted_at = datetime.utcnow()
            existing_item.status = "counted"
            item = existing_item
        else:
            # Créer un nouvel item
            item = InventoryCountItem(
                inventory_count_id=inventory_id,
                product_id=product_id,
                theoretical_quantity=theoretical_quantity,
                actual_quantity=actual_quantity,
                quantity_difference=quantity_difference,
                unit_price=unit_price,
                theoretical_value=theoretical_value,
                actual_value=actual_value,
                value_difference=value_difference,
                comments=comments,
                counted_at=datetime.utcnow(),
                status="counted"
            )
            db.add(item)
            
            # Mettre à jour le compteur
            inventory.counted_products += 1
        
        # Mettre à jour les totaux de l'inventaire
        if quantity_difference != 0:
            inventory.discrepancies += 1
        
        db.commit()
        
        return {
            "message": "Item d'inventaire ajouté avec succès",
            "item": item.to_dict(),
            "has_discrepancy": item.has_discrepancy
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur ajout item inventaire")
        raise HTTPException(status_code=400, detail=f"Erreur ajout item inventaire: {exc}")


@router.post("/inventory-counts/{inventory_id}/complete", summary="Finaliser un inventaire")
async def complete_inventory_count(
    inventory_id: UUID,
    validate_changes: bool = Query(True, description="Appliquer les ajustements de stock"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """Finalise un inventaire et applique les ajustements."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
        
        tenant_id = current_tenant.id if current_tenant else None
        
        inventory = db.query(InventoryCount).filter(
            InventoryCount.id == inventory_id,
            InventoryCount.tenant_id == tenant_id
        ).first()
        
        if not inventory:
            raise HTTPException(status_code=404, detail="Inventaire non trouvé")
        
        if inventory.status != "pending":
            raise HTTPException(status_code=400, detail="Cet inventaire ne peut pas être finalisé")
        
        # Récupérer tous les items
        items = db.query(InventoryCountItem).filter(
            InventoryCountItem.inventory_count_id == inventory_id
        ).all()
        
        # Calculer les totaux
        total_theoretical_value = 0.0
        total_actual_value = 0.0
        
        for item in items:
            total_theoretical_value += float(item.theoretical_value or 0)
            total_actual_value += float(item.actual_value or 0)
        
        inventory.theoretical_value = total_theoretical_value
        inventory.actual_value = total_actual_value
        inventory.difference_value = total_actual_value - total_theoretical_value
        
        if validate_changes:
            # Appliquer les ajustements de stock
            for item in items:
                if item.quantity_difference != 0:
                    product = db.query(Product).filter(
                        Product.id == item.product_id,
                        Product.tenant_id == tenant_id
                    ).first()
                    
                    if product:
                        old_quantity = product.quantity
                        product.quantity = item.actual_quantity
                        product.available_quantity = max(0, item.actual_quantity - product.reserved_quantity)
                        product.last_adjustment_date = datetime.utcnow()
                        product.refresh_statuses()
                        
                        # Créer un mouvement de stock
                        movement = StockMovement(
                            tenant_id=tenant_id,
                            product_id=product.id,
                            pharmacy_id=product.pharmacy_id,
                            quantity_before=old_quantity,
                            quantity_after=item.actual_quantity,
                            quantity_change=item.quantity_difference,
                            movement_type="inventory",
                            reason=f"Inventaire #{inventory.count_number}",
                            reference=inventory.count_number,
                            created_by=current_user.id
                        )
                        db.add(movement)
                        
                        item.validated_at = datetime.utcnow()
                        item.status = "validated"
            
            inventory.status = "completed"
            inventory.completed_at = datetime.utcnow()
        else:
            inventory.status = "completed"
            inventory.completed_at = datetime.utcnow()
        
        db.commit()
        
        logger.info(f"Inventaire finalisé: {inventory.count_number} par {current_user.email}")
        
        return {
            "message": "Inventaire finalisé avec succès",
            "inventory": inventory.to_dict(),
            "adjustments_applied": validate_changes
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur finalisation inventaire")
        raise HTTPException(status_code=400, detail=f"Erreur finalisation inventaire: {exc}")


# =======================
# ROUTES POUR LES STATISTIQUES AVANCÉES
# =======================

@router.get("/turnover", summary="Taux de rotation du stock")
async def get_stock_turnover(
    days: int = Query(365, ge=30, le=730),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """Retourne le taux de rotation du stock."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        tenant_id = current_tenant.id if current_tenant else None
        
        inventory_service = get_inventory_service(db, tenant_id)
        turnover_data = inventory_service.get_stock_turnover_rate(pharmacy_id=pharmacy_id, days=days)
        
        return turnover_data
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur calcul taux rotation")
        raise HTTPException(status_code=500, detail=f"Erreur calcul taux rotation: {exc}")


@router.get("/valuation", summary="Valeur du stock")
async def get_stock_valuation(
    method: str = Query("purchase", description="Méthode d'évaluation: purchase, selling, average"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """Retourne la valeur totale du stock."""
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        tenant_id = current_tenant.id if current_tenant else None
        
        inventory_service = get_inventory_service(db, tenant_id)
        valuation = inventory_service.calculate_stock_value(pharmacy_id, method)
        
        return valuation
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur calcul valeur stock")
        raise HTTPException(status_code=500, detail=f"Erreur calcul valeur stock: {exc}")

# =======================
# ROUTES POUR L'IMPORT
# =======================

@router.post("/import/preview", summary="Prévisualiser l'import de produits")
async def preview_import_products(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Prépare un aperçu de l'import de produits depuis un fichier Excel/CSV.
    Détecte les doublons et retourne un aperçu avant import.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Lire le contenu du fichier
        contents = await file.read()
        
        # Déterminer le type de fichier
        filename = file.filename or ""
        file_ext = filename.split(".")[-1].lower() if "." in filename else ""
        
        import pandas as pd
        import io
        
        df = None
        if file_ext in ["xlsx", "xls"]:
            df = pd.read_excel(io.BytesIO(contents))
        elif file_ext == "csv":
            df = pd.read_csv(io.BytesIO(contents))
        else:
            raise HTTPException(
                status_code=400,
                detail="Format de fichier non supporté. Utilisez .xlsx, .xls ou .csv"
            )
        
        if df.empty:
            raise HTTPException(status_code=400, detail="Le fichier est vide")
        
        # Normaliser les noms de colonnes
        column_mapping = {
            'nom': 'name',
            'name': 'name',
            'produit': 'name',
            'code': 'code',
            'code-barres': 'barcode',
            'barcode': 'barcode',
            'quantite': 'quantity',
            'quantité': 'quantity',
            'qte': 'quantity',
            'prix_achat': 'purchase_price',
            'prix achat': 'purchase_price',
            'prix_achat_ht': 'purchase_price',
            'prix_vente': 'selling_price',
            'prix vente': 'selling_price',
            'prix_vente_ttc': 'selling_price',
            'date_expiration': 'expiry_date',
            'expiration': 'expiry_date',
            'date peremption': 'expiry_date',
            'categorie': 'category',
            'catégorie': 'category',
            'category': 'category',
            'emplacement': 'location',
            'location': 'location',
            'fournisseur': 'supplier',
            'supplier': 'supplier',
            'lot': 'batch_number',
            'batch': 'batch_number',
            'numero_lot': 'batch_number'
        }
        
        # Renommer les colonnes
        df.columns = [column_mapping.get(col.lower().strip(), col.lower().strip()) for col in df.columns]
        
        # Colonnes requises
        required_columns = ['name', 'quantity', 'purchase_price', 'selling_price']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            raise HTTPException(
                status_code=400,
                detail=f"Colonnes manquantes: {', '.join(missing_columns)}"
            )
        
        # Traiter les données
        products_preview = []
        duplicates = []
        new_products = []
        
        # Récupérer tous les produits existants pour la détection des doublons
        existing_products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.pharmacy_id == pharmacy.id,
            Product.is_active == True
        ).all()
        
        # Créer un index pour la recherche rapide
        existing_by_code = {p.code: p for p in existing_products if p.code}
        existing_by_barcode = {p.barcode: p for p in existing_products if p.barcode}
        existing_by_name = {p.name.lower(): p for p in existing_products}
        
        for idx, row in df.iterrows():
            try:
                # Extraire les valeurs
                name = str(row.get('name', '')).strip()
                if not name:
                    continue
                
                quantity = int(float(row.get('quantity', 0)))
                purchase_price = float(row.get('purchase_price', 0))
                selling_price = float(row.get('selling_price', 0))
                code = str(row.get('code', '')).strip() if pd.notna(row.get('code')) else None
                barcode = str(row.get('barcode', '')).strip() if pd.notna(row.get('barcode')) else None
                expiry_date = row.get('expiry_date')
                category = str(row.get('category', '')).strip() if pd.notna(row.get('category')) else None
                location = str(row.get('location', '')).strip() if pd.notna(row.get('location')) else None
                supplier = str(row.get('supplier', '')).strip() if pd.notna(row.get('supplier')) else None
                batch_number = str(row.get('batch_number', '')).strip() if pd.notna(row.get('batch_number')) else None
                
                # Convertir la date d'expiration
                expiry_date_obj = None
                if expiry_date and pd.notna(expiry_date):
                    try:
                        if isinstance(expiry_date, str):
                            expiry_date_obj = datetime.strptime(expiry_date, "%Y-%m-%d").date()
                        else:
                            expiry_date_obj = expiry_date.date() if hasattr(expiry_date, 'date') else expiry_date
                    except:
                        pass
                
                # Détecter les doublons
                existing_product = None
                if code and code in existing_by_code:
                    existing_product = existing_by_code[code]
                elif barcode and barcode in existing_by_barcode:
                    existing_product = existing_by_barcode[barcode]
                elif name.lower() in existing_by_name:
                    existing_product = existing_by_name[name.lower()]
                
                product_data = {
                    "name": name,
                    "code": code,
                    "barcode": barcode,
                    "quantity": quantity,
                    "purchase_price": purchase_price,
                    "selling_price": selling_price,
                    "expiry_date": expiry_date_obj.isoformat() if expiry_date_obj else None,
                    "category": category,
                    "location": location,
                    "supplier": supplier,
                    "batch_number": batch_number,
                    "existing_product": existing_product.to_dict() if existing_product else None,
                    "action": None
                }
                
                products_preview.append(product_data)
                
                if existing_product:
                    duplicates.append(product_data)
                else:
                    new_products.append(product_data)
                    
            except Exception as e:
                logger.warning(f"Erreur traitement ligne {idx}: {e}")
                continue
        
        return {
            "products": products_preview,
            "duplicates": duplicates,
            "new_products": new_products,
            "total_rows": len(df),
            "valid_rows": len(products_preview),
            "skipped_rows": len(df) - len(products_preview),
            "columns_used": list(df.columns)
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur preview import")
        raise HTTPException(status_code=500, detail=f"Erreur preview import: {exc}")

@router.post("/import", summary="Importer des produits")
async def import_products(
    file: UploadFile = File(...),
    mode: str = Form("add"),  # add, update, replace
    duplicate_actions: Optional[str] = Form(None),
    preserve_prices: bool = Form(False),  # Nouveau: conserver les prix du fichier
    preserve_quantities: bool = Form(False),  # Nouveau: conserver les quantités du fichier
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Importe des produits depuis un fichier Excel/CSV.
    
    Modes supportés:
    - add: Ignore les doublons, n'ajoute que les nouveaux
    - update: Met à jour les produits existants
    - replace: Remplace complètement les produits existants (désactive les anciens)
    
    Options:
    - preserve_prices: Si True, conserve les prix exacts du fichier sans recalcul
    - preserve_quantities: Si True, conserve les quantités exactes du fichier
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        branch_id = current_branch.id if current_branch else None
        
        # Lire le contenu du fichier
        contents = await file.read()
        
        # Déterminer le type de fichier
        filename = file.filename or ""
        file_ext = filename.split(".")[-1].lower() if "." in filename else ""
        
        import pandas as pd
        import io
        import json
        
        df = None
        if file_ext in ["xlsx", "xls"]:
            df = pd.read_excel(io.BytesIO(contents))
        elif file_ext == "csv":
            df = pd.read_csv(io.BytesIO(contents))
        else:
            raise HTTPException(
                status_code=400,
                detail="Format de fichier non supporté. Utilisez .xlsx, .xls ou .csv"
            )
        
        if df.empty:
            raise HTTPException(status_code=400, detail="Le fichier est vide")
        
        # Normaliser les noms de colonnes
        column_mapping = {
            'nom': 'name', 'name': 'name', 'produit': 'name',
            'code': 'code', 'code-barres': 'barcode', 'barcode': 'barcode',
            'quantite': 'quantity', 'quantité': 'quantity', 'qte': 'quantity',
            'prix_achat': 'purchase_price', 'prix achat': 'purchase_price',
            'prix_vente': 'selling_price', 'prix vente': 'selling_price',
            'date_expiration': 'expiry_date', 'expiration': 'expiry_date',
            'categorie': 'category', 'catégorie': 'category', 'category': 'category',
            'emplacement': 'location', 'location': 'location',
            'fournisseur': 'supplier', 'supplier': 'supplier',
            'lot': 'batch_number', 'batch': 'batch_number', 'numero_lot': 'batch_number'
        }
        
        df.columns = [column_mapping.get(col.lower().strip(), col.lower().strip()) for col in df.columns]
        
        # Vérifier les colonnes requises
        required_columns = ['name', 'quantity', 'purchase_price', 'selling_price']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            raise HTTPException(
                status_code=400,
                detail=f"Colonnes manquantes: {', '.join(missing_columns)}"
            )
        
        # Parse duplicate actions
        duplicate_actions_dict = {}
        if duplicate_actions:
            try:
                duplicate_actions_dict = json.loads(duplicate_actions)
            except:
                pass
        
        # Mode replace: désactiver tous les produits existants
        if mode == "replace":
            db.query(Product).filter(
                Product.tenant_id == tenant_id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active == True
            ).update({"is_active": False, "deleted_at": datetime.utcnow()})
            db.flush()
        
        # Récupérer les produits existants pour le mode update
        existing_products = {}
        if mode in ["update", "add"]:
            products_list = db.query(Product).filter(
                Product.tenant_id == tenant_id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active == True
            ).all()
            
            for p in products_list:
                if p.code:
                    existing_products[p.code] = p
                if p.barcode:
                    existing_products[p.barcode] = p
                existing_products[p.name.lower()] = p
        
        # Traiter les données
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        # Configuration des prix (uniquement si preservation_prices est False)
        calcul_auto_prix = bool(_tenant_get_config(current_tenant, "calcul_auto_prix", True)) if not preserve_prices else False
        marge_par_defaut = _to_float(_tenant_get_config(current_tenant, "marge_par_defaut", 30.0), 30.0)
        taux_tva = _to_float(_tenant_get_config(current_tenant, "taux_tva", 0.0), 0.0)
        
        for idx, row in df.iterrows():
            try:
                name = str(row.get('name', '')).strip()
                if not name:
                    skipped_count += 1
                    continue
                
                # Récupérer les valeurs du fichier
                file_quantity = int(float(row.get('quantity', 0)))
                file_purchase_price = float(row.get('purchase_price', 0))
                file_selling_price = float(row.get('selling_price', 0))
                
                # Déterminer les valeurs finales selon preserve_* flags
                if preserve_quantities:
                    quantity = file_quantity
                else:
                    quantity = file_quantity  # Par défaut, utiliser la valeur du fichier
                
                if preserve_prices:
                    purchase_price = file_purchase_price
                    selling_price = file_selling_price
                else:
                    purchase_price = file_purchase_price
                    selling_price = file_selling_price
                
                code = str(row.get('code', '')).strip() if pd.notna(row.get('code')) else None
                barcode = str(row.get('barcode', '')).strip() if pd.notna(row.get('barcode')) else None
                expiry_date = row.get('expiry_date')
                category = str(row.get('category', '')).strip() if pd.notna(row.get('category')) else None
                location = str(row.get('location', '')).strip() if pd.notna(row.get('location')) else None
                supplier = str(row.get('supplier', '')).strip() if pd.notna(row.get('supplier')) else None
                batch_number = str(row.get('batch_number', '')).strip() if pd.notna(row.get('batch_number')) else None
                
                # Convertir la date d'expiration
                expiry_date_obj = None
                if expiry_date and pd.notna(expiry_date):
                    try:
                        if isinstance(expiry_date, str):
                            expiry_date_obj = datetime.strptime(expiry_date, "%Y-%m-%d").date()
                        else:
                            expiry_date_obj = expiry_date.date() if hasattr(expiry_date, 'date') else expiry_date
                    except:
                        pass
                
                # Chercher le produit existant
                existing = None
                key = None
                
                # Vérifier par code
                if code and code in existing_products:
                    existing = existing_products[code]
                    key = code
                # Vérifier par code-barres
                elif barcode and barcode in existing_products:
                    existing = existing_products[barcode]
                    key = barcode
                # Vérifier par nom
                elif name.lower() in existing_products:
                    existing = existing_products[name.lower()]
                    key = name.lower()
                
                if existing:
                    # Produit existant
                    action = duplicate_actions_dict.get(str(idx), duplicate_actions_dict.get(key, "update"))
                    
                    if action == "skip":
                        skipped_count += 1
                        continue
                    elif action == "update":
                        # Mettre à jour le produit existant
                        old_quantity = existing.quantity
                        
                        # Appliquer les valeurs selon les flags
                        if not preserve_prices:
                            existing.purchase_price = purchase_price
                            existing.selling_price = selling_price
                        else:
                            # Si preserve_prices est True, on garde les prix existants
                            pass
                        
                        if preserve_quantities:
                            existing.quantity = quantity
                            existing.available_quantity = max(0, quantity - (existing.reserved_quantity or 0))
                        else:
                            existing.quantity = quantity
                            existing.available_quantity = max(0, quantity - (existing.reserved_quantity or 0))
                        
                        if expiry_date_obj:
                            existing.expiry_date = expiry_date_obj
                        if category:
                            existing.category = category
                        if location:
                            existing.location = location
                        if supplier:
                            existing.main_supplier = supplier
                        if batch_number:
                            existing.batch_number = batch_number
                        
                        existing.refresh_statuses()
                        
                        # Créer un mouvement de stock si la quantité a changé
                        if quantity != old_quantity:
                            movement = StockMovement(
                                tenant_id=tenant_id,
                                product_id=existing.id,
                                pharmacy_id=pharmacy.id,
                                branch_id=branch_id,
                                quantity_before=old_quantity,
                                quantity_after=quantity,
                                quantity_change=quantity - old_quantity,
                                movement_type="import",
                                reason=f"Import via fichier ({mode})",
                                created_by=current_user.id
                            )
                            db.add(movement)
                        
                        updated_count += 1
                        
                    elif action == "merge_quantity":
                        # Fusionner les quantités
                        old_quantity = existing.quantity
                        new_quantity = old_quantity + quantity
                        existing.quantity = new_quantity
                        existing.available_quantity = max(0, new_quantity - (existing.reserved_quantity or 0))
                        
                        if not preserve_prices:
                            if purchase_price:
                                existing.purchase_price = purchase_price
                            if selling_price:
                                existing.selling_price = selling_price
                        
                        existing.refresh_statuses()
                        
                        movement = StockMovement(
                            tenant_id=tenant_id,
                            product_id=existing.id,
                            pharmacy_id=pharmacy.id,
                            branch_id=branch_id,
                            quantity_before=old_quantity,
                            quantity_after=new_quantity,
                            quantity_change=quantity,
                            movement_type="import",
                            reason=f"Fusion import (ajout de {quantity})",
                            created_by=current_user.id
                        )
                        db.add(movement)
                        
                        updated_count += 1
                    else:
                        # keep_both: créer un nouveau produit
                        existing = None
                else:
                    existing = None
                
                if not existing:
                    # Créer un nouveau produit
                    product = Product(
                        tenant_id=tenant_id,
                        pharmacy_id=pharmacy.id,
                        branch_id=branch_id,
                        name=name,
                        code=code,
                        barcode=barcode,
                        purchase_price=purchase_price,
                        selling_price=selling_price,
                        quantity=quantity,
                        available_quantity=quantity,
                        reserved_quantity=0,
                        expiry_date=expiry_date_obj,
                        category=category,
                        location=location,
                        main_supplier=supplier,
                        batch_number=batch_number,
                        is_active=True
                    )
                    
                    # Calcul automatique des prix UNIQUEMENT si preserve_prices est False
                    #if not preserve_prices and calcul_auto_prix and purchase_price > 0:
                    #    _safe_calculate_prices(product, marge_par_defaut, taux_tva)
                    
                    product.refresh_statuses()
                    
                    db.add(product)
                    db.flush()
                    
                    # Créer un mouvement de stock initial
                    if quantity > 0:
                        movement = StockMovement(
                            tenant_id=tenant_id,
                            product_id=product.id,
                            pharmacy_id=pharmacy.id,
                            branch_id=branch_id,
                            quantity_before=0,
                            quantity_after=quantity,
                            quantity_change=quantity,
                            movement_type="import",
                            reason="Import via fichier",
                            created_by=current_user.id
                        )
                        db.add(movement)
                    
                    created_count += 1
                    
                    # Ajouter à la cache pour les lignes suivantes
                    if code:
                        existing_products[code] = product
                    if barcode:
                        existing_products[barcode] = product
                    existing_products[name.lower()] = product
                
            except Exception as e:
                logger.error(f"Erreur ligne {idx}: {e}")
                skipped_count += 1
                continue
        
        db.commit()
        
        logger.info(
            f"Import terminé: {created_count} créés, {updated_count} mis à jour, {skipped_count} ignorés"
        )
        
        return {
            "success": True,
            "message": f"Import terminé: {created_count} créés, {updated_count} mis à jour, {skipped_count} ignorés",
            "created": created_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "total_processed": created_count + updated_count + skipped_count,
            "mode": mode,
            "preserve_prices": preserve_prices,
            "preserve_quantities": preserve_quantities
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur import produits")
        raise HTTPException(status_code=500, detail=f"Erreur import produits: {exc}")

@router.get("/import/template", summary="Télécharger le template d'import")
async def download_import_template(
    format: str = Query("excel", description="Format du template: excel, csv"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Télécharge le template d'import des produits au format Excel ou CSV.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        import pandas as pd
        import io
        
        # Définir les colonnes du template
        columns = [
            "name", "code", "barcode", "quantity", "purchase_price",
            "selling_price", "expiry_date", "category", "location",
            "supplier", "batch_number"
        ]
        
        # Lignes d'exemple
        example_data = [
            ["Paracétamol 500mg", "PARA001", "1234567890123", 100, 2.5, 5.0, "2025-12-31", "Médicaments", "A1", "PharmaDistrib", "LOT001"],
            ["Vitamine C 1000mg", "VITC001", "1234567890124", 50, 3.0, 6.0, "2025-10-15", "Compléments", "B2", "VitaLab", "LOT002"],
            ["Pansements", "PAN001", "1234567890125", 200, 1.2, 2.5, "2026-01-01", "Matériel médical", "C3", "MediCare", "LOT003"],
        ]
        
        df = pd.DataFrame(example_data, columns=columns)
        
        # Ajouter une ligne de description
        description = pd.DataFrame([[
            "Nom du produit", "Code unique", "Code-barres EAN13", "Quantité en stock",
            "Prix d'achat HT", "Prix de vente TTC", "YYYY-MM-DD", "Catégorie",
            "Emplacement", "Fournisseur", "Numéro de lot"
        ]], columns=columns)
        
        df = pd.concat([description, df], ignore_index=True)
        
        if format.lower() == "csv":
            output = io.StringIO()
            df.to_csv(output, index=False)
            content = output.getvalue()
            media_type = "text/csv"
            filename = "import_template.csv"
        else:
            output = io.BytesIO()
            df.to_excel(output, index=False, engine="openpyxl")
            content = output.getvalue()
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = "import_template.xlsx"
        
        from fastapi.responses import Response
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur génération template")
        raise HTTPException(status_code=500, detail=f"Erreur génération template: {exc}")


@router.delete("/{product_id}", status_code=status.HTTP_200_OK)
async def delete_product(
    product_id: UUID,
    request: Request,
    deletion_reason: Optional[str] = Query(None, description="Raison de la suppression"),
    permanent: bool = Query(False, description="Suppression définitive (skip trash)"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity)
):
    """
    Supprime un produit (mise en corbeille par défaut)
    """
    from app.services.history_service import HistoryService
    from app.api.v1.endpoints.trash_bin import move_to_trash
    
    if current_user.role not in ["admin", "super_admin", "superadmin", "gestionnaire"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Seuls les administrateurs peuvent supprimer des produits."
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    product = db.query(Product).filter(
        Product.id == product_id,
        Product.tenant_id == tenant_id
    ).first()
    
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Produit non trouvé"
        )
    
    history_service = HistoryService(db)
    
    if permanent:
        # Suppression définitive
        product_data = product.to_dict(include_details=True)
        db.delete(product)
        db.commit()
        
        history_service.log_delete(
            user=current_user,
            module="product",
            entity_id=product.id,
            entity_reference=product.code,
            entity_name=product.name,
            data=product_data,
            deletion_reason=deletion_reason,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            metadata={"permanent": True}
        )
        
        return {
            "message": "Produit supprimé définitivement",
            "product_id": str(product_id),
            "product_name": product.name
        }
    else:
        # Mise en corbeille
        product_data = product.to_dict(include_details=True)
        
        # Sauvegarder dans la corbeille
        trash_item = move_to_trash(
            db=db,
            tenant_id=tenant_id,
            pharmacy_id=current_pharmacy.id if current_pharmacy else product.pharmacy_id,
            item_type="product",
            original_id=product.id,
            original_reference=product.code,
            original_name=product.name,
            data=product_data,
            deleted_by_id=current_user.id,
            deleted_by_name=current_user.nom_complet,
            deleted_by_email=current_user.email,
            deletion_reason=deletion_reason,
            auto_delete_days=30  # Suppression automatique après 30 jours
        )
        
        # Supprimer le produit de la base
        db.delete(product)
        db.commit()
        
        # Enregistrer dans l'historique
        history_service.log_delete(
            user=current_user,
            module="product",
            entity_id=product.id,
            entity_reference=product.code,
            entity_name=product.name,
            data=product_data,
            deletion_reason=deletion_reason,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            metadata={"trash_id": str(trash_item.id)}
        )
        
        return {
            "message": "Produit déplacé dans la corbeille",
            "product_id": str(product_id),
            "product_name": product.name,
            "trash_id": str(trash_item.id),
            "auto_delete_at": trash_item.auto_delete_at.isoformat() if trash_item.auto_delete_at else None
        }

@router.get("/by-branch/{branch_id}", summary="Stock par succursale")
async def get_stock_by_branch(
    branch_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = None,
    category_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """Récupère le stock d'une succursale spécifique."""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Vérifier que la branche appartient au tenant
        branch = db.query(Branch).filter(
            Branch.id == branch_id,
            Branch.tenant_id == tenant_id,
            Branch.is_active == True
        ).first()
        
        if not branch:
            raise HTTPException(status_code=404, detail="Succursale non trouvée")
        
        query = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.branch_id == branch_id,
            Product.is_active == True
        )
        
        if search:
            query = query.filter(
                or_(
                    Product.name.ilike(f"%{search}%"),
                    Product.code.ilike(f"%{search}%"),
                    Product.barcode.ilike(f"%{search}%")
                )
            )
        
        if category_id:
            query = query.filter(Product.category_id == category_id)
        
        total = query.count()
        products = query.offset(skip).limit(limit).all()
        
        # Récupérer la configuration pour la conversion
        config = db.query(PharmacyConfig).filter(
            PharmacyConfig.pharmacy_id == branch.parent_pharmacy_id,
            PharmacyConfig.is_active == True
        ).first()
        
        exchange_rate = config.exchange_rate if config and config.exchange_rate else 1.0
        primary_currency = config.primary_currency if config else "CDF"
        
        stats = {
            "total_products": total,
            "total_quantity": sum(p.quantity or 0 for p in products),
            "total_value_cdf": sum((p.selling_price or 0) * (p.quantity or 0) for p in products),
            "total_value_usd": sum(((p.selling_price or 0) / exchange_rate) * (p.quantity or 0) for p in products),
            "out_of_stock": len([p for p in products if p.stock_status == "out_of_stock"]),
            "low_stock": len([p for p in products if p.stock_status == "low_stock"])
        }
        
        return {
            "branch": {
                "id": str(branch.id),
                "name": branch.name,
                "code": branch.code,
                "parent_pharmacy_id": str(branch.parent_pharmacy_id) if branch.parent_pharmacy_id else None
            },
            "exchange_rate": exchange_rate,
            "primary_currency": primary_currency,
            "stats": stats,
            "total": total,
            "skip": skip,
            "limit": limit,
            "products": [_serialize_product_with_conversion(p, exchange_rate, primary_currency) for p in products]
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur récupération stock par branche")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


def _serialize_product_with_conversion(product: Product, exchange_rate: float, primary_currency: str) -> Dict[str, Any]:
    """Sérialise un produit avec conversion des prix."""
    result = product.to_dict(include_details=False)
    
    if primary_currency == "CDF":
        result["selling_price_cdf"] = result.get("selling_price", 0)
        result["selling_price_usd"] = round(result.get("selling_price", 0) / exchange_rate, 2) if exchange_rate > 0 else 0
    else:
        result["selling_price_usd"] = result.get("selling_price", 0)
        result["selling_price_cdf"] = round(result.get("selling_price", 0) * exchange_rate, 2)
    
    result["exchange_rate"] = exchange_rate
    result["primary_currency"] = primary_currency
    
    return result
        


@router.get("/branches-stock-overview", summary="Vue d'ensemble du stock par branche")
async def get_branches_stock_overview(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Retourne une vue d'ensemble du stock pour toutes les branches de la pharmacie.
    L'admin peut voir toutes les branches, les vendeurs seulement leur branche.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien", "vendeur"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Récupérer les branches de la pharmacie
        branches_query = db.query(Branch).filter(
            Branch.tenant_id == tenant_id,
            Branch.parent_pharmacy_id == pharmacy.id,
            Branch.is_active == True
        )
        
        # Si l'utilisateur n'est pas admin, filtrer par sa branche active
        if current_user.role not in ["super_admin", "superadmin", "admin", "gerant"]:
            branches_query = branches_query.filter(Branch.id == current_user.active_branch_id)
        
        branches = branches_query.all()
        
        result = []
        total_overall = {
            "total_products": 0,
            "total_quantity": 0,
            "total_value": 0,
            "out_of_stock": 0,
            "low_stock": 0,
            "expired": 0,
            "expiring_soon": 0
        }
        
        for branch in branches:
            # Récupérer les produits de la branche
            products = db.query(Product).filter(
                Product.tenant_id == tenant_id,
                Product.pharmacy_id == pharmacy.id,
                Product.branch_id == branch.id,
                Product.is_active == True
            ).all()
            
            branch_stats = {
                "total_products": len(products),
                "total_quantity": sum(p.quantity or 0 for p in products),
                "total_purchase_value": sum((p.purchase_price or 0) * (p.quantity or 0) for p in products),
                "total_selling_value": sum((p.selling_price or 0) * (p.quantity or 0) for p in products),
                "out_of_stock": len([p for p in products if p.stock_status == "out_of_stock"]),
                "low_stock": len([p for p in products if p.stock_status == "low_stock"]),
                "expired": len([p for p in products if p.expiry_status == "expired"]),
                "expiring_soon": len([p for p in products if p.expiry_status in ["critical", "warning"]])
            }
            
            # Ajouter aux totaux globaux
            total_overall["total_products"] += branch_stats["total_products"]
            total_overall["total_quantity"] += branch_stats["total_quantity"]
            total_overall["total_value"] += branch_stats["total_selling_value"]
            total_overall["out_of_stock"] += branch_stats["out_of_stock"]
            total_overall["low_stock"] += branch_stats["low_stock"]
            total_overall["expired"] += branch_stats["expired"]
            total_overall["expiring_soon"] += branch_stats["expiring_soon"]
            
            result.append({
                "branch": {
                    "id": str(branch.id),
                    "name": branch.name,
                    "code": branch.code,
                    "is_main_branch": branch.is_main_branch
                },
                "stats": branch_stats,
                "products": [_serialize_product(p) for p in products[:20]]  # Limite à 20 produits par branche
            })
        
        return {
            "pharmacy": {
                "id": str(pharmacy.id),
                "name": pharmacy.name
            },
            "total_branches": len(branches),
            "total_overall": total_overall,
            "branches": result
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur récupération vue d'ensemble stock par branche")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")

@router.get("/branch-stock-dashboard", summary="Tableau de bord stock par branche")
async def get_branch_stock_dashboard(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Tableau de bord comparatif du stock entre les branches.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Récupérer toutes les branches
        branches = db.query(Branch).filter(
            Branch.tenant_id == tenant_id,
            Branch.parent_pharmacy_id == pharmacy.id,
            Branch.is_active == True
        ).all()
        
        dashboard = {
            "pharmacy": {
                "id": str(pharmacy.id),
                "name": pharmacy.name
            },
            "branches": [],
            "comparison": {
                "best_selling_branch": None,
                "highest_value_branch": None,
                "lowest_stock_branch": None
            }
        }
        
        branch_data = []
        
        for branch in branches:
            # Statistiques de la branche
            products = db.query(Product).filter(
                Product.tenant_id == tenant_id,
                Product.pharmacy_id == pharmacy.id,
                Product.branch_id == branch.id,
                Product.is_active == True
            ).all()
            
            # Ventes des 30 derniers jours pour cette branche
            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            sales_stats = db.query(
                func.coalesce(func.sum(SaleItem.quantity), 0).label("total_sold"),
                func.coalesce(func.sum(SaleItem.total), 0).label("total_revenue")
            ).join(Sale).filter(
                Sale.branch_id == branch.id,
                Sale.status == "completed",
                Sale.created_at >= thirty_days_ago
            ).first()
            
            branch_info = {
                "branch": {
                    "id": str(branch.id),
                    "name": branch.name,
                    "code": branch.code
                },
                "stock_stats": {
                    "total_products": len(products),
                    "total_quantity": sum(p.quantity or 0 for p in products),
                    "total_value": sum((p.selling_price or 0) * (p.quantity or 0) for p in products),
                    "out_of_stock": len([p for p in products if p.stock_status == "out_of_stock"]),
                    "low_stock": len([p for p in products if p.stock_status == "low_stock"])
                },
                "sales_stats": {
                    "last_30_days_sold": int(sales_stats.total_sold or 0),
                    "last_30_days_revenue": float(sales_stats.total_revenue or 0)
                },
                "turnover_rate": round(
                    (sales_stats.total_sold or 0) / max(1, sum(p.quantity or 0 for p in products)), 
                    2
                )
            }
            
            branch_data.append(branch_info)
        
        # Trouver les meilleures branches
        if branch_data:
            dashboard["comparison"]["highest_value_branch"] = max(
                branch_data, 
                key=lambda x: x["stock_stats"]["total_value"]
            )["branch"]["name"]
            
            dashboard["comparison"]["best_selling_branch"] = max(
                branch_data,
                key=lambda x: x["sales_stats"]["last_30_days_revenue"]
            )["branch"]["name"]
            
            dashboard["comparison"]["lowest_stock_branch"] = min(
                branch_data,
                key=lambda x: x["stock_stats"]["total_quantity"]
            )["branch"]["name"]
        
        dashboard["branches"] = branch_data
        
        return dashboard
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur récupération tableau de bord stock par branche")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")

@router.get("/export-by-branch", summary="Exporter le stock par branche")
async def export_stock_by_branch(
    format: str = Query("excel", description="Format: excel, csv"),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par branche specifique"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Exporte le stock de toutes les branches ou d'une branche specifique.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Recuperer les branches
        branches_query = db.query(Branch).filter(
            Branch.tenant_id == tenant_id,
            Branch.parent_pharmacy_id == pharmacy.id,
            Branch.is_active == True
        )
        
        if branch_id:
            branches_query = branches_query.filter(Branch.id == branch_id)
        
        branches = branches_query.all()
        
        # Preparer les donnees pour l'export
        export_data = []
        
        for branch in branches:
            products = db.query(Product).filter(
                Product.tenant_id == tenant_id,
                Product.pharmacy_id == pharmacy.id,
                Product.branch_id == branch.id,
                Product.is_active == True
            ).all()
            
            for product in products:
                export_data.append({
                    "Branche": branch.name,
                    "Code branche": branch.code,
                    "Nom produit": product.name,
                    "Code produit": product.code or "",
                    "Code-barres": product.barcode or "",
                    "Quantite": product.quantity or 0,
                    "Prix d'achat": float(product.purchase_price or 0),
                    "Prix de vente": float(product.selling_price or 0),
                    "Valeur totale": float((product.selling_price or 0) * (product.quantity or 0)),
                    "Date expiration": product.expiry_date.isoformat() if product.expiry_date else "",
                    "Categorie": product.category or "",
                    "Emplacement": product.location or "",
                    "Statut stock": product.stock_status or "normal",
                    "Statut expiration": product.expiry_status or "normal"
                })
        
        # Exporter selon le format
        import pandas as pd
        import io
        
        df = pd.DataFrame(export_data)
        
        if format.lower() == "csv":
            output = io.StringIO()
            df.to_csv(output, index=False, encoding='utf-8-sig')
            content = output.getvalue().encode('utf-8')
            media_type = "text/csv"
            filename = f"stock_par_branche_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        else:
            output = io.BytesIO()
            df.to_excel(output, index=False, engine="openpyxl")
            content = output.getvalue()
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = f"stock_par_branche_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        from fastapi.responses import Response
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur export stock par branche")
        raise HTTPException(status_code=500, detail=f"Erreur export: {exc}")

# À ajouter dans stock.py après les routes existantes

@router.get("/with-versions", response_model=List[Dict[str, Any]])
async def get_products_with_versions(
    branch_id: Optional[UUID] = Query(None, description="ID de la branche"),
    include_versions: bool = Query(True, description="Inclure les versions"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Endpoint pour récupérer les produits avec leurs versions.
    Utilisé par le sync_manager mobile pour la gestion des conflits.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien", "vendeur"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer la branche effective
        effective_branch_id = None
        if branch_id:
            effective_branch_id = branch_id
        elif current_user.active_branch_id:
            effective_branch_id = current_user.active_branch_id
        
        # Requête des produits
        query = _base_product_query(db, tenant_id, pharmacy.id, effective_branch_id)
        
        products = query.order_by(Product.name).all()
        
        # Formater la réponse avec versions
        result = []
        for product in products:
            product_dict = _serialize_product(product, include_details=False)
            product_dict["stock_version"] = getattr(product, 'stock_version', 1)
            product_dict["synced_quantity"] = getattr(product, 'synced_quantity', product.quantity or 0)
            result.append(product_dict)
        
        logger.info(f"Products with versions: {len(result)} produits retournés")
        
        return result
        
    except Exception as e:
        logger.error(f"Erreur get_products_with_versions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch/stock-update")
async def batch_stock_update(
    updates: List[Dict[str, Any]],
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Endpoint pour mettre à jour les stocks en batch.
    Utilisé par le sync_manager mobile.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        updated_count = 0
        errors = []
        
        for update in updates:
            try:
                product_id = UUID(update.get("product_id"))
                new_quantity = int(update.get("new_quantity", 0))
                sync_version = update.get("sync_version", 1)
                
                product = db.query(Product).filter(
                    Product.id == product_id,
                    Product.tenant_id == tenant_id,
                    Product.pharmacy_id == pharmacy.id
                ).first()
                
                if product:
                    old_quantity = product.quantity
                    product.quantity = new_quantity
                    product.synced_quantity = new_quantity
                    product.stock_version = sync_version
                    product.last_sync_at = datetime.utcnow()
                    product.refresh_statuses()
                    
                    updated_count += 1
                else:
                    errors.append({
                        "product_id": str(product_id),
                        "error": "Produit non trouvé"
                    })
                    
            except Exception as e:
                errors.append({
                    "product_id": update.get("product_id"),
                    "error": str(e)
                })
        
        db.commit()
        
        return {
            "success": True,
            "updated_count": updated_count,
            "errors": errors
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur batch_stock_update: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "updated_count": 0,
            "errors": [{"error": str(e)}]
        }

@router.post("/reports/stock-pdf", summary="Générer un rapport PDF du stock")
async def generate_stock_pdf(
    report_data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Génère un rapport PDF du stock par catégorie.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        from fastapi.responses import Response
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm, mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import io
        from datetime import datetime
        
        # Données du rapport
        branch = report_data.get("branch", {})
        user_info = report_data.get("user", {})
        categories = report_data.get("categories", [])
        uncategorized = report_data.get("uncategorized", {})
        global_totals = report_data.get("globalTotals", {})
        pharmacy_id = report_data.get("pharmacy_id")
        branch_id = report_data.get("branch_id")
        
        # Nom de la branche
        branch_name = branch.get("name", "Toutes les branches")
        if branch_id:
            # Récupérer le nom de la branche depuis la base
            branch_obj = db.query(Branch).filter(Branch.id == UUID(branch_id)).first()
            if branch_obj:
                branch_name = branch_obj.name
        
        # Créer le PDF en mémoire
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, 
                                rightMargin=72, leftMargin=72,
                                topMargin=72, bottomMargin=72)
        
        # Styles
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            textColor=colors.HexColor('#1a237e'),
            spaceAfter=30,
            alignment=1  # Center
        )
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=12,
            textColor=colors.HexColor('#0d47a1'),
            spaceAfter=12,
            spaceBefore=6
        )
        normal_style = styles['Normal']
        
        story = []
        
        # En-tête
        story.append(Paragraph(f"Rapport de Stock - {branch_name}", title_style))
        story.append(Paragraph(f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}", normal_style))
        story.append(Paragraph(f"Généré par: {user_info.get('name', current_user.email)}", normal_style))
        story.append(Spacer(1, 20))
        
        # Résumé global
        story.append(Paragraph("Résumé Global", heading_style))
        
        summary_data = [
            ["Total Produits", "Unités Totales", "Valeur Achat", "Valeur Vente", "Profit"],
            [
                str(global_totals.get('productCount', 0)),
                str(global_totals.get('totalQuantity', 0)),
                f"{global_totals.get('totalPurchaseValue', 0):,.0f} FC",
                f"{global_totals.get('totalSellingValue', 0):,.0f} FC",
                f"{global_totals.get('totalProfit', 0):,.0f} FC"
            ]
        ]
        
        summary_table = Table(summary_data, colWidths=[80, 80, 100, 100, 100])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a237e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 20))
        
        # Alertes de stock
        story.append(Paragraph("Alertes de Stock", heading_style))
        
        # Récupérer les alertes réelles
        inventory_service = get_inventory_service(db, current_tenant.id if current_tenant else None)
        alerts = inventory_service.get_stock_alerts(pharmacy_id) if pharmacy_id else {"critical_alerts": [], "warning_alerts": []}
        
        if alerts.get("critical_alerts") or alerts.get("warning_alerts"):
            alert_data = [["Type", "Produit", "Code", "Message"]]
            for alert in alerts.get("critical_alerts", [])[:10]:
                alert_data.append([
                    "⚠️ Rupture",
                    alert.get("name", ""),
                    alert.get("code", ""),
                    alert.get("message", "")
                ])
            for alert in alerts.get("warning_alerts", [])[:10]:
                alert_data.append([
                    "⚠️ Stock faible",
                    alert.get("name", ""),
                    alert.get("code", ""),
                    alert.get("message", "")
                ])
            
            alert_table = Table(alert_data, colWidths=[60, 150, 80, 200])
            alert_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e65100')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.lightgrey),
            ]))
            story.append(alert_table)
        else:
            story.append(Paragraph("Aucune alerte de stock actuellement.", normal_style))
        
        story.append(Spacer(1, 20))
        
        # Produits par catégorie
        story.append(Paragraph("Détail par Catégorie", heading_style))
        
        all_categories = list(categories) + ([uncategorized] if uncategorized.get("products") else [])
        
        for category in all_categories:
            category_name = category.get("categoryName", "Sans catégorie")
            products = category.get("products", [])
            
            if not products:
                continue
            
            story.append(Paragraph(f"<b>{category_name}</b>", heading_style))
            
            # Données des produits
            product_data = [
                ["Code", "Produit", "Qté", "Prix Achat", "Prix Vente", "Val Achat", "Val Vente", "Profit"]
            ]
            
            for product in products[:20]:  # Limiter à 20 produits par catégorie
                quantity = product.get("quantity", 0)
                purchase_price = product.get("purchase_price", 0)
                selling_price = product.get("selling_price", 0)
                purchase_value = quantity * purchase_price
                selling_value = quantity * selling_price
                profit = selling_value - purchase_value
                
                product_data.append([
                    product.get("code", "")[:15],
                    product.get("name", "")[:30],
                    str(quantity),
                    f"{purchase_price:,.0f}",
                    f"{selling_price:,.0f}",
                    f"{purchase_value:,.0f}",
                    f"{selling_value:,.0f}",
                    f"{profit:,.0f}"
                ])
            
            # Total de la catégorie
            product_data.append([
                "", "", "",
                f"<b>Total: {category.get('totalQuantity', 0)}</b>",
                "",
                f"<b>{category.get('totalPurchaseValue', 0):,.0f}</b>",
                f"<b>{category.get('totalSellingValue', 0):,.0f}</b>",
                f"<b>{category.get('totalProfit', 0):,.0f}</b>"
            ])
            
            category_table = Table(product_data, colWidths=[60, 120, 40, 70, 70, 80, 80, 80])
            category_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1565c0')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 8),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e3f2fd')),
                ('GRID', (0, 0), (-1, -2), 0.5, colors.lightgrey),
            ]))
            story.append(category_table)
            story.append(Spacer(1, 15))
        
        # Pied de page
        story.append(Spacer(1, 30))
        story.append(Paragraph(
            f"Rapport généré automatiquement - Pharmacie {branch_name} - {datetime.now().strftime('%Y')}",
            ParagraphStyle('Footer', parent=normal_style, fontSize=8, textColor=colors.grey, alignment=1)
        ))
        
        # Générer le PDF
        doc.build(story)
        buffer.seek(0)
        
        return Response(
            content=buffer.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=rapport_stock_{datetime.now().strftime('%Y%m%d')}.pdf"}
        )
        
    except HTTPException:
        raise
    except ImportError as e:
        logger.error(f"Erreur import reportlab: {e}")
        raise HTTPException(
            status_code=500,
            detail="La bibliothèque reportlab n'est pas installée. Exécutez: pip install reportlab"
        )
    except Exception as exc:
        logger.exception("Erreur génération PDF")
        raise HTTPException(status_code=500, detail=f"Erreur génération PDF: {exc}")

@router.get("/by-category", summary="Récupérer les produits groupés par catégorie")
async def get_products_grouped_by_category(
    branch_id: Optional[UUID] = Query(None, description="ID de la branche (optionnel)"),
    include_empty_categories: bool = Query(False, description="Inclure les catégories sans produit"),
    limit_per_category: int = Query(50, ge=1, le=500, description="Nombre max de produits par catégorie"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère tous les produits groupés par leur catégorie.
    Retourne une structure avec pour chaque catégorie la liste de ses produits.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien", "vendeur"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer la branche
        is_admin = current_user.role in ["super_admin", "superadmin", "admin", "gerant"]
        effective_branch_id = None
        
        if branch_id:
            effective_branch_id = branch_id
        elif not is_admin:
            effective_branch_id = current_user.active_branch_id
        else:
            effective_branch_id = current_branch.id if current_branch else None
        
        # Requête de base
        query = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.pharmacy_id == pharmacy.id,
            Product.is_active == True
        )
        
        if effective_branch_id:
            query = query.filter(Product.branch_id == effective_branch_id)
        
        # Récupérer tous les produits avec leur catégorie
        products = query.order_by(Product.name).all()
        
        # Récupérer toutes les catégories actives
        categories_query = db.query(Category).filter(
            Category.tenant_id == tenant_id,
            Category.is_active == True
        ).order_by(Category.name)
        
        categories = categories_query.all()
        
        # Créer un mapping catégorie ID -> nom
        category_map = {str(c.id): c.name for c in categories}
        category_map[None] = "Sans catégorie"
        
        # Grouper les produits par catégorie
        products_by_category = {}
        
        # Initialiser avec toutes les catégories si demandé
        if include_empty_categories:
            for cat in categories:
                products_by_category[str(cat.id)] = {
                    "category_id": str(cat.id),
                    "category_name": cat.name,
                    "parent_id": str(cat.parent_id) if cat.parent_id else None,
                    "product_count": 0,
                    "products": []
                }
            products_by_category[None] = {
                "category_id": None,
                "category_name": "Sans catégorie",
                "parent_id": None,
                "product_count": 0,
                "products": []
            }
        
        # Remplir avec les produits
        for product in products:
            cat_id = str(product.category_id) if product.category_id else None
            
            if cat_id not in products_by_category:
                # Récupérer le nom de la catégorie
                cat_name = category_map.get(cat_id, "Sans catégorie")
                cat_parent = None
                if cat_id and cat_id in [str(c.id) for c in categories]:
                    cat = next((c for c in categories if str(c.id) == cat_id), None)
                    cat_parent = str(cat.parent_id) if cat and cat.parent_id else None
                
                products_by_category[cat_id] = {
                    "category_id": cat_id,
                    "category_name": cat_name,
                    "parent_id": cat_parent,
                    "product_count": 0,
                    "products": []
                }
            
            # Ajouter le produit
            product_dict = _serialize_product(product, include_details=False)
            
            # Limiter le nombre de produits par catégorie
            if len(products_by_category[cat_id]["products"]) < limit_per_category:
                products_by_category[cat_id]["products"].append(product_dict)
            
            products_by_category[cat_id]["product_count"] += 1
        
        # Convertir en liste ordonnée
        result = []
        
        # D'abord les catégories avec ID
        for cat_id, data in products_by_category.items():
            if cat_id is not None:
                result.append(data)
        
        # Ensuite "Sans catégorie" à la fin
        if None in products_by_category:
            result.append(products_by_category[None])
        
        # Statistiques globales
        total_products = len(products)
        total_categories = len([c for c in result if c["category_id"] is not None])
        
        return {
            "pharmacy": {
                "id": str(pharmacy.id),
                "name": pharmacy.name
            },
            "branch": {
                "id": str(effective_branch_id) if effective_branch_id else None,
                "name": current_branch.name if current_branch else "Toutes les branches"
            } if effective_branch_id else None,
            "total_products": total_products,
            "total_categories": total_categories,
            "categories": result
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur récupération produits par catégorie")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/by-category/{category_name}", summary="Produits d'une catégorie spécifique")
async def get_products_by_category(
    category_name: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    branch_id: Optional[UUID] = Query(None, description="ID de la branche"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère tous les produits d'une catégorie spécifique.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien", "vendeur"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer la branche
        effective_branch_id = branch_id or current_user.active_branch_id
        
        # Normaliser le nom de la catégorie
        # Mettre la première lettre en majuscule, le reste en minuscule
        normalized_category = category_name.capitalize()
        
        # Requête
        query = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.pharmacy_id == pharmacy.id,
            Product.branch_id == effective_branch_id,
            Product.category == normalized_category,
            Product.is_active == True
        )
        
        total = query.count()
        products = query.order_by(Product.name).offset(skip).limit(limit).all()
        
        # Statistiques de la catégorie
        total_quantity = sum(p.quantity or 0 for p in products)
        total_value = sum((p.selling_price or 0) * (p.quantity or 0) for p in products)
        
        return {
            "category": normalized_category,
            "branch_id": str(effective_branch_id) if effective_branch_id else None,
            "total_products": total,
            "total_quantity": total_quantity,
            "total_value": float(total_value),
            "skip": skip,
            "limit": limit,
            "products": [_serialize_product(p, include_details=False) for p in products]
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Erreur récupération produits de la catégorie {category_name}")
        raise HTTPException(status_code=500, detail=f"Erreur: {exc}")
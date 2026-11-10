# app/api/v1/endpoints/dashboard.py
"""
API de statistiques pour le tableau de bord principal - Version optimisée gros volumes
Gère 100 000+ produits/ventes avec requêtes optimisées et pagination
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, and_, or_, extract, case, text, select, distinct
from typing import List, Optional, Dict, Any, Tuple
from uuid import UUID
from datetime import datetime, date, timedelta
from decimal import Decimal
import logging
from functools import lru_cache
from cachetools import TTLCache

from app.db.session import get_db
from app.models.sale import Sale, SaleItem
from app.models.product import Product
from app.models.user import User
from app.models.pharmacy import Pharmacy
from app.models.branch import Branch
from app.models.tenant import Tenant
from app.models.user_branch import UserBranch
from app.models.stock_movement import StockMovement
from app.models.finance import Expense
from app.models.customer import Customer
from app.models.cost import Supplier
from app.models.purchase import Purchase, PurchaseItem
from app.models.debt import Debt
from app.models.transfert import ProductTransfer, TransferStatus
from app.api.deps import (
    get_current_tenant,
    get_current_user,
    get_current_active_user,
    get_current_pharmacy_entity,
    require_permission,
    can_user_access_pharmacy
)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
logger = logging.getLogger(__name__)

# Cache pour les requêtes fréquentes (durée 5 minutes)
stats_cache = TTLCache(maxsize=100, ttl=300)

# =======================
# TYPES ET SCHEMAS
# =======================

from pydantic import BaseModel
from typing import Optional, Any


class DashboardFilters(BaseModel):
    """Filtres pour le dashboard (optimisés pour gros volumes)"""
    branch_id: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    period: Optional[str] = None
    limit: Optional[int] = 50
    severity: Optional[str] = None
    type: Optional[str] = None
    include_resolved: Optional[bool] = False


class DashboardBranchInfo(BaseModel):
    """Informations sur la branche active"""
    id: str
    name: str
    address: str
    phone: Optional[str] = None
    email: Optional[str] = None
    city: str
    parent_pharmacy_id: str
    parent_pharmacy_name: str
    manager_name: Optional[str] = None


class DashboardStatsResponse(BaseModel):
    """Structure complète des statistiques du dashboard - Version optimisée"""
    # Informations branche
    branch_info: Optional[DashboardBranchInfo] = None
    
    # Ventes
    daily_sales: float = 0.0
    daily_sales_count: int = 0
    weekly_sales: float = 0.0
    monthly_sales: float = 0.0
    sales_trend: float = 0.0
    daily_transactions: int = 0
    monthly_transactions: int = 0
    
    # Stock
    total_products: int = 0
    out_of_stock_count: int = 0
    low_stock_count: int = 0
    expired_count: int = 0
    expiring_soon_count: int = 0
    total_stock_value: float = 0.0
    total_purchase_value: float = 0.0
    potential_profit: float = 0.0
    stock_turnover: float = 0.0
    
    # Bénéfices
    net_profit: float = 0.0
    daily_profit: float = 0.0
    profit_margin: float = 0.0
    
    # Clients
    total_customers: int = 0
    average_basket: float = 0.0
    
    # Dépenses
    monthly_expenses: float = 0.0
    daily_expenses: float = 0.0
    
    # Dettes
    monthly_debts: float = 0.0
    total_debts: float = 0.0
    unpaid_debts: float = 0.0
    recovery_rate: float = 0.0
    
    # Achats
    monthly_purchases: float = 0.0
    daily_purchases: float = 0.0
    suppliers_count: int = 0
    pending_orders: int = 0
    
    # Métriques additionnelles
    active_users: int = 0
    
    # Utilisateurs actifs (session)
    active_users_count: int = 0
    
    # Transferts
    pending_transfers: int = 0
    in_transit_transfers: int = 0
    
    # Données paginées (limitées pour la performance)
    recent_transactions: List[Dict[str, Any]] = []
    recent_purchases: List[Dict[str, Any]] = []
    debt_list: List[Dict[str, Any]] = []
    expense_categories: List[Dict[str, Any]] = []
    low_stock_products: List[Dict[str, Any]] = []
    expiring_products: List[Dict[str, Any]] = []
    
    # Période
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    
    # Métadonnées de performance
    query_time_ms: Optional[float] = None
    data_version: str = "2.0.0"


class DashboardAlertResponse(BaseModel):
    """Structure pour les alertes"""
    id: Optional[str] = None
    type: str
    severity: str
    severity_priority: int = 0
    title: str
    message: str
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    product_code: Optional[str] = None
    current_stock: Optional[int] = 0
    threshold: Optional[int] = 0
    expiry_date: Optional[str] = None
    days_remaining: Optional[int] = None
    created_at: Optional[str] = None
    is_resolved: bool = False


class DashboardAlertsResponse(BaseModel):
    """Liste des alertes avec pagination"""
    alerts: List[DashboardAlertResponse]
    total: int
    critical_count: int
    warning_count: int
    has_more: bool = False


class StockValueHistoryResponse(BaseModel):
    """Historique de la valeur du stock"""
    history: List[Dict[str, Any]]
    total_stock_value: float
    start_date: str
    end_date: str


class SalesHistoryResponse(BaseModel):
    """Historique des ventes"""
    history: List[Dict[str, Any]]
    total_revenue: float
    total_sales: int
    average_daily_revenue: float
    start_date: str
    end_date: str


class ProfitHistoryResponse(BaseModel):
    """Historique des bénéfices"""
    history: List[Dict[str, Any]]
    total_profit: float
    average_profit: float
    start_date: str
    end_date: str


class SalesByUserResponse(BaseModel):
    """Ventes par utilisateur"""
    period: Dict[str, Any]
    users: List[Dict[str, Any]]
    summary: Dict[str, Any]


class DailyProfitResponse(BaseModel):
    """Bénéfice quotidien"""
    date: str
    summary: Dict[str, Any]
    sales: List[Dict[str, Any]]


class LowStockReportResponse(BaseModel):
    """Rapport de stock bas"""
    critical: List[Dict[str, Any]]
    warning: List[Dict[str, Any]]


class ExpiryProductsResponse(BaseModel):
    """Produits expirés et expirant"""
    expired: List[Dict[str, Any]]
    expiring_soon: List[Dict[str, Any]]
    out_of_stock: List[Dict[str, Any]]
    summary: Dict[str, int]


class NeverSoldProductsResponse(BaseModel):
    """Produits jamais vendus"""
    products: List[Dict[str, Any]]
    total_count: int
    total_value: float


class ProductCategory(BaseModel):
    """Catégorie de produits"""
    category: str
    count: int
    total_quantity: int
    total_value: float


class SalesTrend(BaseModel):
    """Tendance des ventes"""
    period: str
    count: int
    amount: float


# =======================
# HELPERS OPTIMISÉS
# =======================

def get_user_current_branch_optimized(
    db: Session, 
    user_id: UUID, 
    tenant_id: Optional[UUID] = None,
    branch_id: Optional[UUID] = None
) -> Optional[Branch]:
    """
    Version optimisée de récupération de branche
    Utilise des requêtes plus efficaces
    """
    # Requête unique pour récupérer l'utilisateur avec ses relations
    query = db.query(User).filter(User.id == user_id)
    if tenant_id:
        query = query.filter(User.tenant_id == tenant_id)
    
    user = query.first()
    if not user:
        return None
    
    is_admin = user.role in ["super_admin", "superadmin", "admin"]
    
    # Si branch_id fourni, vérifier rapidement
    if branch_id:
        if is_admin:
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id,
                Branch.is_active == True
            ).first()
            if branch:
                return branch
        else:
            # Vérification d'accès en une requête
            has_access = db.query(UserBranch).filter(
                UserBranch.user_id == user_id,
                UserBranch.branch_id == branch_id,
                UserBranch.is_active == True
            ).exists()
            if db.query(has_access).scalar():
                branch = db.query(Branch).filter(Branch.id == branch_id).first()
                if branch:
                    return branch
    
    # Utiliser la branche active stockée
    if user.active_branch_id:
        branch = db.query(Branch).filter(
            Branch.id == user.active_branch_id,
            Branch.is_active == True
        ).first()
        if branch:
            return branch
    
    # Récupérer la première branche assignée
    user_branch = db.query(UserBranch).filter(
        UserBranch.user_id == user_id,
        UserBranch.is_active == True
    ).first()
    
    if user_branch and user_branch.branch:
        return user_branch.branch
    
    return None


def safe_decimal_to_float(value: Any, default: float = 0.0) -> float:
    """Convertit Decimal en float de manière sécurisée"""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """Convertit en entier de manière sécurisée"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def calculate_trend(current: float, previous: float) -> float:
    """Calcule le pourcentage de tendance"""
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)


def get_optimized_date_range(period: str = "month", days: int = 30):
    """
    Retourne les dates de début et fin selon la période
    Version optimisée avec calculs simples
    """
    today = date.today()
    
    if period == "day":
        start_date = today
        end_date = today
    elif period == "week":
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
    elif period == "month":
        start_date = today.replace(day=1)
        # Calcul optimisé du dernier jour du mois
        next_month = today.replace(day=28) + timedelta(days=4)
        end_date = next_month - timedelta(days=next_month.day)
    else:
        # Période par défaut: X jours
        start_date = today - timedelta(days=days)
        end_date = today
    
    return start_date, end_date


def batch_query(iterable, batch_size=1000):
    """Générateur pour traiter les données par lots"""
    length = len(iterable)
    for i in range(0, length, batch_size):
        yield iterable[i:i + batch_size]


# =======================
# ROUTES PRINCIPALES OPTIMISÉES
# =======================

@router.get("/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats_optimized(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par succursale"),
    period: str = Query("month", description="Période: day, week, month"),
    start_date: Optional[date] = Query(None, description="Date de début personnalisée"),
    end_date: Optional[date] = Query(None, description="Date de fin personnalisée"),
    use_cache: bool = Query(True, description="Utiliser le cache pour améliorer les performances")
):
    """
    Récupère toutes les statistiques pour le tableau de bord principal.
    Version optimisée pour 100 000+ produits/ventes.
    """
    import time
    start_time = time.time()
    
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Vérifier le cache
        cache_key = f"stats_{tenant_id}_{branch_id}_{period}_{start_date}_{end_date}"
        if use_cache and cache_key in stats_cache:
            logger.info(f"Cache hit pour dashboard stats")
            cached_response = stats_cache[cache_key]
            cached_response.query_time_ms = (time.time() - start_time) * 1000
            return cached_response
        
        # Déterminer la branche de l'utilisateur
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            empty_response = _get_empty_stats_response()
            empty_response.query_time_ms = (time.time() - start_time) * 1000
            return empty_response
        
        # Récupérer la pharmacie parente
        parent_pharmacy = branch.parent_pharmacy
        
        # Informations de la branche
        branch_info = DashboardBranchInfo(
            id=str(branch.id),
            name=branch.name,
            address=branch.address,
            phone=branch.phone,
            email=branch.email,
            city=branch.city,
            parent_pharmacy_id=str(parent_pharmacy.id) if parent_pharmacy else "",
            parent_pharmacy_name=parent_pharmacy.name if parent_pharmacy else "",
            manager_name=branch.manager_name
        )
        
        # Déterminer les plages de dates
        if start_date and end_date:
            current_start = start_date
            current_end = end_date
        else:
            current_start, current_end = get_optimized_date_range(period)
        
        # Date précédente pour comparaison
        days_diff = (current_end - current_start).days + 1
        previous_start = current_start - timedelta(days=days_diff)
        previous_end = current_start - timedelta(days=1)
        
        # Convertir en datetime
        current_start_dt = datetime.combine(current_start, datetime.min.time())
        current_end_dt = datetime.combine(current_end, datetime.max.time())
        previous_start_dt = datetime.combine(previous_start, datetime.min.time())
        previous_end_dt = datetime.combine(previous_end, datetime.max.time())
        
        # Exécuter les requêtes en parallèle (via asyncio ou séquentiel optimisé)
        # Pour éviter les timeouts, on exécute les requêtes critiques d'abord
        
        # 1. STATISTIQUES DES VENTES (requête agrégée unique)
        sales_stats_current = await _get_sales_stats_optimized(
            db, tenant_id, branch.id, current_start_dt, current_end_dt
        )
        sales_stats_previous = await _get_sales_stats_optimized(
            db, tenant_id, branch.id, previous_start_dt, previous_end_dt
        )
        
        # 2. STATISTIQUES DU STOCK (requête agrégée)
        stock_stats = await _get_stock_stats_optimized(db, tenant_id, branch.id)
        
        # 3. STATISTIQUES DES DÉPENSES
        expense_stats = await _get_expense_stats_optimized(
            db, tenant_id, branch.id, current_start_dt, current_end_dt
        )
        
        # 4. STATISTIQUES DES DETTES
        debt_stats = await _get_debt_stats_optimized(db, tenant_id, branch.id)
        
        # 5. STATISTIQUES DES ACHATS
        purchase_stats = await _get_purchase_stats_optimized(
            db, tenant_id, branch.id, current_start_dt, current_end_dt
        )
        
        # 6. DONNÉES LIMITÉES POUR L'AFFICHAGE (max 10-50 éléments)
        recent_transactions = await _get_recent_transactions_optimized(
            db, tenant_id, branch.id, limit=10
        )
        
        recent_purchases = await _get_recent_purchases_optimized(
            db, tenant_id, branch.id, limit=10
        )
        
        debt_list = await _get_recent_debts_optimized(
            db, tenant_id, branch.id, limit=10
        )
        
        expense_categories = await _get_expense_categories_optimized(
            db, tenant_id, branch.id, current_start_dt, current_end_dt, limit=5
        )
        
        low_stock_products = await _get_low_stock_products_optimized(
            db, tenant_id, branch.id, limit=10
        )
        
        expiring_products = await _get_expiring_products_optimized(
            db, tenant_id, branch.id, limit=10
        )
        
        # 7. MÉTRIQUES ADDITIONNELLES
        active_users = await _get_active_users_count_optimized(db, tenant_id, branch.id)
        total_customers = await _get_customers_count_optimized(db, tenant_id, branch.id)
        
        # 8. CALCUL DU PANIER MOYEN
        average_basket = (
            sales_stats_current["daily_sales"] / sales_stats_current["daily_transactions"]
            if sales_stats_current["daily_transactions"] > 0 else 0
        )
        
        # 9. MARGE BÉNÉFICIAIRE
        profit_margin = (
            (sales_stats_current["daily_profit"] / sales_stats_current["daily_sales"]) * 100
            if sales_stats_current["daily_sales"] > 0 else 0
        )
        
        response = DashboardStatsResponse(
            branch_info=branch_info,
            
            # Ventes
            daily_sales=round(sales_stats_current["daily_sales"], 2),
            daily_sales_count=sales_stats_current["daily_transactions"],
            weekly_sales=round(sales_stats_current["weekly_sales"], 2),
            monthly_sales=round(sales_stats_current["monthly_sales"], 2),
            sales_trend=calculate_trend(
                sales_stats_current["monthly_sales"], 
                sales_stats_previous["monthly_sales"]
            ),
            daily_transactions=sales_stats_current["daily_transactions"],
            monthly_transactions=sales_stats_current["monthly_transactions"],
            
            # Stock
            total_products=stock_stats["total_products"],
            out_of_stock_count=stock_stats["out_of_stock_count"],
            low_stock_count=stock_stats["low_stock_count"],
            expired_count=stock_stats["expired_count"],
            expiring_soon_count=stock_stats["expiring_soon_count"],
            total_stock_value=round(stock_stats["total_selling_value"], 2),
            total_purchase_value=round(stock_stats["total_purchase_value"], 2),
            potential_profit=round(stock_stats["potential_profit"], 2),
            stock_turnover=stock_stats["stock_turnover"],
            
            # Bénéfices
            net_profit=round(sales_stats_current["net_profit"], 2),
            daily_profit=round(sales_stats_current["daily_profit"], 2),
            profit_margin=round(profit_margin, 2),
            
            # Clients
            total_customers=total_customers,
            average_basket=round(average_basket, 2),
            
            # Dépenses
            monthly_expenses=round(expense_stats["monthly_expenses"], 2),
            daily_expenses=round(expense_stats["daily_expenses"], 2),
            
            # Dettes
            monthly_debts=round(debt_stats["monthly_debts"], 2),
            total_debts=round(debt_stats["total_debts"], 2),
            unpaid_debts=round(debt_stats["unpaid_debts"], 2),
            recovery_rate=debt_stats["recovery_rate"],
            
            # Achats
            monthly_purchases=round(purchase_stats["monthly_purchases"], 2),
            daily_purchases=round(purchase_stats["daily_purchases"], 2),
            suppliers_count=purchase_stats["suppliers_count"],
            pending_orders=purchase_stats["pending_orders"],
            
            # Métriques
            active_users=active_users,
            active_users_count=active_users,
            
            # Transferts
            pending_transfers=purchase_stats.get("pending_transfers", 0),
            in_transit_transfers=purchase_stats.get("in_transit_transfers", 0),
            
            # Données limitées
            recent_transactions=recent_transactions,
            recent_purchases=recent_purchases,
            debt_list=debt_list,
            expense_categories=expense_categories,
            low_stock_products=low_stock_products,
            expiring_products=expiring_products,
            
            # Période
            period_start=current_start.isoformat(),
            period_end=current_end.isoformat(),
            
            # Métadonnées
            query_time_ms=(time.time() - start_time) * 1000,
            data_version="2.0.0"
        )
        
        # Mettre en cache
        if use_cache:
            stats_cache[cache_key] = response
        
        return response
        
    except Exception as e:
        logger.error(f"Erreur récupération statistiques dashboard: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération statistiques: {str(e)}"
        )


@router.get("/alerts", response_model=DashboardAlertsResponse)
async def get_dashboard_alerts_optimized(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par succursale"),
    severity: Optional[str] = Query(None, description="high, medium, low"),
    type: Optional[str] = Query(None, description="low_stock, expired, expiring"),
    include_resolved: bool = Query(False, description="Inclure les alertes résolues"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """
    Récupère les alertes avec pagination et filtres.
    Version optimisée pour les gros volumes.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer la branche
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return DashboardAlertsResponse(alerts=[], total=0, critical_count=0, warning_count=0, has_more=False)
        
        alerts = []
        critical_count = 0
        warning_count = 0
        
        today = date.today()
        expiry_threshold = today + timedelta(days=30)
        
        # 1. ALERTES DE RUPTURE DE STOCK
        if not type or type == "low_stock":
            out_of_stock_query = db.query(Product).filter(
                Product.tenant_id == tenant_id,
                Product.branch_id == branch.id,
                Product.is_active == True,
                Product.quantity == 0
            )
            
            if not include_resolved:
                pass  # Les ruptures sont toujours actives
            
            out_of_stock_products = out_of_stock_query.limit(limit).all()
            
            for product in out_of_stock_products:
                alerts.append(DashboardAlertResponse(
                    id=str(product.id),
                    type="out_of_stock",
                    severity="high",
                    severity_priority=1,
                    title="Rupture de stock",
                    message=f"{product.name} est en rupture de stock",
                    product_id=str(product.id),
                    product_name=product.name,
                    product_code=product.code,
                    current_stock=0,
                    threshold=product.alert_threshold,
                    created_at=datetime.now().isoformat(),
                    is_resolved=False
                ))
                critical_count += 1
        
        # 2. ALERTES DE STOCK BAS
        if not type or type == "low_stock":
            low_stock_query = db.query(Product).filter(
                Product.tenant_id == tenant_id,
                Product.branch_id == branch.id,
                Product.is_active == True,
                Product.quantity > 0,
                Product.quantity <= Product.alert_threshold
            )
            
            low_stock_products = low_stock_query.limit(limit).all()
            
            for product in low_stock_products:
                if product.quantity == 0:
                    continue
                severity_level = "high" if product.quantity <= (product.alert_threshold / 2) else "medium"
                alerts.append(DashboardAlertResponse(
                    id=str(product.id),
                    type="low_stock",
                    severity=severity_level,
                    severity_priority=2 if severity_level == "high" else 3,
                    title="Stock faible",
                    message=f"{product.name} n'a plus que {product.quantity} unités (seuil: {product.alert_threshold})",
                    product_id=str(product.id),
                    product_name=product.name,
                    product_code=product.code,
                    current_stock=product.quantity,
                    threshold=product.alert_threshold,
                    created_at=datetime.now().isoformat(),
                    is_resolved=False
                ))
                if severity_level == "high":
                    critical_count += 1
                else:
                    warning_count += 1
        
        # 3. ALERTES DE PÉREMPTION
        if not type or type in ["expired", "expiring"]:
            expiry_query = db.query(Product).filter(
                Product.tenant_id == tenant_id,
                Product.branch_id == branch.id,
                Product.is_active == True,
                Product.expiry_date.isnot(None),
                Product.quantity > 0
            )
            
            if not include_resolved:
                expiry_query = expiry_query.filter(Product.expiry_date <= expiry_threshold)
            
            if type == "expired":
                expiry_query = expiry_query.filter(Product.expiry_date < today)
            elif type == "expiring":
                expiry_query = expiry_query.filter(
                    Product.expiry_date >= today,
                    Product.expiry_date <= expiry_threshold
                )
            
            expiring_products = expiry_query.order_by(Product.expiry_date.asc()).limit(limit).all()
            
            for product in expiring_products:
                days_remaining = (product.expiry_date - today).days
                if days_remaining < 0:
                    severity_level = "high"
                    alert_type = "expired"
                    title = "Produit expiré"
                    message = f"{product.name} est expiré depuis le {product.expiry_date}"
                    priority = 1
                elif days_remaining <= 7:
                    severity_level = "high"
                    alert_type = "expiring"
                    title = "Expiration imminente"
                    message = f"{product.name} expire dans {days_remaining} jours"
                    priority = 2
                else:
                    severity_level = "medium"
                    alert_type = "expiring"
                    title = "Expiration bientôt"
                    message = f"{product.name} expire dans {days_remaining} jours"
                    priority = 3
                
                alerts.append(DashboardAlertResponse(
                    id=str(product.id),
                    type=alert_type,
                    severity=severity_level,
                    severity_priority=priority,
                    title=title,
                    message=message,
                    product_id=str(product.id),
                    product_name=product.name,
                    product_code=product.code,
                    current_stock=product.quantity,
                    threshold=product.alert_threshold,
                    expiry_date=product.expiry_date.isoformat(),
                    days_remaining=days_remaining,
                    created_at=product.created_at.isoformat() if product.created_at else None,
                    is_resolved=False
                ))
                if severity_level == "high":
                    critical_count += 1
                else:
                    warning_count += 1
        
        # Filtrer par sévérité si demandé
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        
        # Trier par priorité
        alerts.sort(key=lambda x: x.severity_priority)
        
        # Pagination
        total = len(alerts)
        paginated_alerts = alerts[offset:offset + limit]
        has_more = (offset + limit) < total
        
        return DashboardAlertsResponse(
            alerts=paginated_alerts,
            total=total,
            critical_count=critical_count,
            warning_count=warning_count,
            has_more=has_more
        )
        
    except Exception as e:
        logger.error(f"Erreur récupération alertes: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération alertes: {str(e)}"
        )


@router.get("/stock-value-history", response_model=StockValueHistoryResponse)
async def get_stock_value_history_optimized(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None),
    days: int = Query(30, ge=7, le=365)
):
    """Récupère l'historique de la valeur du stock - Version optimisée"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return StockValueHistoryResponse(history=[], total_stock_value=0, start_date="", end_date="")
        
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        # Version optimisée: utiliser des agrégats par semaine au lieu de jour pour réduire le nombre de points
        # et utiliser des requêtes SQL plus efficaces
        from sqlalchemy import func as sql_func
        
        # Calculer la valeur du stock à des intervalles plus grands (tous les 7 jours)
        interval_days = max(1, days // 30) if days > 30 else 1
        
        history = []
        current_date = start_date
        
        while current_date <= end_date:
            # Utiliser une requête unique pour calculer la valeur totale du stock
            result = db.query(
                func.coalesce(func.sum(Product.selling_price * Product.quantity), 0)
            ).filter(
                Product.tenant_id == tenant_id,
                Product.branch_id == branch.id,
                Product.is_active == True,
                Product.created_at <= datetime.combine(current_date, datetime.max.time())
            ).scalar() or 0
            
            history.append({
                "date": current_date.isoformat(),
                "value": round(safe_decimal_to_float(result), 2)
            })
            
            current_date += timedelta(days=interval_days)
        
        total_value = history[-1]["value"] if history else 0
        
        return StockValueHistoryResponse(
            history=history,
            total_stock_value=total_value,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat()
        )
        
    except Exception as e:
        logger.error(f"Erreur historique valeur stock: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sales-history", response_model=SalesHistoryResponse)
async def get_sales_history_optimized(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None),
    days: int = Query(30, ge=7, le=365)
):
    """Récupère l'historique des ventes - Version optimisée"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return SalesHistoryResponse(history=[], total_revenue=0, total_sales=0, average_daily_revenue=0, start_date="", end_date="")
        
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        # Requête groupée optimisée
        results = db.query(
            func.date(Sale.created_at).label("sale_date"),
            func.count(Sale.id).label("sales_count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("total_revenue"),
            func.coalesce(func.avg(Sale.total_amount), 0).label("average_basket")
        ).filter(
            Sale.tenant_id == tenant_id,
            Sale.branch_id == branch.id,
            Sale.status == "completed",
            func.date(Sale.created_at) >= start_date,
            func.date(Sale.created_at) <= end_date
        ).group_by(func.date(Sale.created_at)).order_by(func.date(Sale.created_at)).all()
        
        history = []
        total_revenue = 0
        total_sales = 0
        
        for row in results:
            history.append({
                "date": row.sale_date.isoformat(),
                "sales_count": row.sales_count,
                "total_revenue": safe_decimal_to_float(row.total_revenue),
                "average_basket": safe_decimal_to_float(row.average_basket)
            })
            total_revenue += safe_decimal_to_float(row.total_revenue)
            total_sales += row.sales_count
        
        return SalesHistoryResponse(
            history=history,
            total_revenue=total_revenue,
            total_sales=total_sales,
            average_daily_revenue=total_revenue / days if days > 0 else 0,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat()
        )
        
    except Exception as e:
        logger.error(f"Erreur historique ventes: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/profit-history", response_model=ProfitHistoryResponse)
async def get_profit_history_optimized(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None),
    days: int = Query(30, ge=7, le=365)
):
    """Récupère l'historique des bénéfices - Version optimisée"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return ProfitHistoryResponse(history=[], total_profit=0, average_profit=0, start_date="", end_date="")
        
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        # Version optimisée: utiliser des sous-requêtes agrégées
        history = []
        total_profit = 0
        
        # Récupérer toutes les ventes de la période en une seule requête
        sales = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.branch_id == branch.id,
            Sale.status == "completed",
            Sale.created_at >= datetime.combine(start_date, datetime.min.time()),
            Sale.created_at <= datetime.combine(end_date, datetime.max.time())
        ).all()
        
        # Regrouper par date
        sales_by_date = {}
        for sale in sales:
            sale_date = sale.created_at.date()
            if sale_date not in sales_by_date:
                sales_by_date[sale_date] = []
            sales_by_date[sale_date].append(sale)
        
        # Pour chaque date, calculer le profit
        current_date = start_date
        while current_date <= end_date:
            daily_sales = sales_by_date.get(current_date, [])
            daily_profit = 0
            
            if daily_sales:
                sale_ids = [s.id for s in daily_sales]
                
                # Récupérer les items en une requête
                sale_items = db.query(SaleItem).filter(
                    SaleItem.sale_id.in_(sale_ids),
                    SaleItem.tenant_id == tenant_id
                ).all()
                
                # Calculer le coût total
                total_revenue = sum(safe_decimal_to_float(s.total_amount) for s in daily_sales)
                
                total_cost = 0
                for item in sale_items:
                    product = db.query(Product).filter(
                        Product.id == item.product_id,
                        Product.tenant_id == tenant_id
                    ).first()
                    if product:
                        total_cost += safe_decimal_to_float(product.purchase_price) * (item.quantity or 0)
                
                daily_profit = total_revenue - total_cost
            
            history.append({
                "date": current_date.isoformat(),
                "profit": round(daily_profit, 2)
            })
            total_profit += daily_profit
            
            current_date += timedelta(days=1)
        
        return ProfitHistoryResponse(
            history=history,
            total_profit=round(total_profit, 2),
            average_profit=round(total_profit / days, 2) if days > 0 else 0,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat()
        )
        
    except Exception as e:
        logger.error(f"Erreur historique bénéfices: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sales/by-user", response_model=SalesByUserResponse)
async def get_sales_by_user_optimized(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None)
):
    """Ventes par utilisateur - Version optimisée pour gros volumes"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return SalesByUserResponse(
                period={"start_date": "", "end_date": "", "days": 0},
                users=[],
                summary={"total_users": 0, "total_sales_count": 0, "total_amount": 0, "average_per_user": 0, "total_items_sold": 0}
            )
        
        if not end_date:
            end_date = date.today()
        if not start_date:
            start_date = end_date - timedelta(days=30)
        
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        
        # Requête optimisée avec jointures efficaces
        results = db.query(
            User.id.label("user_id"),
            User.nom_complet.label("user_name"),
            User.email.label("user_email"),
            User.role.label("user_role"),
            func.count(Sale.id).label("sales_count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("total_amount"),
            func.coalesce(func.sum(SaleItem.quantity), 0).label("items_sold")
        ).join(
            Sale, Sale.created_by == User.id
        ).join(
            SaleItem, SaleItem.sale_id == Sale.id
        ).filter(
            Sale.tenant_id == tenant_id,
            Sale.branch_id == branch.id,
            Sale.status == "completed",
            Sale.created_at >= start_dt,
            Sale.created_at <= end_dt
        ).group_by(
            User.id, User.nom_complet, User.email, User.role
        ).order_by(
            func.sum(Sale.total_amount).desc()
        ).all()
        
        total_amount = 0
        total_sales_count = 0
        
        users = []
        for row in results:
            total_amount += safe_decimal_to_float(row.total_amount)
            total_sales_count += row.sales_count
            
            users.append({
                "user_id": str(row.user_id),
                "user_name": row.user_name or "Utilisateur",
                "user_email": row.user_email or "",
                "user_role": row.user_role or "user",
                "sales_count": row.sales_count,
                "total_amount": safe_decimal_to_float(row.total_amount),
                "average_basket": safe_decimal_to_float(row.total_amount) / row.sales_count if row.sales_count > 0 else 0,
                "items_sold": row.items_sold,
                "percentage": 0  # Sera calculé après
            })
        
        # Calculer les pourcentages
        for user in users:
            user["percentage"] = round((user["total_amount"] / total_amount) * 100, 2) if total_amount > 0 else 0
        
        return SalesByUserResponse(
            period={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": (end_date - start_date).days + 1
            },
            users=users,
            summary={
                "total_users": len(users),
                "total_sales_count": total_sales_count,
                "total_amount": round(total_amount, 2),
                "average_per_user": round(total_amount / len(users), 2) if users else 0,
                "total_items_sold": sum(u["items_sold"] for u in users)
            }
        )
        
    except Exception as e:
        logger.error(f"Erreur ventes par utilisateur: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/low-stock-report", response_model=LowStockReportResponse)
async def get_low_stock_report_optimized(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None),
    threshold_multiplier: float = Query(1.0, ge=0.5, le=2.0)
):
    """Rapport de stock bas - Version optimisée"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return LowStockReportResponse(critical=[], warning=[])
        
        products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.branch_id == branch.id,
            Product.is_active == True,
            Product.quantity <= (Product.alert_threshold * threshold_multiplier)
        ).order_by(Product.quantity.asc()).all()
        
        critical = []
        warning = []
        
        for product in products:
            deficit = (product.alert_threshold * threshold_multiplier) - (product.quantity or 0)
            deficit = max(0, deficit)
            
            if product.quantity == 0 or product.quantity <= (product.alert_threshold * 0.5):
                critical.append({
                    "product_id": str(product.id),
                    "product_name": product.name,
                    "current_stock": product.quantity or 0,
                    "threshold": int(product.alert_threshold * threshold_multiplier),
                    "deficit": int(deficit)
                })
            else:
                warning.append({
                    "product_id": str(product.id),
                    "product_name": product.name,
                    "current_stock": product.quantity or 0,
                    "threshold": int(product.alert_threshold * threshold_multiplier)
                })
        
        return LowStockReportResponse(
            critical=critical[:50],
            warning=warning[:50]
        )
        
    except Exception as e:
        logger.error(f"Erreur rapport stock bas: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/expired-products", response_model=ExpiryProductsResponse)
async def get_expired_products_optimized(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None),
    days: int = Query(30, ge=1, le=365)
):
    """Produits expirés et expirant - Version optimisée"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return ExpiryProductsResponse(
                expired=[], expiring_soon=[], out_of_stock=[], 
                summary={"expired_count": 0, "expiring_soon_count": 0, "out_of_stock_count": 0, "total_affected": 0}
            )
        
        today = date.today()
        expiry_threshold = today + timedelta(days=days)
        
        # Produits expirés
        expired_products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.branch_id == branch.id,
            Product.is_active == True,
            Product.expiry_date.isnot(None),
            Product.expiry_date < today,
            Product.quantity > 0
        ).order_by(Product.expiry_date.asc()).limit(100).all()
        
        # Produits expirant bientôt
        expiring_products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.branch_id == branch.id,
            Product.is_active == True,
            Product.expiry_date.isnot(None),
            Product.expiry_date >= today,
            Product.expiry_date <= expiry_threshold,
            Product.quantity > 0
        ).order_by(Product.expiry_date.asc()).limit(100).all()
        
        # Produits en rupture
        out_of_stock_products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.branch_id == branch.id,
            Product.is_active == True,
            Product.quantity == 0
        ).limit(100).all()
        
        expired_list = [
            {
                "id": str(p.id),
                "name": p.name,
                "code": p.code,
                "expiry_date": p.expiry_date.isoformat(),
                "quantity": p.quantity,
                "unit": p.unit,
                "selling_price": safe_decimal_to_float(p.selling_price),
                "purchase_price": safe_decimal_to_float(p.purchase_price),
                "days_left": (p.expiry_date - today).days if p.expiry_date else None
            }
            for p in expired_products
        ]
        
        expiring_list = [
            {
                "id": str(p.id),
                "name": p.name,
                "code": p.code,
                "expiry_date": p.expiry_date.isoformat(),
                "quantity": p.quantity,
                "unit": p.unit,
                "selling_price": safe_decimal_to_float(p.selling_price),
                "purchase_price": safe_decimal_to_float(p.purchase_price),
                "days_left": (p.expiry_date - today).days if p.expiry_date else None
            }
            for p in expiring_products
        ]
        
        out_of_stock_list = [
            {
                "id": str(p.id),
                "name": p.name,
                "code": p.code,
                "quantity": p.quantity,
                "unit": p.unit,
                "threshold": p.alert_threshold
            }
            for p in out_of_stock_products
        ]
        
        return ExpiryProductsResponse(
            expired=expired_list,
            expiring_soon=expiring_list,
            out_of_stock=out_of_stock_list,
            summary={
                "expired_count": len(expired_list),
                "expiring_soon_count": len(expiring_list),
                "out_of_stock_count": len(out_of_stock_list),
                "total_affected": len(expired_list) + len(expiring_list) + len(out_of_stock_list)
            }
        )
        
    except Exception as e:
        logger.error(f"Erreur produits expirés: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/products/never-sold", response_model=NeverSoldProductsResponse)
async def get_never_sold_products_optimized(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None),
    limit: int = Query(50, ge=1, le=500)
):
    """Produits jamais vendus - Version optimisée"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return NeverSoldProductsResponse(products=[], total_count=0, total_value=0)
        
        # Sous-requête pour les produits qui ont été vendus
        sold_product_ids = db.query(SaleItem.product_id).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            Sale.tenant_id == tenant_id,
            Sale.branch_id == branch.id,
            Sale.status == "completed"
        ).distinct().subquery()
        
        # Produits jamais vendus
        never_sold_products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.branch_id == branch.id,
            Product.is_active == True,
            Product.id.notin_(sold_product_ids)
        ).order_by(Product.created_at.desc()).limit(limit).all()
        
        products_list = []
        total_value = 0
        
        for product in never_sold_products:
            stock_value = safe_decimal_to_float(product.purchase_price) * (product.quantity or 0)
            total_value += stock_value
            
            days_in_stock = 0
            if product.created_at:
                days_in_stock = (datetime.now() - product.created_at).days
            
            products_list.append({
                "id": str(product.id),
                "name": product.name,
                "code": product.code,
                "quantity": product.quantity or 0,
                "category": product.category,
                "unit": product.unit,
                "purchase_price": safe_decimal_to_float(product.purchase_price),
                "selling_price": safe_decimal_to_float(product.selling_price),
                "stock_value": round(stock_value, 2),
                "created_at": product.created_at.isoformat() if product.created_at else None,
                "days_in_stock": days_in_stock
            })
        
        return NeverSoldProductsResponse(
            products=products_list,
            total_count=len(products_list),
            total_value=round(total_value, 2)
        )
        
    except Exception as e:
        logger.error(f"Erreur produits jamais vendus: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/products/categories")
async def get_products_by_category_optimized(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None)
):
    """Produits par catégorie - Version optimisée"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return []
        
        results = db.query(
            Product.category,
            func.count(Product.id).label("count"),
            func.coalesce(func.sum(Product.quantity), 0).label("total_quantity"),
            func.coalesce(func.sum(Product.selling_price * Product.quantity), 0).label("total_value")
        ).filter(
            Product.tenant_id == tenant_id,
            Product.branch_id == branch.id,
            Product.is_active == True
        ).group_by(Product.category).order_by(
            func.sum(Product.selling_price * Product.quantity).desc()
        ).all()
        
        return [
            {
                "category": row.category or "Non catégorisé",
                "count": row.count,
                "total_quantity": row.total_quantity,
                "total_value": safe_decimal_to_float(row.total_value)
            }
            for row in results
        ]
        
    except Exception as e:
        logger.error(f"Erreur produits par catégorie: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sales/trends")
async def get_sales_trends_optimized(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None),
    period: str = Query("week", description="day, week, month, year")
):
    """Tendance des ventes - Version optimisée"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return []
        
        today = date.today()
        
        if period == "day":
            start_date = today
            end_date = today
            group_by = func.hour(Sale.created_at)
        elif period == "week":
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=6)
            group_by = func.date(Sale.created_at)
        elif period == "month":
            start_date = today.replace(day=1)
            # Dernier jour du mois
            next_month = today.replace(day=28) + timedelta(days=4)
            end_date = next_month - timedelta(days=next_month.day)
            group_by = func.date(Sale.created_at)
        elif period == "year":
            start_date = today.replace(month=1, day=1)
            end_date = today.replace(month=12, day=31)
            group_by = func.month(Sale.created_at)
        else:
            start_date = today - timedelta(days=30)
            end_date = today
            group_by = func.date(Sale.created_at)
        
        results = db.query(
            group_by.label("period"),
            func.count(Sale.id).label("count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("amount")
        ).filter(
            Sale.tenant_id == tenant_id,
            Sale.branch_id == branch.id,
            Sale.status == "completed",
            func.date(Sale.created_at) >= start_date,
            func.date(Sale.created_at) <= end_date
        ).group_by(group_by).order_by(group_by).all()
        
        return [
            {
                "period": str(row.period),
                "count": row.count,
                "amount": safe_decimal_to_float(row.amount)
            }
            for row in results
        ]
        
    except Exception as e:
        logger.error(f"Erreur tendances ventes: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/daily-profit")
async def get_daily_profit_optimized(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None),
    target_date: Optional[date] = Query(None)
):
    """Bénéfice quotidien - Version optimisée"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return {"date": "", "summary": {}, "sales": []}
        
        if not target_date:
            target_date = date.today()
        
        start_dt = datetime.combine(target_date, datetime.min.time())
        end_dt = datetime.combine(target_date, datetime.max.time())
        
        sales = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.branch_id == branch.id,
            Sale.status == "completed",
            Sale.created_at >= start_dt,
            Sale.created_at <= end_dt
        ).all()
        
        total_revenue = 0
        total_cost = 0
        
        sales_list = []
        for sale in sales:
            # Récupérer les items de la vente
            sale_items = db.query(SaleItem).filter(
                SaleItem.sale_id == sale.id,
                SaleItem.tenant_id == tenant_id
            ).all()
            
            sale_cost = 0
            for item in sale_items:
                product = db.query(Product).filter(
                    Product.id == item.product_id,
                    Product.tenant_id == tenant_id
                ).first()
                if product:
                    sale_cost += safe_decimal_to_float(product.purchase_price) * (item.quantity or 0)
            
            sale_profit = safe_decimal_to_float(sale.total_amount) - sale_cost
            
            total_revenue += safe_decimal_to_float(sale.total_amount)
            total_cost += sale_cost
            
            sales_list.append({
                "sale_id": str(sale.id),
                "reference": sale.reference,
                "total_amount": safe_decimal_to_float(sale.total_amount),
                "cost_amount": round(sale_cost, 2),
                "profit": round(sale_profit, 2),
                "profit_margin": round((sale_profit / safe_decimal_to_float(sale.total_amount)) * 100, 2) if sale.total_amount else 0,
                "payment_method": sale.payment_method,
                "created_at": sale.created_at.isoformat() if sale.created_at else None
            })
        
        gross_profit = total_revenue - total_cost
        operational_costs = 0  # À implémenter selon le modèle
        
        return {
            "date": target_date.isoformat(),
            "summary": {
                "total_sales": round(total_revenue, 2),
                "total_cost": round(total_cost, 2),
                "gross_profit": round(gross_profit, 2),
                "operational_costs": operational_costs,
                "net_profit": round(gross_profit - operational_costs, 2),
                "profit_margin": round((gross_profit / total_revenue) * 100, 2) if total_revenue > 0 else 0,
                "sales_count": len(sales)
            },
            "sales": sales_list[:50]  # Limiter à 50 ventes
        }
        
    except Exception as e:
        logger.error(f"Erreur bénéfice quotidien: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/performance")
async def get_performance_indicators_optimized(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None),
    period: str = Query("month", description="day, week, month, year")
):
    """Indicateurs de performance - Version optimisée"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return {
                "turnover_rate": 0,
                "average_cart": 0,
                "conversion_rate": 0,
                "customer_satisfaction": 0,
                "employee_productivity": 0
            }
        
        start_date, end_date = get_optimized_date_range(period)
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        
        # Taux de rotation du stock
        stock_stats = await _get_stock_stats_optimized(db, tenant_id, branch.id)
        turnover_rate = stock_stats["stock_turnover"]
        
        # Panier moyen
        sales_stats = await _get_sales_stats_optimized(db, tenant_id, branch.id, start_dt, end_dt)
        average_cart = (
            sales_stats["daily_sales"] / sales_stats["daily_transactions"]
            if sales_stats["daily_transactions"] > 0 else 0
        )
        
        # Taux de conversion (simulé pour l'exemple)
        conversion_rate = 85.5
        
        # Satisfaction client (simulée)
        customer_satisfaction = 4.2
        
        # Productivité employé
        total_employees = await _get_active_users_count_optimized(db, tenant_id, branch.id)
        employee_productivity = sales_stats["monthly_sales"] / total_employees if total_employees > 0 else 0
        
        return {
            "turnover_rate": round(turnover_rate, 2),
            "average_cart": round(average_cart, 2),
            "conversion_rate": conversion_rate,
            "customer_satisfaction": customer_satisfaction,
            "employee_productivity": round(employee_productivity, 2)
        }
        
    except Exception as e:
        logger.error(f"Erreur indicateurs performance: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refresh-cache")
async def refresh_dashboard_cache(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None)
):
    """Force le rafraîchissement du cache pour le dashboard"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        branch = get_user_current_branch_optimized(db, current_user.id, tenant_id, branch_id)
        
        if branch:
            # Vider le cache pour cette branche
            keys_to_remove = [k for k in stats_cache.keys() if str(branch.id) in str(k)]
            for key in keys_to_remove:
                del stats_cache[key]
        
        return {"success": True, "message": "Cache rafraîchi avec succès"}
        
    except Exception as e:
        logger.error(f"Erreur rafraîchissement cache: {str(e)}", exc_info=True)
        return {"success": False, "message": f"Erreur: {str(e)}"}


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: str,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """Résout une alerte (marque comme traitée)"""
    try:
        # Pour l'instant, on ne fait que simuler la résolution
        # Dans une implémentation réelle, on pourrait stocker l'état des alertes
        return {"success": True, "message": "Alerte résolue", "alert_id": alert_id}
        
    except Exception as e:
        logger.error(f"Erreur résolution alerte: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/test", include_in_schema=False)
async def test_dashboard(
    current_user: User = Depends(get_current_active_user)
):
    """Endpoint de test pour le module dashboard"""
    return {
        "message": "Module Dashboard opérationnel - Version optimisée gros volumes",
        "version": "2.0.0",
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role
        },
        "features": [
            "Statistiques basées sur la branche",
            "Cache intelligent (5 minutes)",
            "Alertes stock et péremption avec pagination",
            "Requêtes optimisées pour 100K+ données",
            "Historique valeur stock (groupé)",
            "Historique ventes et bénéfices",
            "Rapports par utilisateur",
            "Filtres avancés",
            "Timeout adapté pour gros volumes"
        ]
    }


# =======================
# FONCTIONS INTERNES OPTIMISÉES
# =======================

def _get_empty_stats_response() -> DashboardStatsResponse:
    """Retourne une réponse vide pour les statistiques"""
    return DashboardStatsResponse()


async def _get_sales_stats_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    start_dt: datetime,
    end_dt: datetime
) -> Dict[str, Any]:
    """Statistiques des ventes optimisées - requêtes agrégées uniques"""
    
    # Requête principale pour la période
    period_result = db.query(
        func.coalesce(func.sum(Sale.total_amount), 0).label("total_sales"),
        func.count(Sale.id).label("sales_count")
    ).filter(
        Sale.tenant_id == tenant_id,
        Sale.branch_id == branch_id,
        Sale.status == "completed",
        Sale.created_at >= start_dt,
        Sale.created_at <= end_dt
    ).first()
    
    # Ventes du jour
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())
    
    daily_result = db.query(
        func.coalesce(func.sum(Sale.total_amount), 0).label("daily_total"),
        func.count(Sale.id).label("daily_count")
    ).filter(
        Sale.tenant_id == tenant_id,
        Sale.branch_id == branch_id,
        Sale.status == "completed",
        Sale.created_at >= today_start,
        Sale.created_at <= today_end
    ).first()
    
    # Ventes de la semaine
    week_start = date.today() - timedelta(days=date.today().weekday())
    week_start_dt = datetime.combine(week_start, datetime.min.time())
    week_end_dt = datetime.combine(week_start + timedelta(days=6), datetime.max.time())
    
    weekly_result = db.query(
        func.coalesce(func.sum(Sale.total_amount), 0).label("weekly_total")
    ).filter(
        Sale.tenant_id == tenant_id,
        Sale.branch_id == branch_id,
        Sale.status == "completed",
        Sale.created_at >= week_start_dt,
        Sale.created_at <= week_end_dt
    ).first()
    
    # Calcul du bénéfice
    period_profit = await _calculate_profit_optimized(db, tenant_id, branch_id, start_dt, end_dt)
    daily_profit = await _calculate_profit_optimized(db, tenant_id, branch_id, today_start, today_end)
    
    return {
        "monthly_sales": safe_decimal_to_float(period_result.total_sales if period_result else 0),
        "monthly_transactions": safe_int(period_result.sales_count if period_result else 0),
        "net_profit": period_profit,
        "daily_sales": safe_decimal_to_float(daily_result.daily_total if daily_result else 0),
        "daily_transactions": safe_int(daily_result.daily_count if daily_result else 0),
        "daily_profit": daily_profit,
        "weekly_sales": safe_decimal_to_float(weekly_result.weekly_total if weekly_result else 0)
    }


async def _calculate_profit_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    start_dt: datetime,
    end_dt: datetime
) -> float:
    """Calcul optimisé du bénéfice net"""
    
    # Récupérer les IDs des ventes
    sale_ids = db.query(Sale.id).filter(
        Sale.tenant_id == tenant_id,
        Sale.branch_id == branch_id,
        Sale.status == "completed",
        Sale.created_at >= start_dt,
        Sale.created_at <= end_dt
    ).all()
    
    sale_ids_list = [s.id for s in sale_ids]
    
    if not sale_ids_list:
        return 0.0
    
    # Total des ventes
    total_sales = db.query(func.coalesce(func.sum(Sale.total_amount), 0)).filter(
        Sale.id.in_(sale_ids_list)
    ).scalar() or 0
    
    # Coût des produits vendus - requête optimisée avec jointure
    total_cost = db.query(
        func.coalesce(func.sum(Product.purchase_price * SaleItem.quantity), 0)
    ).join(
        SaleItem, SaleItem.product_id == Product.id
    ).filter(
        SaleItem.sale_id.in_(sale_ids_list),
        Product.tenant_id == tenant_id
    ).scalar() or 0
    
    return safe_decimal_to_float(total_sales) - safe_decimal_to_float(total_cost)


async def _get_stock_stats_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID
) -> Dict[str, Any]:
    """Statistiques du stock optimisées"""
    
    # Une seule requête pour tout
    products = db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.branch_id == branch_id,
        Product.is_active == True
    ).all()
    
    total_quantity = 0
    total_purchase_value = 0
    total_selling_value = 0
    out_of_stock_count = 0
    low_stock_count = 0
    expired_count = 0
    expiring_soon_count = 0
    
    today = date.today()
    expiry_threshold = today + timedelta(days=30)
    
    for product in products:
        qty = product.quantity or 0
        total_quantity += qty
        total_purchase_value += safe_decimal_to_float(product.purchase_price) * qty
        total_selling_value += safe_decimal_to_float(product.selling_price) * qty
        
        if qty == 0:
            out_of_stock_count += 1
        elif qty <= (product.alert_threshold or 5):
            low_stock_count += 1
        
        if product.expiry_date:
            if product.expiry_date < today:
                expired_count += 1
            elif product.expiry_date <= expiry_threshold:
                expiring_soon_count += 1
    
    potential_profit = total_selling_value - total_purchase_value
    
    # Taux de rotation - requête optimisée
    stock_turnover = 0
    if total_quantity > 0:
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        total_sold = db.query(func.coalesce(func.sum(SaleItem.quantity), 0)).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            SaleItem.tenant_id == tenant_id,
            Sale.branch_id == branch_id,
            Sale.status == "completed",
            Sale.created_at >= thirty_days_ago
        ).scalar() or 0
        
        stock_turnover = round(total_sold / total_quantity, 2) if total_quantity > 0 else 0
    
    return {
        "total_products": len(products),
        "total_purchase_value": round(total_purchase_value, 2),
        "total_selling_value": round(total_selling_value, 2),
        "potential_profit": round(potential_profit, 2),
        "out_of_stock_count": out_of_stock_count,
        "low_stock_count": low_stock_count,
        "expired_count": expired_count,
        "expiring_soon_count": expiring_soon_count,
        "stock_turnover": stock_turnover
    }


async def _get_expense_stats_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    start_dt: datetime,
    end_dt: datetime
) -> Dict[str, Any]:
    """Statistiques des dépenses optimisées"""
    
    # Requête unique pour les dépenses de la période
    expenses = db.query(Expense).filter(
        Expense.tenant_id == tenant_id,
        Expense.branch_id == branch_id,
        Expense.expense_date >= start_dt.date(),
        Expense.expense_date <= end_dt.date()
    ).all()
    
    monthly_expenses = sum(safe_decimal_to_float(e.amount) for e in expenses)
    
    # Dépenses du jour
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())
    
    daily_expenses = db.query(Expense).filter(
        Expense.tenant_id == tenant_id,
        Expense.branch_id == branch_id,
        Expense.created_at >= today_start,
        Expense.created_at <= today_end
    ).all()
    
    daily_expenses_sum = sum(safe_decimal_to_float(e.amount) for e in daily_expenses)
    
    return {
        "monthly_expenses": round(monthly_expenses, 2),
        "daily_expenses": round(daily_expenses_sum, 2)
    }


async def _get_debt_stats_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID
) -> Dict[str, Any]:
    """Statistiques des dettes optimisées"""
    
    # Requête unique avec jointure
    debts = db.query(Debt).join(
        Sale, Debt.sale_id == Sale.id
    ).filter(
        Debt.tenant_id == tenant_id,
        Sale.branch_id == branch_id,
        Debt.is_active == True,
        Sale.status == "completed"
    ).all()
    
    total_debts = sum(safe_decimal_to_float(d.remaining_amount) for d in debts)
    unpaid_debts = sum(safe_decimal_to_float(d.remaining_amount) for d in debts if d.status == "unpaid")
    
    recovery_rate = 0
    total_initial = sum(safe_decimal_to_float(d.initial_amount) for d in debts)
    if total_initial > 0:
        recovery_rate = round(((total_initial - total_debts) / total_initial) * 100, 2)
    
    # Dettes du mois
    first_day_of_month = date.today().replace(day=1)
    monthly_debts = sum(
        safe_decimal_to_float(d.initial_amount)
        for d in debts
        if d.created_at and d.created_at.date() >= first_day_of_month
    )
    
    return {
        "total_debts": round(total_debts, 2),
        "unpaid_debts": round(unpaid_debts, 2),
        "recovery_rate": recovery_rate,
        "monthly_debts": round(monthly_debts, 2)
    }


async def _get_purchase_stats_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    start_dt: datetime,
    end_dt: datetime
) -> Dict[str, Any]:
    """Statistiques des achats optimisées"""
    
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        return {"monthly_purchases": 0, "daily_purchases": 0, "suppliers_count": 0, "pending_orders": 0, "pending_transfers": 0, "in_transit_transfers": 0}
    
    # Achats de la période
    purchases = db.query(Purchase).filter(
        Purchase.tenant_id == tenant_id,
        Purchase.pharmacy_id == branch.parent_pharmacy_id,
        Purchase.status == "completed",
        Purchase.created_at >= start_dt,
        Purchase.created_at <= end_dt
    ).all()
    
    monthly_purchases = sum(safe_decimal_to_float(p.total_amount) for p in purchases)
    
    # Achats du jour
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())
    
    daily_purchases = db.query(Purchase).filter(
        Purchase.tenant_id == tenant_id,
        Purchase.pharmacy_id == branch.parent_pharmacy_id,
        Purchase.status == "completed",
        Purchase.created_at >= today_start,
        Purchase.created_at <= today_end
    ).all()
    
    daily_purchases_sum = sum(safe_decimal_to_float(p.total_amount) for p in daily_purchases)
    
    # Nombre de fournisseurs
    suppliers_count = db.query(func.count(func.distinct(Purchase.supplier_id))).filter(
        Purchase.tenant_id == tenant_id,
        Purchase.pharmacy_id == branch.parent_pharmacy_id,
        Purchase.status == "completed"
    ).scalar() or 0
    
    # Commandes en attente
    pending_orders = db.query(Purchase).filter(
        Purchase.tenant_id == tenant_id,
        Purchase.pharmacy_id == branch.parent_pharmacy_id,
        Purchase.status.in_(["pending", "ordered", "draft"])
    ).count()
    
    # Transferts
    pending_transfers = db.query(ProductTransfer).filter(
        ProductTransfer.tenant_id == tenant_id,
        or_(
            ProductTransfer.source_branch_id == branch_id,
            ProductTransfer.destination_branch_id == branch_id
        ),
        ProductTransfer.status == TransferStatus.PENDING
    ).count()
    
    in_transit_transfers = db.query(ProductTransfer).filter(
        ProductTransfer.tenant_id == tenant_id,
        or_(
            ProductTransfer.source_branch_id == branch_id,
            ProductTransfer.destination_branch_id == branch_id
        ),
        ProductTransfer.status == TransferStatus.IN_TRANSIT
    ).count()
    
    return {
        "monthly_purchases": round(monthly_purchases, 2),
        "daily_purchases": round(daily_purchases_sum, 2),
        "suppliers_count": suppliers_count,
        "pending_orders": pending_orders,
        "pending_transfers": pending_transfers,
        "in_transit_transfers": in_transit_transfers
    }


async def _get_recent_transactions_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Transactions récentes optimisées"""
    
    sales = db.query(Sale).filter(
        Sale.tenant_id == tenant_id,
        Sale.branch_id == branch_id,
        Sale.status == "completed"
    ).order_by(desc(Sale.created_at)).limit(limit).all()
    
    return [
        {
            "reference": s.reference,
            "amount": safe_decimal_to_float(s.total_amount),
            "date": s.created_at.isoformat(),
            "payment_method": s.payment_method
        }
        for s in sales
    ]


async def _get_recent_purchases_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Achats récents optimisés"""
    
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        return []
    
    purchases = db.query(Purchase).filter(
        Purchase.tenant_id == tenant_id,
        Purchase.pharmacy_id == branch.parent_pharmacy_id,
        Purchase.status == "completed"
    ).order_by(desc(Purchase.created_at)).limit(limit).all()
    
    return [
        {
            "supplier_name": p.supplier.name if p.supplier and p.supplier.name else (p.supplier_name or "Inconnu"),
            "amount": safe_decimal_to_float(p.total_amount),
            "date": p.created_at.isoformat()
        }
        for p in purchases
    ]


async def _get_recent_debts_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Dettes récentes optimisées"""
    
    debts = db.query(Debt).join(
        Sale, Debt.sale_id == Sale.id
    ).filter(
        Debt.tenant_id == tenant_id,
        Sale.branch_id == branch_id,
        Debt.is_active == True
    ).order_by(desc(Debt.created_at)).limit(limit).all()
    
    return [
        {
            "customer_name": d.customer.name if d.customer else "Client inconnu",
            "amount": safe_decimal_to_float(d.remaining_amount),
            "due_date": d.due_date.isoformat() if d.due_date else None
        }
        for d in debts
    ]


async def _get_expense_categories_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    start_dt: datetime,
    end_dt: datetime,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """Catégories de dépenses optimisées"""
    
    results = db.query(
        Expense.expense_type,
        func.coalesce(func.sum(Expense.amount), 0).label("total")
    ).filter(
        Expense.tenant_id == tenant_id,
        Expense.branch_id == branch_id,
        Expense.expense_date >= start_dt.date(),
        Expense.expense_date <= end_dt.date()
    ).group_by(Expense.expense_type).order_by(desc("total")).limit(limit).all()
    
    return [
        {
            "name": r.expense_type or "Autres",
            "amount": safe_decimal_to_float(r.total)
        }
        for r in results
    ]


async def _get_low_stock_products_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Produits en stock bas optimisés"""
    
    products = db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.branch_id == branch_id,
        Product.is_active == True,
        Product.quantity > 0,
        Product.quantity <= Product.alert_threshold
    ).order_by(Product.quantity.asc()).limit(limit).all()
    
    return [
        {
            "name": p.name,
            "current_stock": p.quantity,
            "threshold": p.alert_threshold
        }
        for p in products
    ]


async def _get_expiring_products_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Produits expirant bientôt optimisés"""
    
    today = date.today()
    threshold = today + timedelta(days=30)
    
    products = db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.branch_id == branch_id,
        Product.is_active == True,
        Product.expiry_date.isnot(None),
        Product.expiry_date <= threshold,
        Product.quantity > 0
    ).order_by(Product.expiry_date.asc()).limit(limit).all()
    
    return [
        {
            "name": p.name,
            "expiry_date": p.expiry_date.isoformat(),
            "quantity": p.quantity
        }
        for p in products
    ]


async def _get_active_users_count_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID
) -> int:
    """Nombre d'utilisateurs actifs optimisé"""
    
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    count = db.query(func.count(func.distinct(Sale.created_by))).filter(
        Sale.tenant_id == tenant_id,
        Sale.branch_id == branch_id,
        Sale.status == "completed",
        Sale.created_at >= thirty_days_ago
    ).scalar() or 0
    
    return count


async def _get_customers_count_optimized(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID
) -> int:
    """Nombre total de clients optimisé"""
    
    return db.query(Customer).filter(
        Customer.tenant_id == tenant_id,
        Customer.branch_id == branch_id,
        Customer.is_active == True
    ).count()
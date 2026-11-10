# app/api/v1/endpoints/profit.py
"""
API de gestion des bénéfices et analyses financières
Intégration complète avec les modules sales et stock
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, and_, or_, extract, case, text
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, date, timedelta
from decimal import Decimal
import logging
from enum import Enum

from app.db.session import get_db
from app.models.sale import Sale, SaleItem
from app.models.product import Product
from app.models.user import User
from app.models.pharmacy import Pharmacy
from app.models.branch import Branch
from app.models.tenant import Tenant
from app.models.user_pharmacy import UserPharmacy
from app.models.stock_movement import StockMovement
from app.schemas.profit import (
    ProfitStatsResponse,
    ProfitBreakdownResponse,
    DailyProfitResponse,
    PeriodProfitResponse,
    UserProfitResponse,
    BranchProfitResponse,
    SessionProfitResponse,
    FinancialAnalysisResponse,
    ProfitComparisonResponse,
    ProfitTrendResponse,
    SWOTAnalysisResponse,
    ProfitForecastResponse,
    BestPerformersResponse
)
from app.api.deps import (
    get_current_tenant,
    get_current_user,
    get_current_active_user,
    get_current_pharmacy_entity,
    get_current_branch_entity,
    require_permission,
    can_user_access_pharmacy
)

router = APIRouter(prefix="/profit", tags=["Bénéfices"])
logger = logging.getLogger(__name__)


# =======================
# Enums et Helpers
# =======================

class PeriodType(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    CUSTOM = "custom"


def get_user_accessible_pharmacies(db: Session, user_id: UUID, tenant_id: Optional[UUID] = None) -> List[UUID]:
    """Récupère la liste des pharmacies accessibles par l'utilisateur"""
    if not user_id:
        return []
    
    query = db.query(UserPharmacy.pharmacy_id).filter(UserPharmacy.user_id == user_id)
    
    if tenant_id:
        query = query.join(Pharmacy).filter(Pharmacy.tenant_id == tenant_id)
    
    return [p.pharmacy_id for p in query.all()]


def calculate_profit_for_sale_item(
    sale_item: SaleItem, 
    product: Optional[Product] = None
) -> Dict[str, Decimal]:
    """
    Calcule le bénéfice pour un item de vente
    """
    if not product and sale_item.product_id:
        return {
            "gross_profit": Decimal('0'),
            "net_profit": Decimal('0'),
            "margin_rate": Decimal('0'),
            "cost_price": Decimal('0'),
            "selling_price": Decimal(str(sale_item.unit_price)),
            "quantity": Decimal(str(sale_item.quantity)),
            "revenue": Decimal(str(sale_item.total))
        }
    
    cost_price = Decimal(str(product.purchase_price)) if product else Decimal('0')
    selling_price = Decimal(str(sale_item.unit_price))
    quantity = Decimal(str(sale_item.quantity))
    
    gross_profit = (selling_price - cost_price) * quantity
    margin_rate = ((selling_price - cost_price) / cost_price * 100) if cost_price > 0 else 0
    
    return {
        "gross_profit": gross_profit,
        "net_profit": gross_profit,  # À ajuster selon les frais
        "margin_rate": margin_rate,
        "cost_price": cost_price,
        "selling_price": selling_price,
        "quantity": quantity,
        "revenue": selling_price * quantity
    }


def get_period_date_range(period: PeriodType, reference_date: date = None) -> tuple:
    """
    Retourne la plage de dates pour une période donnée
    """
    if reference_date is None:
        reference_date = date.today()
    
    if period == PeriodType.DAY:
        start_date = datetime.combine(reference_date, datetime.min.time())
        end_date = datetime.combine(reference_date, datetime.max.time())
    elif period == PeriodType.WEEK:
        start_of_week = reference_date - timedelta(days=reference_date.weekday())
        start_date = datetime.combine(start_of_week, datetime.min.time())
        end_date = datetime.combine(start_of_week + timedelta(days=6), datetime.max.time())
    elif period == PeriodType.MONTH:
        start_of_month = reference_date.replace(day=1)
        next_month = start_of_month.replace(month=start_of_month.month + 1) if start_of_month.month < 12 else start_of_month.replace(year=start_of_month.year + 1, month=1)
        start_date = datetime.combine(start_of_month, datetime.min.time())
        end_date = datetime.combine(next_month - timedelta(days=1), datetime.max.time())
    elif period == PeriodType.YEAR:
        start_of_year = reference_date.replace(month=1, day=1)
        start_date = datetime.combine(start_of_year, datetime.min.time())
        end_date = datetime.combine(start_of_year.replace(year=start_of_year.year + 1) - timedelta(days=1), datetime.max.time())
    else:
        start_date = datetime.combine(reference_date - timedelta(days=30), datetime.min.time())
        end_date = datetime.combine(reference_date, datetime.max.time())
    
    return start_date, end_date


def get_previous_period(period: PeriodType, reference_date: date = None) -> tuple:
    """
    Retourne la plage de dates pour la période précédente
    """
    if reference_date is None:
        reference_date = date.today()
    
    if period == PeriodType.DAY:
        prev_date = reference_date - timedelta(days=1)
        start_date = datetime.combine(prev_date, datetime.min.time())
        end_date = datetime.combine(prev_date, datetime.max.time())
    elif period == PeriodType.WEEK:
        prev_week = reference_date - timedelta(days=7)
        start_of_week = prev_week - timedelta(days=prev_week.weekday())
        start_date = datetime.combine(start_of_week, datetime.min.time())
        end_date = datetime.combine(start_of_week + timedelta(days=6), datetime.max.time())
    elif period == PeriodType.MONTH:
        prev_month = reference_date.replace(day=1) - timedelta(days=1)
        start_of_month = prev_month.replace(day=1)
        next_month = start_of_month.replace(month=start_of_month.month + 1) if start_of_month.month < 12 else start_of_month.replace(year=start_of_month.year + 1, month=1)
        start_date = datetime.combine(start_of_month, datetime.min.time())
        end_date = datetime.combine(next_month - timedelta(days=1), datetime.max.time())
    elif period == PeriodType.YEAR:
        start_date = datetime.combine(reference_date.replace(year=reference_date.year - 1, month=1, day=1), datetime.min.time())
        end_date = datetime.combine(reference_date.replace(year=reference_date.year - 1, month=12, day=31), datetime.max.time())
    else:
        start_date = datetime.combine(reference_date - timedelta(days=60), datetime.min.time())
        end_date = datetime.combine(reference_date - timedelta(days=31), datetime.max.time())
    
    return start_date, end_date


# =======================
# Routes Principales
# =======================

@router.get("/stats", response_model=ProfitStatsResponse, summary="Statistiques globales des bénéfices")
async def get_profit_stats(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    period: PeriodType = Query(PeriodType.MONTH, description="Période d'analyse"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par succursale"),
    start_date: Optional[date] = Query(None, description="Date de début (pour période custom)"),
    end_date: Optional[date] = Query(None, description="Date de fin (pour période custom)")
):
    """
    Statistiques globales des bénéfices:
    - Bénéfice brut et net
    - Valeur d'achat et de vente
    - Profit attendu, réalisé, restant
    - Taux de marge
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacy_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.id == pharmacy_id)
            if branch_id:
                pharmacy_query = pharmacy_query.join(Branch).filter(Branch.id == branch_id)
            pharmacy_ids = [p.id for p in pharmacy_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(status_code=403, detail="Accès non autorisé")
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        if not pharmacy_ids:
            return ProfitStatsResponse(
                gross_profit=0, net_profit=0, total_revenue=0, total_cost=0,
                expected_profit=0, actual_profit=0, remaining_profit=0,
                margin_rate=0, purchase_value=0, selling_value=0
            )
        
        # Déterminer la plage de dates
        if period == PeriodType.CUSTOM and start_date and end_date:
            start_datetime = datetime.combine(start_date, datetime.min.time())
            end_datetime = datetime.combine(end_date, datetime.max.time())
        else:
            start_datetime, end_datetime = get_period_date_range(period)
        
        # Récupérer les ventes de la période
        sales = db.query(Sale).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        if tenant_id:
            sales = sales.filter(Sale.tenant_id == tenant_id)
        
        sale_ids = [s.id for s in sales.all()]
        
        if not sale_ids:
            return ProfitStatsResponse(
                gross_profit=0, net_profit=0, total_revenue=0, total_cost=0,
                expected_profit=0, actual_profit=0, remaining_profit=0,
                margin_rate=0, purchase_value=0, selling_value=0
            )
        
        # Récupérer les items de vente avec leurs produits
        sale_items = db.query(SaleItem).filter(
            SaleItem.sale_id.in_(sale_ids),
            SaleItem.tenant_id == tenant_id
        ).all()
        
        total_revenue = Decimal('0')
        total_cost = Decimal('0')
        gross_profit = Decimal('0')
        
        for item in sale_items:
            product = db.query(Product).filter(
                Product.id == item.product_id,
                Product.tenant_id == tenant_id
            ).first()
            
            revenue = Decimal(str(item.total))
            cost = Decimal(str(product.purchase_price)) * Decimal(str(item.quantity)) if product else Decimal('0')
            
            total_revenue += revenue
            total_cost += cost
            gross_profit += revenue - cost
        
        # Valeur du stock actuel - Convertir en Decimal pour cohérence
        current_products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.pharmacy_id.in_(pharmacy_ids),
            Product.is_active == True
        ).all()
        
        purchase_value = Decimal(str(sum(float(p.purchase_value) for p in current_products)))
        selling_value = Decimal(str(sum(float(p.selling_value) for p in current_products)))
        
        # Bénéfice attendu (marge sur stock actuel)
        expected_profit = selling_value - purchase_value
        
        # Convertir gross_profit en Decimal pour la comparaison
        margin_rate = (gross_profit / total_revenue * 100) if total_revenue > 0 else Decimal('0')
        
        # Convertir les valeurs pour la réponse
        gross_profit_float = float(gross_profit)
        expected_profit_float = float(expected_profit)
        
        return ProfitStatsResponse(
            gross_profit=gross_profit_float,
            net_profit=gross_profit_float,
            total_revenue=float(total_revenue),
            total_cost=float(total_cost),
            expected_profit=expected_profit_float,
            actual_profit=gross_profit_float,
            remaining_profit=expected_profit_float - gross_profit_float if expected_profit_float > gross_profit_float else 0,
            margin_rate=float(margin_rate),
            purchase_value=float(purchase_value),
            selling_value=float(selling_value),
            period_start=start_datetime.isoformat(),
            period_end=end_datetime.isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération stats bénéfices: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération stats: {str(e)}"
        )

@router.get("/daily", response_model=List[DailyProfitResponse], summary="Bénéfices journaliers")
async def get_daily_profit(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    days: int = Query(30, ge=1, le=365, description="Nombre de jours"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par succursale")
):
    """
    Bénéfices journaliers sur une période
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacy_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.id == pharmacy_id)
            if branch_id:
                pharmacy_query = pharmacy_query.join(Branch).filter(Branch.id == branch_id)
            pharmacy_ids = [p.id for p in pharmacy_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(status_code=403, detail="Accès non autorisé")
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        if not pharmacy_ids:
            return []
        
        start_date = date.today() - timedelta(days=days)
        
        results = []
        
        for i in range(days):
            current_date = start_date + timedelta(days=i)
            start_datetime = datetime.combine(current_date, datetime.min.time())
            end_datetime = datetime.combine(current_date, datetime.max.time())
            
            # Ventes du jour
            sales = db.query(Sale).filter(
                Sale.status == "completed",
                Sale.pharmacy_id.in_(pharmacy_ids),
                Sale.created_at >= start_datetime,
                Sale.created_at <= end_datetime
            )
            if tenant_id:
                sales = sales.filter(Sale.tenant_id == tenant_id)
            
            sale_ids = [s.id for s in sales.all()]
            
            if sale_ids:
                sale_items = db.query(SaleItem).filter(
                    SaleItem.sale_id.in_(sale_ids),
                    SaleItem.tenant_id == tenant_id
                ).all()
                
                total_revenue = Decimal('0')
                total_cost = Decimal('0')
                
                for item in sale_items:
                    product = db.query(Product).filter(
                        Product.id == item.product_id,
                        Product.tenant_id == tenant_id
                    ).first()
                    
                    revenue = Decimal(str(item.total))
                    cost = Decimal(str(product.purchase_price)) * Decimal(str(item.quantity)) if product else Decimal('0')
                    
                    total_revenue += revenue
                    total_cost += cost
                
                daily_profit = total_revenue - total_cost
            else:
                total_revenue = Decimal('0')
                total_cost = Decimal('0')
                daily_profit = Decimal('0')
            
            results.append(DailyProfitResponse(
                date=current_date.isoformat(),
                revenue=float(total_revenue),
                cost=float(total_cost),
                profit=float(daily_profit),
                margin_rate=float(daily_profit / total_revenue * 100) if total_revenue > 0 else 0,
                sales_count=len(sale_ids) if sale_ids else 0
            ))
        
        return results
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération bénéfices journaliers: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération bénéfices: {str(e)}"
        )


@router.get("/by-period", response_model=PeriodProfitResponse, summary="Bénéfices par période")
async def get_period_profit(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    period: PeriodType = Query(PeriodType.MONTH, description="Période"),
    year: Optional[int] = Query(None, description="Année (pour mois/année)"),
    month: Optional[int] = Query(None, description="Mois (1-12)"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par succursale")
):
    """
    Bénéfices agrégés par période (jour, semaine, mois, année)
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        today = date.today()
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacy_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.id == pharmacy_id)
            if branch_id:
                pharmacy_query = pharmacy_query.join(Branch).filter(Branch.id == branch_id)
            pharmacy_ids = [p.id for p in pharmacy_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(status_code=403, detail="Accès non autorisé")
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        if not pharmacy_ids:
            return PeriodProfitResponse(
                period=period.value, data=[], total_profit=0, total_revenue=0,
                average_profit=0, best_day=None, worst_day=None
            )
        
        results = []
        
        if period == PeriodType.DAY:
            # 30 derniers jours
            for i in range(30):
                current_date = today - timedelta(days=29 - i)
                start_datetime = datetime.combine(current_date, datetime.min.time())
                end_datetime = datetime.combine(current_date, datetime.max.time())
                
                profit = await _calculate_profit_for_period(
                    db, tenant_id, pharmacy_ids, start_datetime, end_datetime
                )
                
                results.append({
                    "period": current_date.isoformat(),
                    "profit": profit["profit"],
                    "revenue": profit["revenue"]
                })
        
        elif period == PeriodType.WEEK:
            # 12 dernières semaines
            for i in range(12):
                week_start = today - timedelta(days=today.weekday() + 7 * (11 - i))
                start_datetime = datetime.combine(week_start, datetime.min.time())
                end_datetime = datetime.combine(week_start + timedelta(days=6), datetime.max.time())
                
                profit = await _calculate_profit_for_period(
                    db, tenant_id, pharmacy_ids, start_datetime, end_datetime
                )
                
                results.append({
                    "period": f"Semaine {week_start.isoformat()}",
                    "profit": profit["profit"],
                    "revenue": profit["revenue"]
                })
        
        elif period == PeriodType.MONTH:
            # 12 derniers mois
            for i in range(12):
                month_date = today.replace(day=1) - timedelta(days=30 * (11 - i))
                start_datetime = datetime.combine(month_date.replace(day=1), datetime.min.time())
                
                if month_date.month == 12:
                    end_month = month_date.replace(year=month_date.year + 1, month=1, day=1) - timedelta(days=1)
                else:
                    end_month = month_date.replace(month=month_date.month + 1, day=1) - timedelta(days=1)
                
                end_datetime = datetime.combine(end_month, datetime.max.time())
                
                profit = await _calculate_profit_for_period(
                    db, tenant_id, pharmacy_ids, start_datetime, end_datetime
                )
                
                results.append({
                    "period": f"{month_date.strftime('%B %Y')}",
                    "profit": profit["profit"],
                    "revenue": profit["revenue"]
                })
        
        elif period == PeriodType.YEAR:
            # 5 dernières années
            for i in range(5):
                year_val = today.year - (4 - i)
                start_datetime = datetime.combine(date(year_val, 1, 1), datetime.min.time())
                end_datetime = datetime.combine(date(year_val, 12, 31), datetime.max.time())
                
                profit = await _calculate_profit_for_period(
                    db, tenant_id, pharmacy_ids, start_datetime, end_datetime
                )
                
                results.append({
                    "period": str(year_val),
                    "profit": profit["profit"],
                    "revenue": profit["revenue"]
                })
        
        # Calculer les statistiques
        total_profit = sum(r["profit"] for r in results)
        total_revenue = sum(r["revenue"] for r in results)
        average_profit = total_profit / len(results) if results else 0
        
        best_day = max(results, key=lambda x: x["profit"]) if results else None
        worst_day = min(results, key=lambda x: x["profit"]) if results else None
        
        return PeriodProfitResponse(
            period=period.value,
            data=results,
            total_profit=total_profit,
            total_revenue=total_revenue,
            average_profit=average_profit,
            best_day=best_day,
            worst_day=worst_day
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération bénéfices par période: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération bénéfices: {str(e)}"
        )


async def _calculate_profit_for_period(
    db: Session,
    tenant_id: Optional[UUID],
    pharmacy_ids: List[UUID],
    start_datetime: datetime,
    end_datetime: datetime
) -> Dict[str, float]:
    """Calcule le bénéfice pour une période donnée"""
    
    sales = db.query(Sale).filter(
        Sale.status == "completed",
        Sale.pharmacy_id.in_(pharmacy_ids),
        Sale.created_at >= start_datetime,
        Sale.created_at <= end_datetime
    )
    if tenant_id:
        sales = sales.filter(Sale.tenant_id == tenant_id)
    
    sale_ids = [s.id for s in sales.all()]
    
    if not sale_ids:
        return {"profit": 0, "revenue": 0}
    
    sale_items = db.query(SaleItem).filter(
        SaleItem.sale_id.in_(sale_ids),
        SaleItem.tenant_id == tenant_id
    ).all()
    
    total_revenue = Decimal('0')
    total_cost = Decimal('0')
    
    for item in sale_items:
        product = db.query(Product).filter(
            Product.id == item.product_id,
            Product.tenant_id == tenant_id
        ).first()
        
        revenue = Decimal(str(item.total))
        cost = Decimal(str(product.purchase_price)) * Decimal(str(item.quantity)) if product else Decimal('0')
        
        total_revenue += revenue
        total_cost += cost
    
    profit = total_revenue - total_cost
    
    return {
        "profit": float(profit),
        "revenue": float(total_revenue)
    }


@router.get("/by-user", response_model=List[UserProfitResponse], summary="Bénéfices par utilisateur")
async def get_profit_by_user(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    period: PeriodType = Query(PeriodType.MONTH, description="Période"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    limit: int = Query(10, ge=1, le=100, description="Nombre d'utilisateurs à retourner")
):
    """
    Bénéfices réalisés par chaque utilisateur (vendeur)
    Classement des meilleurs vendeurs
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer la plage de dates
        if period == PeriodType.CUSTOM and start_date and end_date:
            start_datetime = datetime.combine(start_date, datetime.min.time())
            end_datetime = datetime.combine(end_date, datetime.max.time())
        else:
            start_datetime, end_datetime = get_period_date_range(period)
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacy_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.id == pharmacy_id)
            pharmacy_ids = [p.id for p in pharmacy_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(status_code=403, detail="Accès non autorisé")
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        if not pharmacy_ids:
            return []
        
        # Récupérer les ventes groupées par utilisateur
        user_sales = db.query(
            Sale.created_by,
            func.sum(Sale.total_amount).label("total_revenue"),
            func.count(Sale.id).label("sale_count")
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        if tenant_id:
            user_sales = user_sales.filter(Sale.tenant_id == tenant_id)
        
        user_sales = user_sales.group_by(Sale.created_by).all()
        
        results = []
        
        for user_sale in user_sales:
            user = db.query(User).filter(User.id == user_sale.created_by).first()
            if not user:
                continue
            
            # Calculer le coût des ventes de cet utilisateur
            sales_by_user = db.query(Sale.id).filter(
                Sale.created_by == user_sale.created_by,
                Sale.status == "completed",
                Sale.pharmacy_id.in_(pharmacy_ids),
                Sale.created_at >= start_datetime,
                Sale.created_at <= end_datetime
            )
            if tenant_id:
                sales_by_user = sales_by_user.filter(Sale.tenant_id == tenant_id)
            
            sale_ids = [s.id for s in sales_by_user.all()]
            
            if sale_ids:
                sale_items = db.query(SaleItem).filter(
                    SaleItem.sale_id.in_(sale_ids),
                    SaleItem.tenant_id == tenant_id
                ).all()
                
                total_cost = Decimal('0')
                
                for item in sale_items:
                    product = db.query(Product).filter(
                        Product.id == item.product_id,
                        Product.tenant_id == tenant_id
                    ).first()
                    
                    cost = Decimal(str(product.purchase_price)) * Decimal(str(item.quantity)) if product else Decimal('0')
                    total_cost += cost
                
                profit = Decimal(str(user_sale.total_revenue)) - total_cost
            else:
                profit = Decimal('0')
            
            results.append(UserProfitResponse(
                user_id=str(user.id),
                user_name=user.nom_complet or user.email,
                user_role=user.role,
                total_revenue=float(user_sale.total_revenue),
                total_profit=float(profit),
                sale_count=int(user_sale.sale_count),
                margin_rate=float(profit / Decimal(str(user_sale.total_revenue)) * 100) if user_sale.total_revenue > 0 else 0
            ))
        
        # Trier par bénéfice décroissant
        results.sort(key=lambda x: x.total_profit, reverse=True)
        
        return results[:limit]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération bénéfices par utilisateur: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération bénéfices: {str(e)}"
        )


@router.get("/by-branch", response_model=List[BranchProfitResponse], summary="Bénéfices par succursale")
async def get_profit_by_branch(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    period: PeriodType = Query(PeriodType.MONTH, description="Période"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Bénéfices par succursale (branch)
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer la plage de dates
        if period == PeriodType.CUSTOM and start_date and end_date:
            start_datetime = datetime.combine(start_date, datetime.min.time())
            end_datetime = datetime.combine(end_date, datetime.max.time())
        else:
            start_datetime, end_datetime = get_period_date_range(period)
        
        # Récupérer toutes les branches accessibles
        branch_query = db.query(Branch).filter(Branch.is_active == True)
        if tenant_id:
            branch_query = branch_query.filter(Branch.tenant_id == tenant_id)
        
        branches = branch_query.all()
        
        results = []
        
        for branch in branches:
            # Ventes de cette branche
            sales = db.query(Sale).filter(
                Sale.status == "completed",
                Sale.branch_id == branch.id,
                Sale.created_at >= start_datetime,
                Sale.created_at <= end_datetime
            )
            if tenant_id:
                sales = sales.filter(Sale.tenant_id == tenant_id)
            
            sale_ids = [s.id for s in sales.all()]
            
            if sale_ids:
                sale_items = db.query(SaleItem).filter(
                    SaleItem.sale_id.in_(sale_ids),
                    SaleItem.tenant_id == tenant_id
                ).all()
                
                total_revenue = Decimal('0')
                total_cost = Decimal('0')
                
                for item in sale_items:
                    product = db.query(Product).filter(
                        Product.id == item.product_id,
                        Product.tenant_id == tenant_id
                    ).first()
                    
                    revenue = Decimal(str(item.total))
                    cost = Decimal(str(product.purchase_price)) * Decimal(str(item.quantity)) if product else Decimal('0')
                    
                    total_revenue += revenue
                    total_cost += cost
                
                profit = total_revenue - total_cost
                sale_count = len(sale_ids)
            else:
                total_revenue = Decimal('0')
                profit = Decimal('0')
                sale_count = 0
            
            results.append(BranchProfitResponse(
                branch_id=str(branch.id),
                branch_name=branch.name,
                total_revenue=float(total_revenue),
                total_profit=float(profit),
                sale_count=sale_count,
                margin_rate=float(profit / total_revenue * 100) if total_revenue > 0 else 0,
                city=branch.city
            ))
        
        # Trier par bénéfice décroissant
        results.sort(key=lambda x: x.total_profit, reverse=True)
        
        return results
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération bénéfices par succursale: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération bénéfices: {str(e)}"
        )


@router.get("/by-session", response_model=List[SessionProfitResponse], summary="Bénéfices par session")
async def get_profit_by_session(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    date_filter: Optional[date] = Query(None, description="Filtrer par date"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    limit: int = Query(20, ge=1, le=100)
):
    """
    Bénéfices par session de caisse
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        if date_filter:
            start_datetime = datetime.combine(date_filter, datetime.min.time())
            end_datetime = datetime.combine(date_filter, datetime.max.time())
        else:
            start_datetime = datetime.combine(date.today(), datetime.min.time())
            end_datetime = datetime.combine(date.today(), datetime.max.time())
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacy_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.id == pharmacy_id)
            pharmacy_ids = [p.id for p in pharmacy_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(status_code=403, detail="Accès non autorisé")
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        if not pharmacy_ids:
            return []
        
        # Grouper par vendeur et par heure approximative (session)
        # Pour simplifier, on utilise l'utilisateur comme session
        user_sales = db.query(
            Sale.created_by,
            func.min(Sale.created_at).label("session_start"),
            func.max(Sale.created_at).label("session_end"),
            func.sum(Sale.total_amount).label("total_revenue"),
            func.count(Sale.id).label("sale_count")
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        if tenant_id:
            user_sales = user_sales.filter(Sale.tenant_id == tenant_id)
        
        user_sales = user_sales.group_by(Sale.created_by).all()
        
        results = []
        
        for user_sale in user_sales:
            user = db.query(User).filter(User.id == user_sale.created_by).first()
            if not user:
                continue
            
            # Calculer le coût
            sales_by_user = db.query(Sale.id).filter(
                Sale.created_by == user_sale.created_by,
                Sale.status == "completed",
                Sale.pharmacy_id.in_(pharmacy_ids),
                Sale.created_at >= start_datetime,
                Sale.created_at <= end_datetime
            )
            if tenant_id:
                sales_by_user = sales_by_user.filter(Sale.tenant_id == tenant_id)
            
            sale_ids = [s.id for s in sales_by_user.all()]
            
            if sale_ids:
                sale_items = db.query(SaleItem).filter(
                    SaleItem.sale_id.in_(sale_ids),
                    SaleItem.tenant_id == tenant_id
                ).all()
                
                total_cost = Decimal('0')
                
                for item in sale_items:
                    product = db.query(Product).filter(
                        Product.id == item.product_id,
                        Product.tenant_id == tenant_id
                    ).first()
                    
                    cost = Decimal(str(product.purchase_price)) * Decimal(str(item.quantity)) if product else Decimal('0')
                    total_cost += cost
                
                profit = Decimal(str(user_sale.total_revenue)) - total_cost
            else:
                profit = Decimal('0')
            
            results.append(SessionProfitResponse(
                session_id=f"SESS-{user_sale.created_by}-{start_datetime.strftime('%Y%m%d')}",
                user_id=str(user.id),
                user_name=user.nom_complet or user.email,
                pharmacy_id=str(pharmacy_ids[0]) if pharmacy_ids else None,
                session_start=user_sale.session_start.isoformat(),
                session_end=user_sale.session_end.isoformat() if user_sale.session_end else None,
                total_revenue=float(user_sale.total_revenue),
                total_profit=float(profit),
                sale_count=int(user_sale.sale_count),
                margin_rate=float(profit / Decimal(str(user_sale.total_revenue)) * 100) if user_sale.total_revenue > 0 else 0
            ))
        
        results.sort(key=lambda x: x.total_profit, reverse=True)
        
        return results[:limit]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération bénéfices par session: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération bénéfices: {str(e)}"
        )


@router.get("/comparison", response_model=ProfitComparisonResponse, summary="Comparaison de bénéfices")
async def get_profit_comparison(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    period1_type: PeriodType = Query(PeriodType.MONTH, description="Type de période 1"),
    period1_start: Optional[date] = Query(None, description="Début période 1"),
    period1_end: Optional[date] = Query(None, description="Fin période 1"),
    period2_type: PeriodType = Query(PeriodType.MONTH, description="Type de période 2"),
    period2_start: Optional[date] = Query(None, description="Début période 2"),
    period2_end: Optional[date] = Query(None, description="Fin période 2"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Comparaison des bénéfices entre deux périodes
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        today = date.today()
        
        # Définir la période 1
        if period1_type == PeriodType.CUSTOM and period1_start and period1_end:
            p1_start = datetime.combine(period1_start, datetime.min.time())
            p1_end = datetime.combine(period1_end, datetime.max.time())
        else:
            p1_start, p1_end = get_period_date_range(period1_type, today)
        
        # Définir la période 2
        if period2_type == PeriodType.CUSTOM and period2_start and period2_end:
            p2_start = datetime.combine(period2_start, datetime.min.time())
            p2_end = datetime.combine(period2_end, datetime.max.time())
        else:
            p2_start, p2_end = get_period_date_range(period2_type, today - timedelta(days=30))
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacy_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.id == pharmacy_id)
            pharmacy_ids = [p.id for p in pharmacy_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(status_code=403, detail="Accès non autorisé")
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        if not pharmacy_ids:
            return ProfitComparisonResponse(
                period1={}, period2={}, absolute_change=0, percentage_change=0,
                trend="stable", analysis="Aucune donnée disponible"
            )
        
        # Calculer les bénéfices pour les deux périodes
        profit1 = await _calculate_profit_for_period(db, tenant_id, pharmacy_ids, p1_start, p1_end)
        profit2 = await _calculate_profit_for_period(db, tenant_id, pharmacy_ids, p2_start, p2_end)
        
        absolute_change = profit1["profit"] - profit2["profit"]
        percentage_change = (absolute_change / profit2["profit"] * 100) if profit2["profit"] > 0 else 0
        
        if absolute_change > 0:
            trend = "up"
            analysis = f"Augmentation de {percentage_change:.1f}% par rapport à la période précédente"
        elif absolute_change < 0:
            trend = "down"
            analysis = f"Diminution de {abs(percentage_change):.1f}% par rapport à la période précédente"
        else:
            trend = "stable"
            analysis = "Stable par rapport à la période précédente"
        
        return ProfitComparisonResponse(
            period1={
                "start": p1_start.isoformat(),
                "end": p1_end.isoformat(),
                "profit": profit1["profit"],
                "revenue": profit1["revenue"]
            },
            period2={
                "start": p2_start.isoformat(),
                "end": p2_end.isoformat(),
                "profit": profit2["profit"],
                "revenue": profit2["revenue"]
            },
            absolute_change=absolute_change,
            percentage_change=percentage_change,
            trend=trend,
            analysis=analysis
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur comparaison bénéfices: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur comparaison bénéfices: {str(e)}"
        )


@router.get("/trend", response_model=ProfitTrendResponse, summary="Tendance des bénéfices")
async def get_profit_trend(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    months: int = Query(12, ge=1, le=24, description="Nombre de mois"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Tendance des bénéfices sur plusieurs mois
    Avec projection et analyse de progression
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        today = date.today()
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacy_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.id == pharmacy_id)
            pharmacy_ids = [p.id for p in pharmacy_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(status_code=403, detail="Accès non autorisé")
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        if not pharmacy_ids:
            return ProfitTrendResponse(monthly_data=[], trend_percentage=0, trend_direction="stable", forecast=[])
        
        monthly_data = []
        
        for i in range(months):
            month_date = today.replace(day=1) - timedelta(days=30 * (months - 1 - i))
            start_datetime = datetime.combine(month_date.replace(day=1), datetime.min.time())
            
            if month_date.month == 12:
                end_month = month_date.replace(year=month_date.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                end_month = month_date.replace(month=month_date.month + 1, day=1) - timedelta(days=1)
            
            end_datetime = datetime.combine(end_month, datetime.max.time())
            
            profit = await _calculate_profit_for_period(db, tenant_id, pharmacy_ids, start_datetime, end_datetime)
            
            monthly_data.append({
                "month": month_date.strftime("%B %Y"),
                "profit": profit["profit"],
                "revenue": profit["revenue"]
            })
        
        # Calculer la tendance
        if len(monthly_data) >= 3:
            recent_avg = sum(m["profit"] for m in monthly_data[-3:]) / 3
            older_avg = sum(m["profit"] for m in monthly_data[:3]) / 3
            
            trend_percentage = ((recent_avg - older_avg) / older_avg * 100) if older_avg > 0 else 0
            
            if trend_percentage > 5:
                trend_direction = "up"
            elif trend_percentage < -5:
                trend_direction = "down"
            else:
                trend_direction = "stable"
        else:
            trend_percentage = 0
            trend_direction = "stable"
        
        # Projection simple (moyenne mobile)
        forecast = []
        if len(monthly_data) >= 3:
            avg_growth = sum(monthly_data[i+1]["profit"] - monthly_data[i]["profit"] for i in range(len(monthly_data)-1)) / (len(monthly_data)-1)
            
            last_profit = monthly_data[-1]["profit"]
            for i in range(1, 4):
                forecast.append({
                    "month": (today.replace(day=1) + timedelta(days=30 * i)).strftime("%B %Y"),
                    "projected_profit": last_profit + avg_growth * i,
                    "confidence": "medium"
                })
        
        return ProfitTrendResponse(
            monthly_data=monthly_data,
            trend_percentage=trend_percentage,
            trend_direction=trend_direction,
            forecast=forecast
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur tendance bénéfices: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur tendance bénéfices: {str(e)}"
        )


@router.get("/swot", response_model=SWOTAnalysisResponse, summary="Analyse SWOT")
async def get_swot_analysis(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Analyse SWOT (Forces, Faiblesses, Opportunités, Menaces)
    Basée sur les données de bénéfices et de ventes
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        today = date.today()
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacy_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.id == pharmacy_id)
            pharmacy_ids = [p.id for p in pharmacy_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(status_code=403, detail="Accès non autorisé")
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        # Périodes pour l'analyse
        this_month_start, this_month_end = get_period_date_range(PeriodType.MONTH, today)
        last_month_start, last_month_end = get_period_date_range(PeriodType.MONTH, today - timedelta(days=30))
        last_3_months_start, _ = get_period_date_range(PeriodType.MONTH, today - timedelta(days=90))
        
        # Calculer les bénéfices
        this_month_profit = await _calculate_profit_for_period(db, tenant_id, pharmacy_ids, this_month_start, this_month_end)
        last_month_profit = await _calculate_profit_for_period(db, tenant_id, pharmacy_ids, last_month_start, last_month_end)
        last_3_months_profit = await _calculate_profit_for_period(db, tenant_id, pharmacy_ids, last_3_months_start, this_month_end)
        
        # FORCES (Strengths)
        strengths = []
        
        # Tendance positive
        if this_month_profit["profit"] > last_month_profit["profit"]:
            strengths.append({
                "category": "Tendance",
                "description": "Croissance des bénéfices",
                "impact": "positive",
                "score": 8
            })
        
        # Marge bénéficiaire
        margin_rate = (this_month_profit["profit"] / this_month_profit["revenue"] * 100) if this_month_profit["revenue"] > 0 else 0
        if margin_rate > 30:
            strengths.append({
                "category": "Rentabilité",
                "description": "Bonne marge bénéficiaire",
                "impact": "positive",
                "score": 9
            })
        
        # FAIBLESSES (Weaknesses)
        weaknesses = []
        
        # Baisse des bénéfices
        if this_month_profit["profit"] < last_month_profit["profit"]:
            weaknesses.append({
                "category": "Performance",
                "description": "Baisse des bénéfices",
                "impact": "negative",
                "score": 7
            })
        
        # Faible marge
        if margin_rate < 15:
            weaknesses.append({
                "category": "Rentabilité",
                "description": "Marge bénéficiaire faible",
                "impact": "negative",
                "score": 8
            })
        
        # OPPORTUNITÉS (Opportunities)
        opportunities = []
        
        # Potentiel de croissance
        growth_potential = 100 - margin_rate if margin_rate < 30 else 10
        opportunities.append({
            "category": "Croissance",
            "description": "Potentiel d'augmentation des marges",
            "potential": growth_potential,
            "action": "Optimiser les prix de vente"
        })
        
        # MENACES (Threats)
        threats = []
        
        # Concurrence
        threats.append({
            "category": "Concurrence",
            "description": "Pression concurrentielle",
            "severity": 6,
            "mitigation": "Différenciation et fidélisation"
        })
        
        # Risque d'expiration
        expiring_soon = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.pharmacy_id.in_(pharmacy_ids),
            Product.is_active == True,
            Product.expiry_date.isnot(None),
            Product.expiry_date <= date.today() + timedelta(days=90)
        ).count()
        
        if expiring_soon > 10:
            threats.append({
                "category": "Stock",
                "description": "Risque de péremption de produits",
                "severity": 7,
                "mitigation": "Promotions sur produits proches expiration"
            })
        
        # Recommandations
        recommendations = []
        if margin_rate < 20:
            recommendations.append("Augmenter les prix des produits à forte demande")
        if this_month_profit["profit"] < last_month_profit["profit"]:
            recommendations.append("Analyser les causes de la baisse des bénéfices")
        if len(opportunities) > 0:
            recommendations.append("Exploiter les opportunités de croissance identifiées")
        
        return SWOTAnalysisResponse(
            strengths=strengths,
            weaknesses=weaknesses,
            opportunities=opportunities,
            threats=threats,
            recommendations=recommendations,
            summary=f"Analyse SWOT basée sur les données des {len(pharmacy_ids)} pharmacie(s)",
            last_updated=datetime.utcnow().isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur analyse SWOT: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur analyse SWOT: {str(e)}"
        )


@router.get("/forecast", response_model=ProfitForecastResponse, summary="Prévisions de bénéfices")
async def get_profit_forecast(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    months: int = Query(6, ge=1, le=12, description="Nombre de mois à prévoir"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Prévisions de bénéfices basées sur les données historiques
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        today = date.today()
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacy_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.id == pharmacy_id)
            pharmacy_ids = [p.id for p in pharmacy_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(status_code=403, detail="Accès non autorisé")
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        if not pharmacy_ids:
            return ProfitForecastResponse(forecast=[], confidence_level=0, methodology="Aucune donnée")
        
        # Récupérer les données des 12 derniers mois
        historical_data = []
        for i in range(12):
            month_date = today.replace(day=1) - timedelta(days=30 * (11 - i))
            start_datetime = datetime.combine(month_date.replace(day=1), datetime.min.time())
            
            if month_date.month == 12:
                end_month = month_date.replace(year=month_date.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                end_month = month_date.replace(month=month_date.month + 1, day=1) - timedelta(days=1)
            
            end_datetime = datetime.combine(end_month, datetime.max.time())
            
            profit = await _calculate_profit_for_period(db, tenant_id, pharmacy_ids, start_datetime, end_datetime)
            historical_data.append(profit["profit"])
        
        # Calculer la tendance (régression linéaire simple)
        n = len(historical_data)
        x = list(range(n))
        y = historical_data
        
        if n > 1:
            sum_x = sum(x)
            sum_y = sum(y)
            sum_xy = sum(x[i] * y[i] for i in range(n))
            sum_x2 = sum(x[i] ** 2 for i in range(n))
            
            slope = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x ** 2)
            intercept = (sum_y - slope * sum_x) / n
        else:
            slope = 0
            intercept = historical_data[0] if historical_data else 0
        
        # Générer les prévisions
        forecast = []
        for i in range(1, months + 1):
            projected = intercept + slope * (n + i)
            forecast.append({
                "month": (today.replace(day=1) + timedelta(days=30 * i)).strftime("%B %Y"),
                "projected_profit": max(0, projected),
                "lower_bound": max(0, projected * 0.8),
                "upper_bound": projected * 1.2
            })
        
        # Calculer le niveau de confiance basé sur la variance des données historiques
        if len(historical_data) >= 6:
            variance = sum((y[i] - (intercept + slope * x[i])) ** 2 for i in range(n)) / n
            confidence = max(50, min(95, 100 - (variance / (sum(y) / n) * 100) if sum(y) > 0 else 50))
        else:
            confidence = 60
        
        return ProfitForecastResponse(
            forecast=forecast,
            confidence_level=confidence,
            methodology="Régression linéaire basée sur 12 mois d'historique",
            historical_average=sum(historical_data) / n if historical_data else 0,
            historical_trend=slope
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur prévisions bénéfices: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur prévisions bénéfices: {str(e)}"
        )


# Correction pour la fonction get_best_performers dans profit.py

@router.get("/best-performers", response_model=BestPerformersResponse, summary="Meilleurs performances")
async def get_best_performers(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    period: PeriodType = Query(PeriodType.MONTH, description="Période"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    limit: int = Query(5, ge=1, le=10, description="Nombre de meilleurs performers"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Meilleurs performers:
    - Meilleurs produits
    - Meilleurs vendeurs
    - Meilleures catégories
    - Meilleures périodes
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        today_date = date.today()  # Correction: définir la variable today
        
        # Déterminer la plage de dates
        if period == PeriodType.CUSTOM and start_date and end_date:
            start_datetime = datetime.combine(start_date, datetime.min.time())
            end_datetime = datetime.combine(end_date, datetime.max.time())
        else:
            start_datetime, end_datetime = get_period_date_range(period, today_date)
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacy_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.id == pharmacy_id)
            pharmacy_ids = [p.id for p in pharmacy_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(status_code=403, detail="Accès non autorisé")
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        if not pharmacy_ids:
            return BestPerformersResponse(
                top_products=[], top_sellers=[], top_categories=[], top_periods=[]
            )
        
        # 1. Meilleurs produits
        top_products = db.query(
            Product.id,
            Product.name,
            Product.code,
            func.sum(SaleItem.quantity).label("total_quantity"),
            func.sum(SaleItem.total).label("total_revenue"),
            func.sum(SaleItem.quantity * Product.purchase_price).label("total_cost")
        ).join(
            SaleItem, SaleItem.product_id == Product.id
        ).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        
        if tenant_id:
            top_products = top_products.filter(Product.tenant_id == tenant_id)
        
        top_products = top_products.group_by(
            Product.id, Product.name, Product.code
        ).order_by(
            desc("total_revenue")
        ).limit(limit).all()
        
        top_products_list = []
        for p in top_products:
            profit = float(p.total_revenue) - float(p.total_cost or 0)
            top_products_list.append({
                "product_id": str(p.id),
                "product_name": p.name,
                "product_code": p.code,
                "total_sold": int(p.total_quantity),
                "total_revenue": float(p.total_revenue),
                "profit": profit,
                "margin_rate": profit / float(p.total_revenue) * 100 if p.total_revenue > 0 else 0
            })
        
        # 2. Meilleurs vendeurs
        top_sellers = db.query(
            Sale.created_by,
            func.sum(Sale.total_amount).label("total_revenue"),
            func.count(Sale.id).label("sale_count")
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        
        if tenant_id:
            top_sellers = top_sellers.filter(Sale.tenant_id == tenant_id)
        
        top_sellers = top_sellers.group_by(Sale.created_by).order_by(
            desc("total_revenue")
        ).limit(limit).all()
        
        top_sellers_list = []
        for s in top_sellers:
            user = db.query(User).filter(User.id == s.created_by).first()
            if user:
                top_sellers_list.append({
                    "user_id": str(user.id),
                    "user_name": user.nom_complet or user.email,
                    "total_revenue": float(s.total_revenue),
                    "sale_count": int(s.sale_count),
                    "average_basket": float(s.total_revenue) / s.sale_count if s.sale_count > 0 else 0
                })
        
        # 3. Meilleures catégories
        top_categories = db.query(
            Product.category,
            func.sum(SaleItem.quantity).label("total_quantity"),
            func.sum(SaleItem.total).label("total_revenue")
        ).join(
            SaleItem, SaleItem.product_id == Product.id
        ).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime,
            Product.category.isnot(None)
        )
        
        if tenant_id:
            top_categories = top_categories.filter(Product.tenant_id == tenant_id)
        
        top_categories = top_categories.group_by(Product.category).order_by(
            desc("total_revenue")
        ).limit(limit).all()
        
        # Calculer le total des revenus pour les pourcentages
        total_revenue_categories = sum(float(c.total_revenue) for c in top_categories) if top_categories else 0
        
        top_categories_list = []
        for c in top_categories:
            top_categories_list.append({
                "category": c.category,
                "total_sold": int(c.total_quantity),
                "total_revenue": float(c.total_revenue),
                "percentage": float(c.total_revenue) / total_revenue_categories * 100 if total_revenue_categories > 0 else 0
            })
        
        # 4. Meilleures périodes
        top_periods = []
        for i in range(6):
            period_start = today_date - timedelta(days=30 * i)
            period_end = period_start + timedelta(days=30)
            start_dt = datetime.combine(period_start.replace(day=1), datetime.min.time())
            # Correction: calculer correctement la fin du mois
            if period_start.month == 12:
                end_dt = datetime.combine(period_start.replace(year=period_start.year + 1, month=1, day=1) - timedelta(days=1), datetime.max.time())
            else:
                end_dt = datetime.combine(period_start.replace(month=period_start.month + 1, day=1) - timedelta(days=1), datetime.max.time())
            
            profit_data = await _calculate_profit_for_period(db, tenant_id, pharmacy_ids, start_dt, end_dt)
            
            top_periods.append({
                "period": period_start.strftime("%B %Y"),
                "profit": profit_data["profit"],
                "revenue": profit_data["revenue"]
            })
        
        top_periods.sort(key=lambda x: x["profit"], reverse=True)
        
        return BestPerformersResponse(
            top_products=top_products_list,
            top_sellers=top_sellers_list,
            top_categories=top_categories_list,
            top_periods=top_periods[:limit]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération meilleurs performers: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération performers: {str(e)}"
        )

@router.get("/history", summary="Historique des bénéfices")
async def get_profit_history(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    start_date: date = Query(..., description="Date de début"),
    end_date: date = Query(..., description="Date de fin"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par succursale"),
    group_by: str = Query("day", description="Grouper par: day, week, month")
):
    """
    Historique détaillé des bénéfices sur une période
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacy_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.id == pharmacy_id)
            if branch_id:
                pharmacy_query = pharmacy_query.join(Branch).filter(Branch.id == branch_id)
            pharmacy_ids = [p.id for p in pharmacy_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(status_code=403, detail="Accès non autorisé")
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        if not pharmacy_ids:
            return {"history": [], "total_profit": 0, "total_revenue": 0}
        
        # Déterminer le groupement
        if group_by == "day":
            group_field = func.date(Sale.created_at)
        elif group_by == "week":
            group_field = func.date_trunc('week', Sale.created_at)
        elif group_by == "month":
            group_field = func.date_trunc('month', Sale.created_at)
        else:
            group_field = func.date(Sale.created_at)
        
        # Récupérer les ventes groupées
        sales_by_period = db.query(
            group_field.label("period"),
            func.sum(Sale.total_amount).label("total_revenue"),
            func.count(Sale.id).label("sale_count")
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        
        if tenant_id:
            sales_by_period = sales_by_period.filter(Sale.tenant_id == tenant_id)
        
        sales_by_period = sales_by_period.group_by("period").order_by("period").all()
        
        history = []
        total_profit = 0
        total_revenue = 0
        
        for period_data in sales_by_period:
            # Récupérer les items pour cette période
            period_start = period_data.period
            if group_by == "day":
                period_end = period_start + timedelta(days=1)
            elif group_by == "week":
                period_end = period_start + timedelta(days=7)
            else:
                if period_start.month == 12:
                    period_end = period_start.replace(year=period_start.year + 1, month=1, day=1)
                else:
                    period_end = period_start.replace(month=period_start.month + 1, day=1)
            
            period_start_dt = datetime.combine(period_start, datetime.min.time())
            period_end_dt = datetime.combine(period_end - timedelta(days=1), datetime.max.time())
            
            profit_data = await _calculate_profit_for_period(
                db, tenant_id, pharmacy_ids, period_start_dt, period_end_dt
            )
            
            history.append({
                "period": period_start.isoformat(),
                "period_label": period_start.strftime("%Y-%m-%d") if group_by == "day" else 
                               period_start.strftime("Semaine %W %Y") if group_by == "week" else
                               period_start.strftime("%B %Y"),
                "revenue": profit_data["revenue"],
                "profit": profit_data["profit"],
                "sale_count": period_data.sale_count,
                "margin_rate": profit_data["profit"] / profit_data["revenue"] * 100 if profit_data["revenue"] > 0 else 0
            })
            
            total_profit += profit_data["profit"]
            total_revenue += profit_data["revenue"]
        
        return {
            "history": history,
            "total_profit": total_profit,
            "total_revenue": total_revenue,
            "average_profit": total_profit / len(history) if history else 0,
            "periods_count": len(history),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "group_by": group_by
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur historique bénéfices: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur historique bénéfices: {str(e)}"
        )


@router.get("/financial-analysis", response_model=FinancialAnalysisResponse, summary="Analyse financière complète")
async def get_financial_analysis(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    period: PeriodType = Query(PeriodType.MONTH, description="Période"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Analyse financière complète:
    - Ratio de rentabilité
    - Structure des coûts
    - Analyse des marges
    - Indicateurs de performance
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        today = date.today()
        
        start_datetime, end_datetime = get_period_date_range(period, today)
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacy_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacy_query = pharmacy_query.filter(Pharmacy.id == pharmacy_id)
            pharmacy_ids = [p.id for p in pharmacy_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(status_code=403, detail="Accès non autorisé")
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        if not pharmacy_ids:
            return FinancialAnalysisResponse(
                profitability_ratios={}, cost_structure={}, margin_analysis={},
                performance_indicators={}, recommendations=[]
            )
        
        # Récupérer les données financières
        profit_data = await _calculate_profit_for_period(db, tenant_id, pharmacy_ids, start_datetime, end_datetime)
        
        # Récupérer les ventes par méthode de paiement
        payment_methods = db.query(
            Sale.payment_method,
            func.sum(Sale.total_amount).label("total")
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        
        if tenant_id:
            payment_methods = payment_methods.filter(Sale.tenant_id == tenant_id)
        
        payment_methods = payment_methods.group_by(Sale.payment_method).all()
        
        payment_breakdown = {pm.payment_method: float(pm.total) for pm in payment_methods}
        
        # Récupérer les produits les plus vendus
        top_products = db.query(
            Product.name,
            func.sum(SaleItem.quantity).label("quantity"),
            func.sum(SaleItem.total).label("revenue")
        ).join(
            SaleItem, SaleItem.product_id == Product.id
        ).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        
        if tenant_id:
            top_products = top_products.filter(Product.tenant_id == tenant_id)
        
        top_products = top_products.group_by(Product.name).order_by(desc("revenue")).limit(5).all()
        
        # Récupérer les frais (si disponibles)
        # Pour l'instant, on simule quelques frais
        total_revenue = profit_data["revenue"]
        total_cost = profit_data["revenue"] - profit_data["profit"]
        
        cost_structure = {
            "cost_of_goods_sold": total_cost,
            "cost_percentage": (total_cost / total_revenue * 100) if total_revenue > 0 else 0,
            "operating_expenses": total_revenue * 0.1,  # Simulé
            "taxes": total_revenue * 0.05,  # Simulé
            "net_profit": profit_data["profit"] - (total_revenue * 0.15)  # Simulé
        }
        
        profitability_ratios = {
            "gross_margin": profit_data["profit"],
            "gross_margin_rate": (profit_data["profit"] / total_revenue * 100) if total_revenue > 0 else 0,
            "net_margin_rate": (cost_structure["net_profit"] / total_revenue * 100) if total_revenue > 0 else 0,
            "roi": (profit_data["profit"] / total_cost * 100) if total_cost > 0 else 0
        }
        
        margin_analysis = {
            "average_product_margin": profitability_ratios["gross_margin_rate"],
            "high_margin_products": len([p for p in top_products if p.revenue > 0]),
            "low_margin_products": 0,
            "margin_distribution": {}
        }
        
        performance_indicators = {
            "revenue_per_sale": total_revenue / len(pharmacy_ids) if pharmacy_ids else 0,
            "profit_per_sale": profit_data["profit"] / len(pharmacy_ids) if pharmacy_ids else 0,
            "sales_per_day": (len(pharmacy_ids) / ((end_datetime - start_datetime).days or 1)),
            "conversion_rate": 85  # Simulé
        }
        
        recommendations = []
        if profitability_ratios["gross_margin_rate"] < 30:
            recommendations.append("Augmenter les prix des produits à forte demande")
        if profitability_ratios["net_margin_rate"] < 15:
            recommendations.append("Réduire les coûts opérationnels")
        if performance_indicators["profit_per_sale"] < 100:
            recommendations.append("Optimiser le panier moyen des clients")
        
        return FinancialAnalysisResponse(
            profitability_ratios=profitability_ratios,
            cost_structure=cost_structure,
            margin_analysis=margin_analysis,
            performance_indicators=performance_indicators,
            recommendations=recommendations,
            payment_methods_breakdown=payment_breakdown,
            top_products=[
                {
                    "name": p.name,
                    "quantity": int(p.quantity),
                    "revenue": float(p.revenue)
                }
                for p in top_products
            ]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur analyse financière: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur analyse financière: {str(e)}"
        )
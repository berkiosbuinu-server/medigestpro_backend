# app/api/routes/finance.py
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, and_, or_, case
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, date, timedelta
from decimal import Decimal
import logging

from app.db.session import get_db
from app.models.finance import FinancialPeriod, FinancialTransaction, Capital, Expense
from app.models.sale import Sale, SaleItem
from app.models.product import Product
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.finance import (
    FinancialPeriodCreate, FinancialPeriodInDB, FinancialPeriodUpdate,
    FinancialTransactionCreate, FinancialTransactionInDB, FinancialTransactionUpdate,
    CapitalCreate, CapitalInDB, CapitalUpdate,
    ExpenseCreate, ExpenseInDB, ExpenseUpdate,
    FinancialAnalysisRequest, FinancialKPIs, MonthlyAnalysis, AnnualAnalysis,
    StockValuation, CapitalAnalysis, FinancialDashboard, ExportRequest
)
from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission
from app.services.reporting import ReportService

router = APIRouter(prefix="/finance", tags=["Finance"])
logger = logging.getLogger(__name__)

# ==============================================
# ENDPOINTS TABLEAU DE BORD
# ==============================================

@router.get("/dashboard", response_model=FinancialDashboard)
@require_permission("rapports")
def get_financial_dashboard(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les données du tableau de bord financier
    """
    today = date.today()
    current_month = today.month
    current_year = today.year
    
    try:
        # Calculs du jour
        today_sales = db.query(func.sum(Sale.total_amount)).filter(
            Sale.tenant_id == current_tenant.id,
            func.date(Sale.created_at) == today,
            Sale.status == "complete"
        ).scalar() or Decimal('0')
        
        today_expenses = db.query(func.sum(Expense.total_amount)).filter(
            Expense.tenant_id == current_tenant.id,
            Expense.expense_date == today
        ).scalar() or Decimal('0')
        
        today_profit = today_sales - today_expenses
        
        # Calculs du mois (jusqu'à aujourd'hui)
        month_start = date(current_year, current_month, 1)
        month_to_date_sales = db.query(func.sum(Sale.total_amount)).filter(
            Sale.tenant_id == current_tenant.id,
            Sale.created_at >= month_start,
            Sale.created_at <= today,
            Sale.status == "complete"
        ).scalar() or Decimal('0')
        
        month_to_date_expenses = db.query(func.sum(Expense.total_amount)).filter(
            Expense.tenant_id == current_tenant.id,
            Expense.expense_date >= month_start,
            Expense.expense_date <= today
        ).scalar() or Decimal('0')
        
        month_to_date_profit = month_to_date_sales - month_to_date_expenses
        
        # Calculs de l'année (jusqu'à aujourd'hui)
        year_start = date(current_year, 1, 1)
        year_to_date_sales = db.query(func.sum(Sale.total_amount)).filter(
            Sale.tenant_id == current_tenant.id,
            Sale.created_at >= year_start,
            Sale.created_at <= today,
            Sale.status == "complete"
        ).scalar() or Decimal('0')
        
        year_to_date_expenses = db.query(func.sum(Expense.total_amount)).filter(
            Expense.tenant_id == current_tenant.id,
            Expense.expense_date >= year_start,
            Expense.expense_date <= today
        ).scalar() or Decimal('0')
        
        year_to_date_profit = year_to_date_sales - year_to_date_expenses
        
        # Valeur du stock
        stock_value = db.query(func.sum(Product.quantity * Product.purchase_price)).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        ).scalar() or Decimal('0')
        
        # Capital total
        total_capital = db.query(func.sum(Capital.amount)).filter(
            Capital.tenant_id == current_tenant.id,
            Capital.capital_type.in_(["initial", "additional", "reinvestment"]),
            Capital.status == "completed"
        ).scalar() or Decimal('0')
        
        # Solde de caisse (simplifié)
        total_withdrawals = db.query(func.sum(Capital.amount)).filter(
            Capital.tenant_id == current_tenant.id,
            Capital.capital_type == "withdrawal",
            Capital.status == "completed"
        ).scalar() or Decimal('0')
        
        cash_balance = total_capital + year_to_date_profit - total_withdrawals
        
        # Alertes et recommandations
        alerts = []
        recommendations = []
        
        # Vérifier les seuils d'alerte
        if today_sales == 0:
            alerts.append("Aucune vente enregistrée aujourd'hui")
            recommendations.append("Vérifier les opérations de vente")
        
        if month_to_date_profit < Decimal('0'):
            alerts.append("Perte enregistrée ce mois-ci")
            recommendations.append("Analyser les dépenses et optimiser les coûts")
        
        # Calculer l'atteinte des objectifs si configuré
        monthly_target = None
        target_achievement = None
        
        if current_tenant.config and current_tenant.config.get("monthly_sales_target"):
            monthly_target = Decimal(str(current_tenant.config["monthly_sales_target"]))
            if monthly_target > 0:
                target_achievement = (month_to_date_sales / monthly_target) * 100
        
        return FinancialDashboard(
            today_sales=today_sales,
            today_expenses=today_expenses,
            today_profit=today_profit,
            month_to_date_sales=month_to_date_sales,
            month_to_date_expenses=month_to_date_expenses,
            month_to_date_profit=month_to_date_profit,
            monthly_target=monthly_target,
            target_achievement=target_achievement,
            year_to_date_sales=year_to_date_sales,
            year_to_date_expenses=year_to_date_expenses,
            year_to_date_profit=year_to_date_profit,
            stock_value=stock_value,
            total_capital=total_capital,
            cash_balance=cash_balance,
            alerts=alerts,
            recommendations=recommendations
        )
        
    except Exception as e:
        logger.error(f"Erreur lors du calcul du tableau de bord: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors du calcul du tableau de bord"
        )

# ==============================================
# ENDPOINTS ANALYSE MENSUELLE
# ==============================================

@router.get("/analysis/monthly", response_model=List[MonthlyAnalysis])
@require_permission("rapports")
def get_monthly_analysis(
    year: int = Query(None, description="Année spécifique (défaut: année courante)"),
    compare_years: bool = Query(False, description="Comparer avec l'année précédente"),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère l'analyse mensuelle détaillée
    """
    target_year = year or date.today().year
    previous_year = target_year - 1
    
    try:
        monthly_data = []
        
        for month in range(1, 13):
            # Période du mois
            month_start = date(target_year, month, 1)
            if month == 12:
                month_end = date(target_year, month, 31)
            else:
                month_end = date(target_year, month + 1, 1) - timedelta(days=1)
            
            # CA du mois
            monthly_sales = db.query(func.sum(Sale.total_amount)).filter(
                Sale.tenant_id == current_tenant.id,
                Sale.created_at >= month_start,
                Sale.created_at <= month_end,
                Sale.status == "complete"
            ).scalar() or Decimal('0')
            
            # Dépenses du mois
            monthly_expenses = db.query(func.sum(Expense.total_amount)).filter(
                Expense.tenant_id == current_tenant.id,
                Expense.expense_date >= month_start,
                Expense.expense_date <= month_end
            ).scalar() or Decimal('0')
            
            # Profit du mois
            monthly_profit = monthly_sales - monthly_expenses
            
            # Croissance vs mois précédent (même mois année précédente si compare_years=True)
            sales_growth = None
            if compare_years and month > 1:
                prev_month_start = date(previous_year, month, 1)
                if month == 12:
                    prev_month_end = date(previous_year, month, 31)
                else:
                    prev_month_end = date(previous_year, month + 1, 1) - timedelta(days=1)
                
                prev_month_sales = db.query(func.sum(Sale.total_amount)).filter(
                    Sale.tenant_id == current_tenant.id,
                    Sale.created_at >= prev_month_start,
                    Sale.created_at <= prev_month_end,
                    Sale.status == "complete"
                ).scalar() or Decimal('0')
                
                if prev_month_sales > 0:
                    sales_growth = ((monthly_sales - prev_month_sales) / prev_month_sales) * 100
            
            # Meilleur et pire jour
            best_day_query = db.query(
                func.date(Sale.created_at).label('sale_date'),
                func.sum(Sale.total_amount).label('daily_sales')
            ).filter(
                Sale.tenant_id == current_tenant.id,
                Sale.created_at >= month_start,
                Sale.created_at <= month_end,
                Sale.status == "complete"
            ).group_by(
                func.date(Sale.created_at)
            ).order_by(
                func.sum(Sale.total_amount).desc()
            ).first()
            
            best_day = None
            if best_day_query:
                best_day = {
                    "date": best_day_query[0],
                    "sales": best_day_query[1]
                }
            
            # Jours actifs
            active_days = db.query(
                func.count(func.distinct(func.date(Sale.created_at)))
            ).filter(
                Sale.tenant_id == current_tenant.id,
                Sale.created_at >= month_start,
                Sale.created_at <= month_end,
                Sale.status == "complete"
            ).scalar() or 0
            
            # Panier moyen
            avg_cart = Decimal('0')
            if active_days > 0:
                total_transactions = db.query(func.count(Sale.id)).filter(
                    Sale.tenant_id == current_tenant.id,
                    Sale.created_at >= month_start,
                    Sale.created_at <= month_end,
                    Sale.status == "complete"
                ).scalar() or 0
                
                if total_transactions > 0:
                    avg_cart = monthly_sales / total_transactions
            
            monthly_data.append(MonthlyAnalysis(
                month=f"{month:02d}/{target_year}",
                year=target_year,
                total_sales=monthly_sales,
                total_expenses=monthly_expenses,
                net_profit=monthly_profit,
                sales_growth=sales_growth,
                best_day=best_day,
                average_cart=avg_cart,
                active_days=active_days
            ))
        
        return monthly_data
        
    except Exception as e:
        logger.error(f"Erreur lors de l'analyse mensuelle: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'analyse mensuelle"
        )

# ==============================================
# ENDPOINTS ANALYSE ANNUELLE
# ==============================================

@router.get("/analysis/annual", response_model=List[AnnualAnalysis])
@require_permission("rapports")
def get_annual_analysis(
    years: List[int] = Query([], description="Liste des années à analyser"),
    include_breakdown: bool = Query(True, description="Inclure la répartition trimestrielle"),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère l'analyse annuelle détaillée
    """
    if not years:
        current_year = date.today().year
        years = [current_year - 1, current_year]
    
    try:
        annual_data = []
        
        for year in sorted(years):
            # CA annuel
            year_start = date(year, 1, 1)
            year_end = date(year, 12, 31)
            
            annual_sales = db.query(func.sum(Sale.total_amount)).filter(
                Sale.tenant_id == current_tenant.id,
                Sale.created_at >= year_start,
                Sale.created_at <= year_end,
                Sale.status == "complete"
            ).scalar() or Decimal('0')
            
            # Dépenses annuelles
            annual_expenses = db.query(func.sum(Expense.total_amount)).filter(
                Expense.tenant_id == current_tenant.id,
                Expense.expense_date >= year_start,
                Expense.expense_date <= year_end
            ).scalar() or Decimal('0')
            
            # Profit annuel
            annual_profit = annual_sales - annual_expenses
            
            # Croissance annuelle
            yearly_growth = None
            if years.index(year) > 0:
                prev_year = years[years.index(year) - 1]
                prev_year_sales = db.query(func.sum(Sale.total_amount)).filter(
                    Sale.tenant_id == current_tenant.id,
                    Sale.created_at >= date(prev_year, 1, 1),
                    Sale.created_at <= date(prev_year, 12, 31),
                    Sale.status == "complete"
                ).scalar() or Decimal('0')
                
                if prev_year_sales > 0:
                    yearly_growth = ((annual_sales - prev_year_sales) / prev_year_sales) * 100
            
            # Meilleur mois
            best_month_data = {}
            best_month_query = db.query(
                extract('month', Sale.created_at).label('month'),
                func.sum(Sale.total_amount).label('monthly_sales')
            ).filter(
                Sale.tenant_id == current_tenant.id,
                Sale.created_at >= year_start,
                Sale.created_at <= year_end,
                Sale.status == "complete"
            ).group_by(
                extract('month', Sale.created_at)
            ).order_by(
                func.sum(Sale.total_amount).desc()
            ).first()
            
            if best_month_query:
                best_month_data = {
                    "month": int(best_month_query[0]),
                    "sales": best_month_query[1]
                }
            
            # Répartition trimestrielle
            quarterly_breakdown = {}
            if include_breakdown:
                for quarter in range(1, 5):
                    quarter_start_month = (quarter - 1) * 3 + 1
                    quarter_start = date(year, quarter_start_month, 1)
                    
                    if quarter == 4:
                        quarter_end = date(year, 12, 31)
                    else:
                        quarter_end = date(year, quarter_start_month + 3, 1) - timedelta(days=1)
                    
                    quarter_sales = db.query(func.sum(Sale.total_amount)).filter(
                        Sale.tenant_id == current_tenant.id,
                        Sale.created_at >= quarter_start,
                        Sale.created_at <= quarter_end,
                        Sale.status == "complete"
                    ).scalar() or Decimal('0')
                    
                    quarterly_breakdown[f"Q{quarter}"] = quarter_sales
            
            # Tendance mensuelle
            monthly_trend = []
            for month in range(1, 13):
                month_start = date(year, month, 1)
                if month == 12:
                    month_end = date(year, month, 31)
                else:
                    month_end = date(year, month + 1, 1) - timedelta(days=1)
                
                month_sales = db.query(func.sum(Sale.total_amount)).filter(
                    Sale.tenant_id == current_tenant.id,
                    Sale.created_at >= month_start,
                    Sale.created_at <= month_end,
                    Sale.status == "complete"
                ).scalar() or Decimal('0')
                
                monthly_trend.append(month_sales)
            
            annual_data.append(AnnualAnalysis(
                year=year,
                total_sales=annual_sales,
                total_expenses=annual_expenses,
                net_profit=annual_profit,
                yearly_growth=yearly_growth,
                best_month=best_month_data,
                quarterly_breakdown=quarterly_breakdown,
                monthly_trend=monthly_trend
            ))
        
        return annual_data
        
    except Exception as e:
        logger.error(f"Erreur lors de l'analyse annuelle: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'analyse annuelle"
        )

# ==============================================
# ENDPOINTS VALEUR DU STOCK
# ==============================================

@router.get("/stock/valuation", response_model=StockValuation)
@require_permission("rapports")
def get_stock_valuation(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère l'évaluation détaillée du stock
    """
    try:
        # Valeurs totales
        total_purchase_value = db.query(
            func.sum(Product.quantity * Product.purchase_price)
        ).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        ).scalar() or Decimal('0')
        
        total_selling_value = db.query(
            func.sum(Product.quantity * Product.selling_price)
        ).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        ).scalar() or Decimal('0')
        
        potential_profit = total_selling_value - total_purchase_value
        
        # Marge moyenne
        avg_margin_query = db.query(
            func.avg((Product.selling_price - Product.purchase_price) / Product.purchase_price * 100)
        ).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True,
            Product.purchase_price > 0
        ).scalar()
        
        average_margin = float(avg_margin_query or 0)
        
        # Répartition par catégorie
        category_breakdown = {}
        category_query = db.query(
            Product.category,
            func.sum(Product.quantity).label('total_quantity'),
            func.sum(Product.quantity * Product.purchase_price).label('purchase_value'),
            func.sum(Product.quantity * Product.selling_price).label('selling_value')
        ).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        ).group_by(
            Product.category
        ).all()
        
        for category, quantity, purchase_val, selling_val in category_query:
            category_breakdown[category or "Non catégorisé"] = {
                "quantity": quantity,
                "purchase_value": purchase_val,
                "selling_value": selling_val,
                "potential_profit": selling_val - purchase_val if selling_val and purchase_val else Decimal('0')
            }
        
        # Analyse des péremptions
        today = date.today()
        expiry_analysis = {
            "expired": 0,
            "expiring_7_days": 0,
            "expiring_30_days": 0,
            "expiring_90_days": 0,
            "safe": 0
        }
        
        expiry_query = db.query(
            Product.expiry_date,
            func.sum(Product.quantity).label('quantity')
        ).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True,
            Product.expiry_date.isnot(None)
        ).group_by(
            Product.expiry_date
        ).all()
        
        for expiry_date, quantity in expiry_query:
            if not expiry_date:
                continue
                
            days_remaining = (expiry_date - today).days
            
            if days_remaining < 0:
                expiry_analysis["expired"] += quantity
            elif days_remaining <= 7:
                expiry_analysis["expiring_7_days"] += quantity
            elif days_remaining <= 30:
                expiry_analysis["expiring_30_days"] += quantity
            elif days_remaining <= 90:
                expiry_analysis["expiring_90_days"] += quantity
            else:
                expiry_analysis["safe"] += quantity
        
        # Alertes de stock bas
        low_stock_alerts = []
        low_stock_query = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True,
            Product.quantity > 0,
            Product.quantity <= Product.alert_threshold
        ).all()
        
        for product in low_stock_query:
            low_stock_alerts.append({
                "product_id": product.id,
                "product_name": product.name,
                "current_quantity": product.quantity,
                "alert_threshold": product.alert_threshold,
                "days_of_supply": calculate_days_of_supply(product, db)
            })
        
        return StockValuation(
            total_purchase_value=total_purchase_value,
            total_selling_value=total_selling_value,
            potential_profit=potential_profit,
            average_margin=average_margin,
            category_breakdown=category_breakdown,
            expiry_analysis=expiry_analysis,
            low_stock_alerts=low_stock_alerts
        )
        
    except Exception as e:
        logger.error(f"Erreur lors de l'évaluation du stock: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'évaluation du stock"
        )

# ==============================================
# ENDPOINTS CAPITAL
# ==============================================

@router.get("/capital/analysis", response_model=CapitalAnalysis)
@require_permission("rapports")
def get_capital_analysis(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère l'analyse détaillée du capital
    """
    try:
        # Capital initial
        initial_capital = db.query(func.sum(Capital.amount)).filter(
            Capital.tenant_id == current_tenant.id,
            Capital.capital_type == "initial",
            Capital.status == "completed"
        ).scalar() or Decimal('0')
        
        # Investissements supplémentaires
        additional_investments = db.query(func.sum(Capital.amount)).filter(
            Capital.tenant_id == current_tenant.id,
            Capital.capital_type.in_(["additional", "reinvestment"]),
            Capital.status == "completed"
        ).scalar() or Decimal('0')
        
        # Retraits
        withdrawals = db.query(func.sum(Capital.amount)).filter(
            Capital.tenant_id == current_tenant.id,
            Capital.capital_type == "withdrawal",
            Capital.status == "completed"
        ).scalar() or Decimal('0')
        
        # Capital actuel (simplifié)
        current_capital = initial_capital + additional_investments - withdrawals
        
        # Croissance du capital
        capital_growth = 0.0
        if initial_capital > 0:
            capital_growth = ((current_capital - initial_capital) / initial_capital) * 100
        
        # ROI (Return On Investment)
        # Calcul simplifié: profit total / capital total investi
        total_invested = initial_capital + additional_investments
        year_to_date_profit = Decimal('0')
        
        if total_invested > 0:
            current_year = date.today().year
            year_start = date(current_year, 1, 1)
            
            year_sales = db.query(func.sum(Sale.total_amount)).filter(
                Sale.tenant_id == current_tenant.id,
                Sale.created_at >= year_start,
                Sale.status == "complete"
            ).scalar() or Decimal('0')
            
            year_expenses = db.query(func.sum(Expense.total_amount)).filter(
                Expense.tenant_id == current_tenant.id,
                Expense.expense_date >= year_start
            ).scalar() or Decimal('0')
            
            year_to_date_profit = year_sales - year_expenses
            
            # ROI annuel
            return_on_investment = (year_to_date_profit / total_invested) * 100
        else:
            return_on_investment = 0.0
        
        # Historique du capital
        capital_history = []
        history_query = db.query(Capital).filter(
            Capital.tenant_id == current_tenant.id
        ).order_by(
            Capital.capital_date
        ).all()
        
        for capital in history_query:
            capital_history.append({
                "date": capital.capital_date,
                "type": capital.capital_type,
                "amount": capital.amount,
                "description": capital.description,
                "status": capital.status
            })
        
        return CapitalAnalysis(
            initial_capital=initial_capital,
            additional_investments=additional_investments,
            withdrawals=withdrawals,
            current_capital=current_capital,
            capital_growth=capital_growth,
            return_on_investment=return_on_investment,
            capital_history=capital_history
        )
        
    except Exception as e:
        logger.error(f"Erreur lors de l'analyse du capital: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'analyse du capital"
        )

# ==============================================
# ENDPOINTS EXPORT
# ==============================================

@router.post("/export")
@require_permission("rapports")
def export_financial_data(
    export_request: ExportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Exporte les données financières dans différents formats
    """
    try:
        report_service = ReportService(current_tenant, db)
        
        # Préparer les données selon le type d'analyse
        if export_request.analysis_type == "monthly":
            data = get_monthly_analysis(
                year=export_request.start_date.year if export_request.start_date else None,
                compare_years=True,
                db=db,
                current_tenant=current_tenant,
                current_user=current_user
            )
        elif export_request.analysis_type == "annual":
            data = get_annual_analysis(
                years=[],
                include_breakdown=True,
                db=db,
                current_tenant=current_tenant,
                current_user=current_user
            )
        elif export_request.analysis_type == "stock":
            data = get_stock_valuation(
                db=db,
                current_tenant=current_tenant,
                current_user=current_user
            )
        elif export_request.analysis_type == "capital":
            data = get_capital_analysis(
                db=db,
                current_tenant=current_tenant,
                current_user=current_user
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Type d'analyse non supporté"
            )
        
        # Lancer l'export en arrière-plan
        background_tasks.add_task(
            report_service.export_financial_data,
            data=data,
            export_format=export_request.format,
            analysis_type=export_request.analysis_type,
            user_id=current_user.id,
            include_charts=export_request.include_charts
        )
        
        return {
            "message": "Export démarré en arrière-plan",
            "format": export_request.format,
            "analysis_type": export_request.analysis_type
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de l'export des données: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'export des données"
        )

# ==============================================
# METHODES UTILITAIRES
# ==============================================

def calculate_days_of_supply(product, db: Session) -> Optional[int]:
    """
    Calcule les jours de stock restant basé sur les ventes récentes
    """
    try:
        # Ventes des 30 derniers jours
        thirty_days_ago = date.today() - timedelta(days=30)
        
        total_sold = db.query(func.sum(SaleItem.quantity)).join(Sale).filter(
            SaleItem.product_id == product.id,
            Sale.created_at >= thirty_days_ago,
            Sale.status == "complete"
        ).scalar() or 0
        
        # Calcul de la consommation journalière moyenne
        if total_sold > 0:
            daily_consumption = total_sold / 30
            if daily_consumption > 0:
                return int(product.quantity / daily_consumption)
        
        return None
        
    except Exception:
        return None

@router.get("/kpis/detailed")
@require_permission("rapports")
def get_detailed_kpis(
    period: str = Query("month", pattern="^(day|week|month|quarter|year)$"),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère des KPIs détaillés pour une période
    """
    try:
        # Définir la période si non spécifiée
        if not start_date or not end_date:
            today = date.today()
            if period == "day":
                start_date = end_date = today
            elif period == "week":
                start_date = today - timedelta(days=today.weekday())
                end_date = start_date + timedelta(days=6)
            elif period == "month":
                start_date = date(today.year, today.month, 1)
                if today.month == 12:
                    end_date = date(today.year, 12, 31)
                else:
                    end_date = date(today.year, today.month + 1, 1) - timedelta(days=1)
            elif period == "quarter":
                quarter = (today.month - 1) // 3 + 1
                start_date = date(today.year, 3 * quarter - 2, 1)
                if quarter == 4:
                    end_date = date(today.year, 12, 31)
                else:
                    end_date = date(today.year, 3 * quarter + 1, 1) - timedelta(days=1)
            else:  # year
                start_date = date(today.year, 1, 1)
                end_date = date(today.year, 12, 31)
        
        # Calculer les KPIs
        sales = db.query(func.sum(Sale.total_amount)).filter(
            Sale.tenant_id == current_tenant.id,
            Sale.created_at >= start_date,
            Sale.created_at <= end_date,
            Sale.status == "complete"
        ).scalar() or Decimal('0')
        
        expenses = db.query(func.sum(Expense.total_amount)).filter(
            Expense.tenant_id == current_tenant.id,
            Expense.expense_date >= start_date,
            Expense.expense_date <= end_date
        ).scalar() or Decimal('0')
        
        profit = sales - expenses
        
        # Nombre de transactions
        transactions = db.query(func.count(Sale.id)).filter(
            Sale.tenant_id == current_tenant.id,
            Sale.created_at >= start_date,
            Sale.created_at <= end_date,
            Sale.status == "complete"
        ).scalar() or 0
        
        # Panier moyen
        avg_cart = sales / transactions if transactions > 0 else Decimal('0')
        
        # Marge moyenne
        avg_margin_query = db.query(
            func.avg((SaleItem.unit_price - Product.purchase_price) / Product.purchase_price * 100)
        ).join(Product, SaleItem.product_id == Product.id).join(Sale, SaleItem.sale_id == Sale.id).filter(
            Sale.tenant_id == current_tenant.id,
            Sale.created_at >= start_date,
            Sale.created_at <= end_date,
            Sale.status == "complete",
            Product.purchase_price > 0
        ).scalar()
        
        avg_margin = float(avg_margin_query or 0)
        
        # Ratio dépenses/CA
        expense_ratio = (expenses / sales * 100) if sales > 0 else 0
        
        # Rentabilité
        profitability = (profit / sales * 100) if sales > 0 else 0
        
        return FinancialKPIs(
            period=f"{start_date} au {end_date}",
            total_sales=sales,
            total_expenses=expenses,
            gross_profit=profit,
            gross_margin=avg_margin,
            net_profit=profit,
            net_margin=profitability,
            expense_ratio=expense_ratio,
            profitability_ratio=profitability
        )
        
    except Exception as e:
        logger.error(f"Erreur lors du calcul des KPIs: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors du calcul des KPIs"
        )
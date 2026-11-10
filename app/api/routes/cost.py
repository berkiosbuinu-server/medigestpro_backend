# app/api/routes/cost.py
"""
API Routes pour la gestion des coûts, budgets et fournisseurs
Avec intégration complète du crédit fournisseur et de la comptabilité SYSCOHADA
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks, UploadFile, File
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, and_, or_, extract, desc
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, date, timedelta
import logging
import pandas as pd
from io import BytesIO
from decimal import Decimal
import uuid
from app.db.session import get_db
from app.models.cost import Cost, CostAllocation, Budget, Supplier, CostCategory, CostFrequency, PaymentMethod, BudgetPeriod
from app.models.department import Department
from app.models.project import Project
from app.models.tenant import Tenant
from app.models.user import User
from app.models.category import Category
from app.models.supplier_credit import (
    SupplierCreditConfig, SupplierDebt, PurchaseCredit, 
    ProductCreditItem, SaleCreditAllocation, SupplierCreditTransaction,
    CreditStatus, PaymentFrequency
)
from app.models.capital import Capital, CapitalTransaction, AdjustedCapital
from app.schemas.cost import (
    CostCreate, CostInDB, CostUpdate,
    CostAllocationCreate, BudgetCreate, BudgetInDB,
    SupplierCreate, SupplierInDB,
    CostSummary, CostAnalytics
)
from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission
from app.services.cost import CostService
from app.services.supplier_credit_service import SupplierCreditService

router = APIRouter(prefix="/costs", tags=["Costs"])
logger = logging.getLogger(__name__)


# ==============================================
# FONCTIONS UTILITAIRES
# ==============================================

def generate_cost_reference(db: Session, tenant_id: UUID, category: str) -> str:
    """Génère une référence unique pour un coût"""
    today = date.today()
    prefix = f"EXP-{today.strftime('%Y%m')}"
    
    # Compter les coûts du mois
    count = db.query(Cost).filter(
        Cost.tenant_id == tenant_id,
        Cost.reference.like(f"{prefix}%")
    ).count() + 1
    
    return f"{prefix}-{count:04d}"


def calculate_next_payment_date(current_date: date, frequency: str) -> date:
    """Calcule la prochaine date de paiement"""
    if frequency == "quotidien":
        return current_date + timedelta(days=1)
    elif frequency == "hebdomadaire":
        return current_date + timedelta(days=7)
    elif frequency == "mensuel":
        year = current_date.year
        month = current_date.month + 1
        if month > 12:
            month = 1
            year += 1
        return date(year, month, min(current_date.day, 28))
    elif frequency == "trimestriel":
        return current_date + timedelta(days=90)
    elif frequency == "semestriel":
        return current_date + timedelta(days=180)
    elif frequency == "annuel":
        return date(current_date.year + 1, current_date.month, current_date.day)
    else:
        return current_date


def get_period_dates(period: str, year: Optional[int] = None, month: Optional[int] = None):
    """Retourne les dates de début et fin pour une période donnée"""
    today = date.today()
    
    if period == "day":
        start_date = today
        end_date = today
    elif period == "week":
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
    elif period == "month":
        if year and month:
            start_date = date(year, month, 1)
            if month == 12:
                end_date = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = date(year, month + 1, 1) - timedelta(days=1)
        else:
            start_date = date(today.year, today.month, 1)
            if today.month == 12:
                end_date = date(today.year + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = date(today.year, today.month + 1, 1) - timedelta(days=1)
    elif period == "quarter":
        quarter = (today.month - 1) // 3
        start_month = quarter * 3 + 1
        start_date = date(today.year, start_month, 1)
        if start_month == 10:
            end_date = date(today.year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(today.year, start_month + 3, 1) - timedelta(days=1)
    elif period == "year":
        if year:
            start_date = date(year, 1, 1)
            end_date = date(year, 12, 31)
        else:
            start_date = date(today.year, 1, 1)
            end_date = date(today.year, 12, 31)
    else:  # "all"
        start_date = date(2000, 1, 1)
        end_date = date(2100, 12, 31)
    
    return start_date, end_date


# ==============================================
# ENDPOINTS COÛTS
# ==============================================

@router.post("/", response_model=CostInDB)
@require_permission("costs_manage")
def create_cost(
    cost_data: CostCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Crée un nouveau coût avec support du crédit fournisseur
    """
    try:
        # Calculer le montant total
        total_amount = cost_data.amount + cost_data.tax_amount
        
        # Générer la référence
        reference = generate_cost_reference(db, current_tenant.id, cost_data.category.value)
        
        # Créer le coût
        cost = Cost(
            tenant_id=current_tenant.id,
            reference=reference,
            category=cost_data.category.value,
            subcategory=cost_data.subcategory,
            amount=cost_data.amount,
            tax_amount=cost_data.tax_amount,
            total_amount=total_amount,
            currency=current_tenant.currency or "CDF",
            exchange_rate=cost_data.exchange_rate or Decimal('1.0'),
            description=cost_data.description,
            payment_date=cost_data.payment_date,
            due_date=cost_data.due_date,
            payment_method=cost_data.payment_method.value,
            is_paid=cost_data.is_paid,
            invoice_number=cost_data.invoice_number,
            supplier_id=cost_data.supplier_id,
            is_recurring=cost_data.is_recurring,
            frequency=cost_data.frequency.value if cost_data.frequency else "unique",
            recurring_until=cost_data.recurring_until,
            budget_id=cost_data.budget_id,
            notes=cost_data.notes,
            tags=cost_data.tags or [],
            justification=cost_data.justification,
            status="paid" if cost_data.is_paid else "draft",
            created_by=current_user.id,
            approved_by=current_user.id if cost_data.is_paid else None,
            approval_date=datetime.utcnow() if cost_data.is_paid else None
        )
        
        db.add(cost)
        db.flush()
        
        # Si récurrent, planifier le prochain paiement
        if cost_data.is_recurring and cost_data.frequency and cost_data.frequency.value != "unique":
            next_date = calculate_next_payment_date(
                cost_data.payment_date,
                cost_data.frequency.value
            )
            cost.next_payment_date = next_date
        
        # Mettre à jour le budget si spécifié
        if cost_data.budget_id:
            budget = db.query(Budget).filter(
                Budget.id == cost_data.budget_id,
                Budget.tenant_id == current_tenant.id
            ).first()
            if budget:
                budget.update_spent_amount(db)
        
        # Si c'est un achat à crédit (via fournisseur), créer l'entrée de crédit
        if cost_data.supplier_id and not cost_data.is_paid and cost_data.due_date:
            # Vérifier si le fournisseur a une configuration de crédit
            credit_config = db.query(SupplierCreditConfig).filter(
                SupplierCreditConfig.supplier_id == cost_data.supplier_id,
                SupplierCreditConfig.is_active == True
            ).first()
            
            if credit_config:
                # Créer ou récupérer la dette du fournisseur
                debt = db.query(SupplierDebt).filter(
                    SupplierDebt.supplier_id == cost_data.supplier_id,
                    SupplierDebt.tenant_id == current_tenant.id
                ).first()
                
                if not debt:
                    debt = SupplierDebt(
                        tenant_id=current_tenant.id,
                        supplier_id=cost_data.supplier_id,
                        total_credit_amount=Decimal('0'),
                        total_repaid_amount=Decimal('0'),
                        current_debt=Decimal('0'),
                        first_credit_date=cost_data.due_date,
                        status=CreditStatus.ACTIVE.value
                    )
                    db.add(debt)
                    db.flush()
                
                # Mettre à jour la dette
                debt.total_credit_amount += total_amount
                debt.current_debt += total_amount
                debt.update_debt()
                
                # Créer le PurchaseCredit
                purchase_credit = PurchaseCredit(
                    tenant_id=current_tenant.id,
                    purchase_id=None,  # Sera lié à l'achat si nécessaire
                    supplier_id=cost_data.supplier_id,
                    config_id=credit_config.id,
                    debt_id=debt.id,
                    credit_amount=total_amount,
                    repaid_amount=Decimal('0'),
                    remaining_amount=total_amount,
                    interest_rate_applied=credit_config.interest_rate,
                    payment_frequency=credit_config.payment_frequency,
                    repayment_percentage=credit_config.repayment_percentage_of_sale,
                    due_date=cost_data.due_date,
                    status=CreditStatus.ACTIVE.value,
                    created_by=current_user.id,
                    notes=f"Coût créé: {cost.description}"
                )
                db.add(purchase_credit)
                
                # Créer la transaction de crédit
                transaction = SupplierCreditTransaction(
                    tenant_id=current_tenant.id,
                    debt_id=debt.id,
                    supplier_id=cost_data.supplier_id,
                    transaction_type="credit_purchase",
                    amount=total_amount,
                    balance_before=debt.current_debt - total_amount,
                    balance_after=debt.current_debt,
                    purchase_credit_id=purchase_credit.id,
                    description=f"Achat à crédit: {cost.description}",
                    transaction_date=cost_data.due_date,
                    created_by=current_user.id
                )
                db.add(transaction)
        
        db.commit()
        db.refresh(cost)
        
        logger.info(f"Coût créé: {cost.reference} - {total_amount} {cost.currency}")
        
        return cost
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création du coût: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la création du coût: {str(e)}"
        )


@router.get("/", response_model=List[CostInDB])
@require_permission("costs_view")
def list_costs(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    category: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    supplier_id: Optional[UUID] = None,
    budget_id: Optional[UUID] = None,
    is_paid: Optional[bool] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    search: Optional[str] = None,
    status: Optional[str] = None
):
    """
    Liste les coûts avec filtres avancés
    """
    query = db.query(Cost).filter(Cost.tenant_id == current_tenant.id)
    
    # Appliquer les filtres
    if category:
        query = query.filter(Cost.category == category)
    
    if start_date:
        query = query.filter(Cost.payment_date >= start_date)
    
    if end_date:
        query = query.filter(Cost.payment_date <= end_date)
    
    if supplier_id:
        query = query.filter(Cost.supplier_id == supplier_id)
    
    if budget_id:
        query = query.filter(Cost.budget_id == budget_id)
    
    if is_paid is not None:
        query = query.filter(Cost.is_paid == is_paid)
    
    if min_amount is not None:
        query = query.filter(Cost.total_amount >= min_amount)
    
    if max_amount is not None:
        query = query.filter(Cost.total_amount <= max_amount)
    
    if status:
        query = query.filter(Cost.status == status)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Cost.description.ilike(search_term),
                Cost.notes.ilike(search_term),
                Cost.invoice_number.ilike(search_term),
                Cost.reference.ilike(search_term)
            )
        )
    
    # Trier par date de paiement décroissante
    costs = query.order_by(
        Cost.payment_date.desc(),
        Cost.created_at.desc()
    ).offset(skip).limit(limit).all()
    
    return costs


@router.get("/{cost_id}", response_model=CostInDB)
@require_permission("costs_view")
def get_cost(
    cost_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Récupère un coût spécifique"""
    cost = db.query(Cost).filter(
        Cost.id == cost_id,
        Cost.tenant_id == current_tenant.id
    ).first()
    
    if not cost:
        raise HTTPException(status_code=404, detail="Coût non trouvé")
    
    return cost


@router.put("/{cost_id}", response_model=CostInDB)
@require_permission("costs_manage")
def update_cost(
    cost_id: UUID,
    cost_update: CostUpdate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Met à jour un coût"""
    cost = db.query(Cost).filter(
        Cost.id == cost_id,
        Cost.tenant_id == current_tenant.id
    ).first()
    
    if not cost:
        raise HTTPException(status_code=404, detail="Coût non trouvé")
    
    update_data = cost_update.dict(exclude_unset=True)
    
    for field, value in update_data.items():
        setattr(cost, field, value)
    
    if 'amount' in update_data or 'tax_amount' in update_data:
        cost.calculate_totals()
    
    cost.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(cost)
    
    return cost


@router.delete("/{cost_id}")
@require_permission("costs_manage")
def delete_cost(
    cost_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Supprime un coût"""
    cost = db.query(Cost).filter(
        Cost.id == cost_id,
        Cost.tenant_id == current_tenant.id
    ).first()
    
    if not cost:
        raise HTTPException(status_code=404, detail="Coût non trouvé")
    
    db.delete(cost)
    db.commit()
    
    return {"message": "Coût supprimé avec succès"}


@router.post("/{cost_id}/pay")
@require_permission("costs_manage")
def mark_cost_as_paid(
    cost_id: UUID,
    payment_date: Optional[date] = None,
    payment_reference: Optional[str] = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Marque un coût comme payé"""
    cost = db.query(Cost).filter(
        Cost.id == cost_id,
        Cost.tenant_id == current_tenant.id
    ).first()
    
    if not cost:
        raise HTTPException(status_code=404, detail="Coût non trouvé")
    
    cost.mark_as_paid(current_user.id, payment_date)
    if payment_reference:
        cost.payment_reference = payment_reference
    
    db.commit()
    
    return {"message": f"Coût {cost.reference} marqué comme payé"}


# ==============================================
# ENDPOINTS RÉSUMÉS ET ANALYSES
# ==============================================

@router.get("/summary", response_model=CostSummary)
@require_permission("costs_view")
def get_cost_summary(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    period: str = Query("month", pattern="^(day|week|month|quarter|year|all)$"),
    year: Optional[int] = None,
    month: Optional[int] = None
):
    """Obtient un résumé des coûts"""
    # Déterminer la période
    start_date, end_date = get_period_dates(period, year, month)
    
    # Requête de base
    query = db.query(Cost).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date,
        Cost.status == "paid"
    )
    
    # Total des coûts
    total_costs = db.query(func.sum(Cost.total_amount)).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date,
        Cost.is_paid == True
    ).scalar() or 0.0
    
    # Par catégorie
    category_query = db.query(
        Cost.category,
        func.sum(Cost.total_amount).label('total')
    ).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date,
        Cost.is_paid == True
    ).group_by(Cost.category)
    
    by_category = {
        category: float(total) for category, total in category_query.all()
    }
    
    # Par mois
    monthly_query = db.query(
        extract('year', Cost.payment_date).label('year'),
        extract('month', Cost.payment_date).label('month'),
        func.sum(Cost.total_amount).label('total')
    ).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date - timedelta(days=365),
        Cost.payment_date <= end_date,
        Cost.is_paid == True
    ).group_by('year', 'month').order_by('year', 'month')
    
    by_month = {}
    for year_val, month_val, total in monthly_query.all():
        key = f"{int(year_val)}-{int(month_val):02d}"
        by_month[key] = float(total)
    
    # Coûts les plus élevés
    top_costs = db.query(Cost).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date
    ).order_by(
        Cost.total_amount.desc()
    ).limit(10).all()
    
    formatted_top_costs = []
    for cost in top_costs:
        formatted_top_costs.append({
            "id": str(cost.id),
            "reference": cost.reference,
            "description": cost.description,
            "category": cost.category,
            "amount": float(cost.total_amount),
            "date": cost.payment_date.isoformat() if cost.payment_date else None,
            "supplier": cost.supplier.name if cost.supplier else None
        })
    
    # Variance avec le budget
    budget_variance = {}
    budgets = db.query(Budget).filter(
        Budget.tenant_id == current_tenant.id,
        Budget.start_date <= end_date,
        Budget.end_date >= start_date,
        Budget.is_active == True
    ).all()
    
    for budget in budgets:
        budget.update_spent_amount(db)
        budget_variance[budget.name] = {
            "allocated": float(budget.allocated_amount),
            "spent": float(budget.spent_amount),
            "remaining": float(budget.remaining_amount),
            "percentage": budget.spending_percentage
        }
    
    # Calcul de la moyenne mensuelle
    month_count = max(1, (end_date - start_date).days / 30)
    average_monthly = float(total_costs) / month_count
    
    return CostSummary(
        period=period,
        total_costs=float(total_costs),
        by_category=by_category,
        by_month=by_month,
        average_monthly=average_monthly,
        top_costs=formatted_top_costs,
        budget_variance=budget_variance
    )


@router.get("/analytics", response_model=CostAnalytics)
@require_permission("costs_view")
def get_cost_analytics(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    months: int = Query(12, ge=1, le=60)
):
    """Obtient des analyses avancées sur les coûts"""
    end_date = date.today()
    start_date = end_date - timedelta(days=months * 30)
    
    # Tendances mensuelles
    monthly_query = db.query(
        extract('year', Cost.payment_date).label('year'),
        extract('month', Cost.payment_date).label('month'),
        func.sum(Cost.total_amount).label('total')
    ).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date,
        Cost.is_paid == True
    ).group_by('year', 'month').order_by('year', 'month')
    
    monthly_trend = []
    for year_val, month_val, total in monthly_query.all():
        monthly_trend.append({
            "period": f"{int(year_val)}-{int(month_val):02d}",
            "amount": float(total),
            "year": int(year_val),
            "month": int(month_val)
        })
    
    # Distribution par catégorie
    category_query = db.query(
        Cost.category,
        func.sum(Cost.total_amount).label('total'),
        func.count(Cost.id).label('count')
    ).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date,
        Cost.is_paid == True
    ).group_by(Cost.category)
    
    total_all = sum(float(total) for _, total, _ in category_query.all())
    category_distribution = []
    for category, total, count in category_query.all():
        category_distribution.append({
            "category": category,
            "amount": float(total),
            "count": count,
            "percentage": float(total) / total_all * 100 if total_all > 0 else 0
        })
    
    # Analyse des fournisseurs
    supplier_query = db.query(
        Supplier.name,
        func.sum(Cost.total_amount).label('total'),
        func.count(Cost.id).label('count')
    ).join(
        Cost, Supplier.id == Cost.supplier_id
    ).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date,
        Cost.is_paid == True
    ).group_by(Supplier.name).order_by(func.sum(Cost.total_amount).desc()).limit(10)
    
    supplier_analysis = []
    for name, total, count in supplier_query.all():
        supplier_analysis.append({
            "name": name,
            "amount": float(total),
            "count": count
        })
    
    # Analyse de variance
    total_this_year = db.query(func.sum(Cost.total_amount)).filter(
        Cost.tenant_id == current_tenant.id,
        extract('year', Cost.payment_date) == end_date.year,
        Cost.is_paid == True
    ).scalar() or 0.0
    
    total_last_year = db.query(func.sum(Cost.total_amount)).filter(
        Cost.tenant_id == current_tenant.id,
        extract('year', Cost.payment_date) == end_date.year - 1,
        Cost.is_paid == True
    ).scalar() or 0.0
    
    variance = float(total_this_year) - float(total_last_year)
    variance_percentage = (variance / float(total_last_year) * 100) if float(total_last_year) > 0 else 0
    
    # Recommandations
    recommendations = []
    
    if variance_percentage > 20:
        recommendations.append("Augmentation significative des coûts cette année. Examiner les dépenses.")
    
    if category_distribution:
        top_category = max(category_distribution, key=lambda x: x['amount'])
        if top_category['percentage'] > 50:
            recommendations.append(f"Concentration élevée dans {top_category['category']}. Diversifier les dépenses.")
    
    # Analyse des coûts récurrents
    recurring_costs = db.query(Cost).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.is_recurring == True,
        Cost.is_active == True
    ).count()
    
    if recurring_costs > 10:
        recommendations.append(f"Vous avez {recurring_costs} coûts récurrents. Réviser ceux qui ne sont plus nécessaires.")
    
    return CostAnalytics(
        monthly_trend=monthly_trend,
        category_distribution=category_distribution,
        supplier_analysis=supplier_analysis,
        variance_analysis={
            "current_year": float(total_this_year),
            "last_year": float(total_last_year),
            "variance": variance,
            "variance_percentage": variance_percentage
        },
        recommendations=recommendations
    )


# ==============================================
# ENDPOINTS BUDGETS
# ==============================================

@router.post("/budgets", response_model=BudgetInDB)
@require_permission("costs_manage")
def create_budget(
    budget_data: BudgetCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Crée un nouveau budget"""
    try:
        # Générer le code du budget
        code = f"BUD-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        
        # Vérifier les chevauchements
        overlapping_budgets = db.query(Budget).filter(
            Budget.tenant_id == current_tenant.id,
            Budget.category == budget_data.category.value,
            Budget.is_active == True,
            or_(
                and_(
                    Budget.start_date <= budget_data.end_date,
                    Budget.end_date >= budget_data.start_date
                )
            )
        ).all()
        
        if overlapping_budgets:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Un budget actif existe déjà pour cette période et catégorie"
            )
        
        # Créer le budget
        budget = Budget(
            tenant_id=current_tenant.id,
            name=budget_data.name,
            code=code,
            description=budget_data.description,
            category=budget_data.category.value,
            subcategory=budget_data.subcategory,
            period_type=budget_data.period_type.value,
            start_date=budget_data.start_date,
            end_date=budget_data.end_date,
            allocated_amount=budget_data.allocated_amount,
            remaining_amount=budget_data.allocated_amount,
            warning_threshold=budget_data.warning_threshold or Decimal('80.0'),
            critical_threshold=budget_data.critical_threshold or Decimal('95.0'),
            owner_id=current_user.id,
            notes=budget_data.notes,
            budget_metadata=budget_data.budget_metadata or {}
        )
        
        db.add(budget)
        db.commit()
        db.refresh(budget)
        
        logger.info(f"Budget créé: {budget.code} - {budget.name} - {budget.allocated_amount}")
        
        return budget
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création du budget: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la création du budget: {str(e)}"
        )


@router.get("/budgets", response_model=List[BudgetInDB])
@require_permission("costs_view")
def list_budgets(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    category: Optional[str] = None,
    is_active: Optional[bool] = None,
    year: Optional[int] = None
):
    """Liste les budgets"""
    query = db.query(Budget).filter(Budget.tenant_id == current_tenant.id)
    
    if category:
        query = query.filter(Budget.category == category)
    
    if is_active is not None:
        query = query.filter(Budget.is_active == is_active)
    
    if year:
        query = query.filter(
            extract('year', Budget.start_date) == year
        )
    
    budgets = query.order_by(
        Budget.start_date.desc(),
        Budget.created_at.desc()
    ).all()
    
    # Mettre à jour les montants dépensés
    for budget in budgets:
        budget.update_spent_amount(db)
    
    return budgets


@router.get("/budgets/{budget_id}")
@require_permission("costs_view")
def get_budget(
    budget_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Récupère un budget spécifique"""
    budget = db.query(Budget).filter(
        Budget.id == budget_id,
        Budget.tenant_id == current_tenant.id
    ).first()
    
    if not budget:
        raise HTTPException(status_code=404, detail="Budget non trouvé")
    
    budget.update_spent_amount(db)
    
    return budget


@router.get("/budgets/{budget_id}/alerts")
@require_permission("costs_view")
def get_budget_alerts(
    budget_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Récupère les alertes pour un budget"""
    budget = db.query(Budget).filter(
        Budget.id == budget_id,
        Budget.tenant_id == current_tenant.id
    ).first()
    
    if not budget:
        raise HTTPException(status_code=404, detail="Budget non trouvé")
    
    budget.update_spent_amount(db)
    
    alerts = []
    percentage = budget.spending_percentage
    
    if percentage >= float(budget.critical_threshold):
        alerts.append({
            "level": "critical",
            "message": f"Budget dépassé à {percentage:.1f}%",
            "percentage": percentage,
            "threshold": float(budget.critical_threshold)
        })
    elif percentage >= float(budget.warning_threshold):
        alerts.append({
            "level": "warning",
            "message": f"Budget approche de la limite: {percentage:.1f}%",
            "percentage": percentage,
            "threshold": float(budget.warning_threshold)
        })
    
    return {
        "budget": budget.to_dict(),
        "alerts": alerts,
        "spent_percentage": percentage,
        "days_remaining": budget.days_remaining
    }


@router.put("/budgets/{budget_id}/close")
@require_permission("costs_manage")
def close_budget(
    budget_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Ferme un budget"""
    budget = db.query(Budget).filter(
        Budget.id == budget_id,
        Budget.tenant_id == current_tenant.id
    ).first()
    
    if not budget:
        raise HTTPException(status_code=404, detail="Budget non trouvé")
    
    budget.close_budget()
    db.commit()
    
    return {"message": f"Budget {budget.code} fermé avec succès"}


# ==============================================
# ENDPOINTS FOURNISSEURS
# ==============================================

@router.post("/suppliers", response_model=SupplierInDB)
@require_permission("costs_manage")
def create_supplier(
    supplier_data: SupplierCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Crée un nouveau fournisseur"""
    try:
        # Générer le code du fournisseur
        code = f"SUP-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
        
        supplier = Supplier(
            tenant_id=current_tenant.id,
            code=code,
            name=supplier_data.name,
            company_name=supplier_data.company_name,
            type_supplier=supplier_data.type_supplier,
            tax_id=supplier_data.tax_id,
            rccm=supplier_data.rccm,
            id_nat=supplier_data.id_nat,
            email=supplier_data.email,
            phone=supplier_data.phone,
            phone_secondary=supplier_data.phone_secondary,
            address=supplier_data.address,
            city=supplier_data.city,
            province=supplier_data.province,
            country=supplier_data.country or "RDC",
            bank_name=supplier_data.bank_name,
            bank_account=supplier_data.bank_account,
            bank_swift=supplier_data.bank_swift,
            payment_terms=supplier_data.payment_terms,
            categories=supplier_data.categories or [],
            website=supplier_data.website,
            contact_person=supplier_data.contact_person,
            notes=supplier_data.notes,
            status="active"
        )
        
        db.add(supplier)
        db.commit()
        db.refresh(supplier)
        
        logger.info(f"Fournisseur créé: {supplier.code} - {supplier.name}")
        
        return supplier
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création du fournisseur: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la création du fournisseur: {str(e)}"
        )


@router.get("/suppliers", response_model=List[SupplierInDB])
@require_permission("costs_view")
def list_suppliers(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = None,
    status: Optional[str] = None,
    is_preferred: Optional[bool] = None
):
    """Liste les fournisseurs"""
    query = db.query(Supplier).filter(Supplier.tenant_id == current_tenant.id)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Supplier.name.ilike(search_term),
                Supplier.company_name.ilike(search_term),
                Supplier.code.ilike(search_term),
                Supplier.email.ilike(search_term)
            )
        )
    
    if status:
        query = query.filter(Supplier.status == status)
    
    if is_preferred is not None:
        query = query.filter(Supplier.is_preferred == is_preferred)
    
    suppliers = query.order_by(Supplier.name).offset(skip).limit(limit).all()
    
    return suppliers


@router.get("/suppliers/{supplier_id}", response_model=SupplierInDB)
@require_permission("costs_view")
def get_supplier(
    supplier_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Récupère un fournisseur spécifique"""
    supplier = db.query(Supplier).filter(
        Supplier.id == supplier_id,
        Supplier.tenant_id == current_tenant.id
    ).first()
    
    if not supplier:
        raise HTTPException(status_code=404, detail="Fournisseur non trouvé")
    
    return supplier


@router.get("/suppliers/{supplier_id}/debt")
@require_permission("costs_view")
def get_supplier_debt(
    supplier_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Récupère la dette d'un fournisseur"""
    supplier = db.query(Supplier).filter(
        Supplier.id == supplier_id,
        Supplier.tenant_id == current_tenant.id
    ).first()
    
    if not supplier:
        raise HTTPException(status_code=404, detail="Fournisseur non trouvé")
    
    debt = db.query(SupplierDebt).filter(
        SupplierDebt.supplier_id == supplier_id,
        SupplierDebt.tenant_id == current_tenant.id
    ).first()
    
    if not debt:
        return {
            "supplier_id": str(supplier_id),
            "supplier_name": supplier.name,
            "total_credit_amount": 0,
            "total_repaid_amount": 0,
            "current_debt": 0,
            "status": "no_debt"
        }
    
    return {
        "supplier_id": str(supplier_id),
        "supplier_name": supplier.name,
        "total_credit_amount": float(debt.total_credit_amount),
        "total_repaid_amount": float(debt.total_repaid_amount),
        "current_debt": float(debt.current_debt),
        "accrued_interest": float(debt.accrued_interest),
        "late_fees": float(debt.late_fees),
        "status": debt.status,
        "debt_ratio": debt.debt_ratio
    }


# ==============================================
# ENDPOINTS IMPORT/EXPORT
# ==============================================

@router.post("/import")
@require_permission("costs_manage")
async def import_costs(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Importe des coûts depuis un fichier Excel ou CSV"""
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format de fichier non supporté. Utilisez Excel ou CSV."
        )
    
    try:
        # Lire le fichier
        contents = await file.read()
        
        if file.filename.endswith('.csv'):
            df = pd.read_csv(BytesIO(contents))
        else:
            df = pd.read_excel(BytesIO(contents))
        
        # Valider les colonnes requises
        required_columns = ['category', 'amount', 'description', 'payment_date']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Colonnes manquantes: {', '.join(missing_columns)}"
            )
        
        imported_count = 0
        errors = []
        
        for index, row in df.iterrows():
            try:
                # Convertir la date
                payment_date = pd.to_datetime(row['payment_date']).date()
                
                # Générer la référence
                ref_date = payment_date.strftime('%Y%m')
                count = db.query(Cost).filter(
                    Cost.tenant_id == current_tenant.id,
                    Cost.reference.like(f"EXP-{ref_date}%")
                ).count() + imported_count + 1
                reference = f"EXP-{ref_date}-{count:04d}"
                
                # Créer le coût
                cost = Cost(
                    tenant_id=current_tenant.id,
                    reference=reference,
                    category=row.get('category', 'diverse'),
                    amount=float(row['amount']),
                    tax_amount=float(row.get('tax_amount', 0)),
                    total_amount=float(row['amount']) + float(row.get('tax_amount', 0)),
                    currency=row.get('currency', current_tenant.currency or 'CDF'),
                    description=row['description'],
                    payment_date=payment_date,
                    payment_method=row.get('payment_method', 'cash'),
                    is_paid=bool(row.get('is_paid', True)),
                    invoice_number=row.get('invoice_number'),
                    notes=row.get('notes'),
                    tags=row.get('tags', '').split(',') if pd.notna(row.get('tags')) else [],
                    status="paid" if bool(row.get('is_paid', True)) else "draft",
                    created_by=current_user.id,
                    approved_by=current_user.id if bool(row.get('is_paid', True)) else None
                )
                
                # Gérer le fournisseur
                if pd.notna(row.get('supplier')):
                    supplier = db.query(Supplier).filter(
                        Supplier.tenant_id == current_tenant.id,
                        Supplier.name == row['supplier']
                    ).first()
                    
                    if not supplier:
                        supplier_code = f"SUP-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
                        supplier = Supplier(
                            tenant_id=current_tenant.id,
                            code=supplier_code,
                            name=row['supplier'],
                            status="active"
                        )
                        db.add(supplier)
                        db.flush()
                    
                    cost.supplier_id = supplier.id
                
                db.add(cost)
                imported_count += 1
                
                if imported_count % 100 == 0:
                    db.commit()
                    
            except Exception as e:
                errors.append(f"Ligne {index + 2}: {str(e)}")
        
        db.commit()
        
        return {
            "message": f"{imported_count} coûts importés avec succès",
            "imported_count": imported_count,
            "errors": errors if errors else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de l'importation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de l'importation: {str(e)}"
        )


@router.get("/export")
@require_permission("costs_view")
def export_costs(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    format: str = Query("excel", pattern="^(excel|csv|pdf)$")
):
    """Exporte les coûts dans différents formats"""
    # Ici, vous pouvez implémenter l'export asynchrone
    return {
        "message": "Export en cours de développement",
        "format": format,
        "start_date": start_date,
        "end_date": end_date
    }


# ==============================================
# ENDPOINTS CATÉGORIES
# ==============================================

@router.post("/categories")
@require_permission("costs_manage")
def create_cost_category(
    name: str,
    description: Optional[str] = None,
    icon: Optional[str] = None,
    color: Optional[str] = None,
    parent_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Crée une nouvelle catégorie de coûts"""
    category = Category(
        tenant_id=current_tenant.id,
        name=name,
        description=description,
        icon=icon,
        color=color,
        parent_id=parent_id,
        is_active=True
    )
    
    db.add(category)
    db.commit()
    db.refresh(category)
    
    return category


@router.get("/categories")
@require_permission("costs_view")
def get_cost_categories(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    is_active: Optional[bool] = True
):
    """Récupère toutes les catégories de coûts"""
    query = db.query(Category).filter(Category.tenant_id == current_tenant.id)
    
    if is_active is not None:
        query = query.filter(Category.is_active == is_active)
    
    categories = query.order_by(Category.name).all()
    
    return categories
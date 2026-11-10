# app/services/cost.py
"""
Service de gestion des coûts, budgets et fournisseurs
Avec intégration du crédit fournisseur et de la comptabilité SYSCOHADA
"""

from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from uuid import UUID
from decimal import Decimal
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, and_, or_, desc
from sqlalchemy.sql import extract

from app.models.cost import Cost, Budget, Supplier, CostCategory, PaymentMethod, BudgetPeriod
from app.models.department import Department
from app.models.project import Project
from app.models.tenant import Tenant
from app.models.user import User
from app.models.category import Category
from app.models.supplier_credit import (
    SupplierDebt, PurchaseCredit, SupplierCreditConfig, 
    CreditStatus, PaymentFrequency
)
from app.models.capital import Capital, AdjustedCapital


class CostService:
    """Service pour la gestion des coûts, budgets et fournisseurs"""

    def __init__(self, db: Session, tenant_id: UUID = None):
        self.db = db
        self.tenant_id = tenant_id

    # ==============================================
    # GESTION DES COÛTS
    # ==============================================

    def create_cost(
        self,
        tenant_id: UUID,
        user_id: UUID,
        amount: Decimal,
        category: str,
        description: str,
        payment_date: date,
        **kwargs
    ) -> Cost:
        """
        Crée un nouveau coût avec gestion automatique du crédit fournisseur
        """
        # Générer la référence
        reference = self._generate_cost_reference(tenant_id, category)
        
        # Calculer le total
        tax_amount = kwargs.get('tax_amount', Decimal('0'))
        total_amount = amount + tax_amount
        
        # Créer le coût
        cost = Cost(
            tenant_id=tenant_id,
            reference=reference,
            category=category,
            subcategory=kwargs.get('subcategory'),
            amount=amount,
            tax_amount=tax_amount,
            total_amount=total_amount,
            currency=kwargs.get('currency', 'CDF'),
            exchange_rate=kwargs.get('exchange_rate', Decimal('1.0')),
            description=description,
            payment_date=payment_date,
            due_date=kwargs.get('due_date'),
            payment_method=kwargs.get('payment_method', PaymentMethod.CASH.value),
            is_paid=kwargs.get('is_paid', True),
            invoice_number=kwargs.get('invoice_number'),
            supplier_id=kwargs.get('supplier_id'),
            is_recurring=kwargs.get('is_recurring', False),
            frequency=kwargs.get('frequency', 'unique'),
            recurring_until=kwargs.get('recurring_until'),
            budget_id=kwargs.get('budget_id'),
            notes=kwargs.get('notes'),
            tags=kwargs.get('tags', []),
            justification=kwargs.get('justification'),
            status="paid" if kwargs.get('is_paid', True) else "draft",
            created_by=user_id,
            approved_by=user_id if kwargs.get('is_paid', True) else None,
            approval_date=datetime.utcnow() if kwargs.get('is_paid', True) else None
        )
        
        self.db.add(cost)
        self.db.flush()
        
        # Gérer la récurrence
        if cost.is_recurring and cost.frequency != "unique":
            cost.next_payment_date = self._calculate_next_payment_date(
                payment_date, cost.frequency
            )
        
        # Mettre à jour le budget
        if cost.budget_id:
            self._update_budget_spent(cost.budget_id)
        
        # Si c'est un achat à crédit, créer les entrées de crédit
        if cost.supplier_id and not cost.is_paid and cost.due_date:
            self._create_credit_entry(cost, user_id)
        
        self.db.commit()
        self.db.refresh(cost)
        
        return cost

    def get_cost(self, cost_id: UUID, tenant_id: UUID = None) -> Optional[Cost]:
        """Récupère un coût par son ID"""
        query = self.db.query(Cost).filter(Cost.id == cost_id)
        if tenant_id or self.tenant_id:
            query = query.filter(Cost.tenant_id == (tenant_id or self.tenant_id))
        return query.first()

    def get_costs(
        self,
        tenant_id: UUID = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        category: Optional[str] = None,
        supplier_id: Optional[UUID] = None,
        is_paid: Optional[bool] = None,
        status: Optional[str] = None,
        min_amount: Optional[Decimal] = None,
        max_amount: Optional[Decimal] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[Cost]:
        """Récupère une liste de coûts avec filtres"""
        query = self.db.query(Cost).filter(
            Cost.tenant_id == (tenant_id or self.tenant_id)
        )
        
        if start_date:
            query = query.filter(Cost.payment_date >= start_date)
        
        if end_date:
            query = query.filter(Cost.payment_date <= end_date)
        
        if category:
            query = query.filter(Cost.category == category)
        
        if supplier_id:
            query = query.filter(Cost.supplier_id == supplier_id)
        
        if is_paid is not None:
            query = query.filter(Cost.is_paid == is_paid)
        
        if status:
            query = query.filter(Cost.status == status)
        
        if min_amount is not None:
            query = query.filter(Cost.total_amount >= min_amount)
        
        if max_amount is not None:
            query = query.filter(Cost.total_amount <= max_amount)
        
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Cost.description.ilike(search_term),
                    Cost.reference.ilike(search_term),
                    Cost.invoice_number.ilike(search_term),
                    Cost.notes.ilike(search_term)
                )
            )
        
        return query.order_by(desc(Cost.payment_date)).offset(skip).limit(limit).all()

    def update_cost(self, cost_id: UUID, update_data: Dict[str, Any]) -> Optional[Cost]:
        """Met à jour un coût"""
        cost = self.get_cost(cost_id)
        if not cost:
            return None
        
        for field, value in update_data.items():
            if hasattr(cost, field):
                setattr(cost, field, value)
        
        if 'amount' in update_data or 'tax_amount' in update_data:
            cost.calculate_totals()
        
        cost.updated_at = datetime.utcnow()
        
        self.db.commit()
        self.db.refresh(cost)
        
        return cost

    def delete_cost(self, cost_id: UUID) -> bool:
        """Supprime un coût"""
        cost = self.get_cost(cost_id)
        if not cost:
            return False
        
        self.db.delete(cost)
        self.db.commit()
        
        return True

    def mark_as_paid(self, cost_id: UUID, user_id: UUID, payment_date: Optional[date] = None) -> Optional[Cost]:
        """Marque un coût comme payé"""
        cost = self.get_cost(cost_id)
        if not cost:
            return None
        
        cost.mark_as_paid(user_id, payment_date)
        self.db.commit()
        self.db.refresh(cost)
        
        return cost

    # ==============================================
    # GESTION DES BUDGETS
    # ==============================================

    def create_budget(
        self,
        tenant_id: UUID,
        user_id: UUID,
        name: str,
        category: str,
        allocated_amount: Decimal,
        start_date: date,
        end_date: date,
        **kwargs
    ) -> Budget:
        """Crée un nouveau budget"""
        # Générer le code du budget
        code = f"BUD-{datetime.utcnow().strftime('%Y%m%d')}-{UUID(int=0).hex[:6].upper()}"
        
        # Vérifier les chevauchements
        overlapping = self._check_budget_overlap(tenant_id, category, start_date, end_date)
        if overlapping:
            raise ValueError("Un budget actif existe déjà pour cette période et catégorie")
        
        budget = Budget(
            tenant_id=tenant_id,
            name=name,
            code=code,
            description=kwargs.get('description'),
            category=category,
            subcategory=kwargs.get('subcategory'),
            period_type=kwargs.get('period_type', BudgetPeriod.MONTHLY.value),
            start_date=start_date,
            end_date=end_date,
            allocated_amount=allocated_amount,
            remaining_amount=allocated_amount,
            warning_threshold=kwargs.get('warning_threshold', Decimal('80.0')),
            critical_threshold=kwargs.get('critical_threshold', Decimal('95.0')),
            owner_id=user_id,
            notes=kwargs.get('notes'),
            budget_metadata=kwargs.get('budget_metadata', {})
        )
        
        self.db.add(budget)
        self.db.commit()
        self.db.refresh(budget)
        
        return budget

    def get_budgets(
        self,
        tenant_id: UUID = None,
        category: Optional[str] = None,
        is_active: Optional[bool] = None,
        year: Optional[int] = None
    ) -> List[Budget]:
        """Récupère une liste de budgets"""
        query = self.db.query(Budget).filter(
            Budget.tenant_id == (tenant_id or self.tenant_id)
        )
        
        if category:
            query = query.filter(Budget.category == category)
        
        if is_active is not None:
            query = query.filter(Budget.is_active == is_active)
        
        if year:
            query = query.filter(extract('year', Budget.start_date) == year)
        
        budgets = query.order_by(desc(Budget.start_date)).all()
        
        # Mettre à jour les montants dépensés
        for budget in budgets:
            budget.update_spent_amount(self.db)
        
        return budgets

    def get_budget_alerts(self, budget_id: UUID) -> Dict[str, Any]:
        """Récupère les alertes pour un budget"""
        budget = self.db.query(Budget).filter(Budget.id == budget_id).first()
        if not budget:
            return {"error": "Budget non trouvé"}
        
        budget.update_spent_amount(self.db)
        
        percentage = budget.spending_percentage
        alerts = []
        
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
            "budget_id": str(budget.id),
            "budget_name": budget.name,
            "spent_percentage": percentage,
            "days_remaining": budget.days_remaining,
            "alerts": alerts,
            "status": budget.alert_level
        }

    def close_budget(self, budget_id: UUID) -> Optional[Budget]:
        """Ferme un budget"""
        budget = self.db.query(Budget).filter(Budget.id == budget_id).first()
        if not budget:
            return None
        
        budget.close_budget()
        self.db.commit()
        self.db.refresh(budget)
        
        return budget

    # ==============================================
    # GESTION DES FOURNISSEURS
    # ==============================================

    def create_supplier(
        self,
        tenant_id: UUID,
        name: str,
        **kwargs
    ) -> Supplier:
        """Crée un nouveau fournisseur"""
        # Générer le code
        code = f"SUP-{datetime.utcnow().strftime('%Y%m%d')}-{UUID(int=0).hex[:4].upper()}"
        
        supplier = Supplier(
            tenant_id=tenant_id,
            code=code,
            name=name,
            company_name=kwargs.get('company_name'),
            type_supplier=kwargs.get('type_supplier', 'company'),
            tax_id=kwargs.get('tax_id'),
            rccm=kwargs.get('rccm'),
            id_nat=kwargs.get('id_nat'),
            email=kwargs.get('email'),
            phone=kwargs.get('phone'),
            phone_secondary=kwargs.get('phone_secondary'),
            address=kwargs.get('address'),
            city=kwargs.get('city'),
            province=kwargs.get('province'),
            country=kwargs.get('country', 'RDC'),
            bank_name=kwargs.get('bank_name'),
            bank_account=kwargs.get('bank_account'),
            bank_swift=kwargs.get('bank_swift'),
            payment_terms=kwargs.get('payment_terms', '30 days'),
            categories=kwargs.get('categories', []),
            website=kwargs.get('website'),
            contact_person=kwargs.get('contact_person'),
            notes=kwargs.get('notes'),
            is_preferred=kwargs.get('is_preferred', False),
            status=kwargs.get('status', 'active')
        )
        
        self.db.add(supplier)
        self.db.commit()
        self.db.refresh(supplier)
        
        return supplier

    def get_supplier(self, supplier_id: UUID) -> Optional[Supplier]:
        """Récupère un fournisseur par son ID"""
        return self.db.query(Supplier).filter(Supplier.id == supplier_id).first()

    def get_suppliers(
        self,
        tenant_id: UUID = None,
        search: Optional[str] = None,
        status: Optional[str] = None,
        is_preferred: Optional[bool] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[Supplier]:
        """Récupère une liste de fournisseurs"""
        query = self.db.query(Supplier).filter(
            Supplier.tenant_id == (tenant_id or self.tenant_id)
        )
        
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
        
        return query.order_by(Supplier.name).offset(skip).limit(limit).all()

    def get_supplier_debt(self, supplier_id: UUID) -> Dict[str, Any]:
        """Récupère la dette d'un fournisseur"""
        supplier = self.get_supplier(supplier_id)
        if not supplier:
            return {"error": "Fournisseur non trouvé"}
        
        debt = self.db.query(SupplierDebt).filter(
            SupplierDebt.supplier_id == supplier_id,
            SupplierDebt.tenant_id == (self.tenant_id or supplier.tenant_id)
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

    def update_supplier_rating(
        self,
        supplier_id: UUID,
        new_rating: float,
        reliability: Optional[float] = None,
        delivery: Optional[float] = None,
        quality: Optional[float] = None
    ) -> Optional[Supplier]:
        """Met à jour la note d'un fournisseur"""
        supplier = self.get_supplier(supplier_id)
        if not supplier:
            return None
        
        supplier.update_rating(new_rating, reliability, delivery, quality)
        self.db.commit()
        self.db.refresh(supplier)
        
        return supplier

    # ==============================================
    # RAPPORTS ET ANALYSES
    # ==============================================

    def generate_monthly_report(self, year: int, month: int, tenant_id: UUID = None) -> Dict[str, Any]:
        """Génère un rapport mensuel des coûts"""
        start_date = date(year, month, 1)
        if month < 12:
            end_date = date(year, month + 1, 1) - timedelta(days=1)
        else:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        
        costs = self.get_costs(
            tenant_id=tenant_id or self.tenant_id,
            start_date=start_date,
            end_date=end_date
        )
        
        total_amount = sum(float(c.total_amount) for c in costs)
        
        report = {
            "period": f"{year}-{month:02d}",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "total_costs": total_amount,
            "total_transactions": len(costs),
            "average_transaction": total_amount / len(costs) if costs else 0,
            "by_category": {},
            "by_supplier": {},
            "by_status": {},
            "budget_comparison": {}
        }
        
        # Analyse par catégorie
        for cost in costs:
            category = cost.category
            if category not in report["by_category"]:
                report["by_category"][category] = {"amount": 0.0, "count": 0}
            report["by_category"][category]["amount"] += float(cost.total_amount)
            report["by_category"][category]["count"] += 1
        
        # Analyse par fournisseur
        for cost in costs:
            if cost.supplier:
                supplier_name = cost.supplier.name
                if supplier_name not in report["by_supplier"]:
                    report["by_supplier"][supplier_name] = {"amount": 0.0, "count": 0}
                report["by_supplier"][supplier_name]["amount"] += float(cost.total_amount)
                report["by_supplier"][supplier_name]["count"] += 1
        
        # Analyse par statut
        for cost in costs:
            status = cost.status
            if status not in report["by_status"]:
                report["by_status"][status] = {"amount": 0.0, "count": 0}
            report["by_status"][status]["amount"] += float(cost.total_amount)
            report["by_status"][status]["count"] += 1
        
        # Comparaison avec les budgets
        budgets = self.get_budgets(tenant_id=tenant_id or self.tenant_id)
        for budget in budgets:
            if budget.start_date <= end_date and budget.end_date >= start_date:
                budget.update_spent_amount(self.db)
                report["budget_comparison"][budget.name] = {
                    "allocated": float(budget.allocated_amount),
                    "spent": float(budget.spent_amount),
                    "remaining": float(budget.remaining_amount),
                    "percentage": budget.spending_percentage
                }
        
        return report

    def get_cost_breakdown(
        self,
        start_date: date,
        end_date: date,
        tenant_id: UUID = None
    ) -> Dict[str, Any]:
        """Obtient une répartition détaillée des coûts"""
        costs = self.get_costs(
            tenant_id=tenant_id or self.tenant_id,
            start_date=start_date,
            end_date=end_date
        )
        
        total_amount = sum(float(c.total_amount) for c in costs)
        
        breakdown = {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": (end_date - start_date).days + 1
            },
            "total_amount": total_amount,
            "transaction_count": len(costs),
            "average_transaction": total_amount / len(costs) if costs else 0,
            "categories": {},
            "payment_methods": {},
            "monthly_trend": {},
            "top_costs": []
        }
        
        # Répartition par catégorie
        for cost in costs:
            category = cost.category
            if category not in breakdown["categories"]:
                breakdown["categories"][category] = {"amount": 0.0, "count": 0, "percentage": 0.0}
            breakdown["categories"][category]["amount"] += float(cost.total_amount)
            breakdown["categories"][category]["count"] += 1
        
        # Calculer les pourcentages
        for category in breakdown["categories"]:
            breakdown["categories"][category]["percentage"] = (
                breakdown["categories"][category]["amount"] / total_amount * 100
                if total_amount > 0 else 0
            )
        
        # Répartition par méthode de paiement
        for cost in costs:
            method = cost.payment_method
            if method not in breakdown["payment_methods"]:
                breakdown["payment_methods"][method] = {"amount": 0.0, "count": 0}
            breakdown["payment_methods"][method]["amount"] += float(cost.total_amount)
            breakdown["payment_methods"][method]["count"] += 1
        
        # Tendance mensuelle
        for cost in costs:
            month_key = cost.payment_date.strftime("%Y-%m")
            if month_key not in breakdown["monthly_trend"]:
                breakdown["monthly_trend"][month_key] = {"amount": 0.0, "count": 0}
            breakdown["monthly_trend"][month_key]["amount"] += float(cost.total_amount)
            breakdown["monthly_trend"][month_key]["count"] += 1
        
        # Top 5 des coûts
        sorted_costs = sorted(costs, key=lambda x: float(x.total_amount), reverse=True)[:5]
        breakdown["top_costs"] = [
            {
                "id": str(cost.id),
                "reference": cost.reference,
                "description": cost.description,
                "amount": float(cost.total_amount),
                "category": cost.category,
                "date": cost.payment_date.isoformat(),
                "supplier": cost.supplier.name if cost.supplier else None
            }
            for cost in sorted_costs
        ]
        
        return breakdown

    def compare_periods(
        self,
        period1_start: date,
        period1_end: date,
        period2_start: date,
        period2_end: date,
        tenant_id: UUID = None
    ) -> Dict[str, Any]:
        """Compare les coûts entre deux périodes"""
        tenant = tenant_id or self.tenant_id
        
        # Coûts période 1
        period1_total = self.db.query(func.sum(Cost.total_amount)).filter(
            Cost.tenant_id == tenant,
            Cost.payment_date >= period1_start,
            Cost.payment_date <= period1_end,
            Cost.is_paid == True
        ).scalar() or Decimal('0')
        
        # Coûts période 2
        period2_total = self.db.query(func.sum(Cost.total_amount)).filter(
            Cost.tenant_id == tenant,
            Cost.payment_date >= period2_start,
            Cost.payment_date <= period2_end,
            Cost.is_paid == True
        ).scalar() or Decimal('0')
        
        period1_float = float(period1_total)
        period2_float = float(period2_total)
        
        # Calculer la variation
        if period1_float > 0:
            variance = period2_float - period1_float
            variance_percentage = (variance / period1_float) * 100
        else:
            variance = period2_float
            variance_percentage = 100.0 if period2_float > 0 else 0.0
        
        # Analyse par catégorie
        period1_by_category = self.db.query(
            Cost.category,
            func.sum(Cost.total_amount).label('total')
        ).filter(
            Cost.tenant_id == tenant,
            Cost.payment_date >= period1_start,
            Cost.payment_date <= period1_end,
            Cost.is_paid == True
        ).group_by(Cost.category).all()
        
        period2_by_category = self.db.query(
            Cost.category,
            func.sum(Cost.total_amount).label('total')
        ).filter(
            Cost.tenant_id == tenant,
            Cost.payment_date >= period2_start,
            Cost.payment_date <= period2_end,
            Cost.is_paid == True
        ).group_by(Cost.category).all()
        
        category_comparison = {}
        
        # Ajouter les catégories de la période 1
        for category, total in period1_by_category:
            category_comparison[category] = {
                "period1": float(total),
                "period2": 0.0,
                "variance": 0.0,
                "variance_percentage": 0.0
            }
        
        # Ajouter/Comparer avec période 2
        for category, total in period2_by_category:
            if category in category_comparison:
                period1_val = category_comparison[category]["period1"]
                period2_val = float(total)
                category_comparison[category]["period2"] = period2_val
                if period1_val > 0:
                    variance_val = period2_val - period1_val
                    category_comparison[category]["variance"] = variance_val
                    category_comparison[category]["variance_percentage"] = (variance_val / period1_val) * 100
                else:
                    category_comparison[category]["variance"] = period2_val
                    category_comparison[category]["variance_percentage"] = 100.0
            else:
                category_comparison[category] = {
                    "period1": 0.0,
                    "period2": float(total),
                    "variance": float(total),
                    "variance_percentage": 100.0
                }
        
        return {
            "period1": {
                "start_date": period1_start.isoformat(),
                "end_date": period1_end.isoformat(),
                "total_costs": period1_float,
                "days": (period1_end - period1_start).days + 1
            },
            "period2": {
                "start_date": period2_start.isoformat(),
                "end_date": period2_end.isoformat(),
                "total_costs": period2_float,
                "days": (period2_end - period2_start).days + 1
            },
            "comparison": {
                "absolute_variance": variance,
                "percentage_variance": variance_percentage,
                "trend": "increase" if variance > 0 else "decrease" if variance < 0 else "stable"
            },
            "category_comparison": category_comparison
        }

    def predict_future_costs(self, months: int = 6, tenant_id: UUID = None) -> List[Dict[str, Any]]:
        """Prédit les coûts futurs basés sur l'historique"""
        end_date = date.today()
        start_date = end_date - timedelta(days=months * 30)
        
        tenant = tenant_id or self.tenant_id
        
        # Récupérer les coûts historiques par mois
        monthly_totals = self.db.query(
            extract('year', Cost.payment_date).label('year'),
            extract('month', Cost.payment_date).label('month'),
            func.sum(Cost.total_amount).label('total')
        ).filter(
            Cost.tenant_id == tenant,
            Cost.payment_date >= start_date,
            Cost.payment_date <= end_date,
            Cost.is_paid == True
        ).group_by('year', 'month').all()
        
        totals = [float(total) for _, _, total in monthly_totals]
        
        # Calculer la moyenne et la tendance
        if totals:
            average_monthly = sum(totals) / len(totals)
            # Calculer la tendance (régression linéaire simple)
            if len(totals) >= 2:
                x = list(range(len(totals)))
                slope = self._calculate_slope(x, totals)
                trend_factor = 1 + (slope / average_monthly) if average_monthly > 0 else 1
            else:
                trend_factor = 1.0
        else:
            average_monthly = 0.0
            trend_factor = 1.0
        
        # Générer les prédictions
        predictions = []
        for i in range(1, months + 1):
            prediction_date = end_date + timedelta(days=30 * i)
            predicted = average_monthly * (trend_factor ** (i / 12))
            predictions.append({
                "period": f"{prediction_date.year}-{prediction_date.month:02d}",
                "predicted_amount": round(predicted, 2),
                "confidence": min(0.9, 0.7 + (0.02 * len(totals))) if totals else 0.5,
                "lower_bound": round(predicted * 0.8, 2),
                "upper_bound": round(predicted * 1.2, 2)
            })
        
        return predictions

    def optimize_costs(self, tenant_id: UUID = None) -> List[Dict[str, Any]]:
        """Identifie les opportunités d'optimisation des coûts"""
        tenant = tenant_id or self.tenant_id
        recommendations = []
        
        # Analyser les fournisseurs
        suppliers = self.get_suppliers(tenant_id=tenant)
        for supplier in suppliers:
            total = self.db.query(func.sum(Cost.total_amount)).filter(
                Cost.tenant_id == tenant,
                Cost.supplier_id == supplier.id,
                Cost.is_paid == True
            ).scalar() or Decimal('0')
            
            total_float = float(total)
            if total_float > 0:
                priority = "high" if total_float > 500000 else "medium" if total_float > 100000 else "low"
                recommendations.append({
                    "type": "supplier_negotiation",
                    "supplier_id": str(supplier.id),
                    "supplier_name": supplier.name,
                    "title": f"Négocier avec {supplier.name}",
                    "description": f"Coût total sur la période: {total_float:,.2f}",
                    "potential_savings": round(total_float * 0.1, 2),
                    "priority": priority,
                    "action_items": [
                        "Demander une remise sur volume",
                        "Négocier les délais de paiement",
                        "Comparer avec d'autres fournisseurs"
                    ]
                })
        
        # Analyser les catégories de coûts
        categories = self.db.query(
            Cost.category,
            func.sum(Cost.total_amount).label('total')
        ).filter(
            Cost.tenant_id == tenant,
            Cost.payment_date >= date.today() - timedelta(days=365),
            Cost.is_paid == True
        ).group_by(Cost.category).all()
        
        total_all = sum(float(total) for _, total in categories)
        
        for category, total in categories:
            total_float = float(total)
            percentage = (total_float / total_all * 100) if total_all > 0 else 0
            
            if percentage > 30:
                recommendations.append({
                    "type": "category_optimization",
                    "category": category,
                    "title": f"Optimiser les coûts de {category}",
                    "description": f"Dépenses annuelles: {total_float:,.2f} ({percentage:.1f}% du total)",
                    "potential_savings": round(total_float * 0.15, 2),
                    "priority": "high",
                    "action_items": [
                        "Audit des dépenses de cette catégorie",
                        "Rechercher des alternatives moins chères",
                        "Mettre en place un contrôle budgétaire"
                    ]
                })
        
        # Analyser les coûts récurrents
        recurring_costs = self.db.query(Cost).filter(
            Cost.tenant_id == tenant,
            Cost.is_recurring == True,
            Cost.is_active == True,
            Cost.status == "paid"
        ).all()
        
        if len(recurring_costs) > 10:
            recurring_total = sum(float(c.total_amount) for c in recurring_costs)
            recommendations.append({
                "type": "recurring_review",
                "title": "Réviser les abonnements et coûts récurrents",
                "description": f"Vous avez {len(recurring_costs)} coûts récurrents totalisant {recurring_total:,.2f}",
                "potential_savings": round(recurring_total * 0.2, 2),
                "priority": "medium",
                "action_items": [
                    "Identifier les abonnements non utilisés",
                    "Négocier les renouvellements",
                    "Regrouper les services similaires"
                ]
            })
        
        # Analyser les budgets dépassés
        budgets = self.get_budgets(tenant_id=tenant, is_active=True)
        for budget in budgets:
            budget.update_spent_amount(self.db)
            if budget.spending_percentage > 100:
                recommendations.append({
                    "type": "budget_alert",
                    "budget_id": str(budget.id),
                    "budget_name": budget.name,
                    "title": f"Budget {budget.name} dépassé",
                    "description": f"Dépassement de {budget.spending_percentage - 100:.1f}%",
                    "potential_savings": round(float(budget.spent_amount - budget.allocated_amount), 2),
                    "priority": "high",
                    "action_items": [
                        "Analyser les causes du dépassement",
                        "Mettre en place des contrôles supplémentaires",
                        "Revoir l'allocation budgétaire"
                    ]
                })
        
        return recommendations

    # ==============================================
    # STATISTIQUES ET DASHBOARD
    # ==============================================

    def get_dashboard_stats(self, tenant_id: UUID = None) -> Dict[str, Any]:
        """Récupère les statistiques pour le tableau de bord"""
        tenant = tenant_id or self.tenant_id
        today = date.today()
        first_day_month = date(today.year, today.month, 1)
        
        # Coûts du mois
        monthly_costs = self.db.query(func.sum(Cost.total_amount)).filter(
            Cost.tenant_id == tenant,
            Cost.payment_date >= first_day_month,
            Cost.payment_date <= today,
            Cost.is_paid == True
        ).scalar() or Decimal('0')
        
        # Coûts en attente
        pending_costs = self.db.query(func.sum(Cost.total_amount)).filter(
            Cost.tenant_id == tenant,
            Cost.is_paid == False,
            Cost.status.in_(['draft', 'submitted', 'approved'])
        ).scalar() or Decimal('0')
        
        # Coûts en retard
        overdue_costs = self.db.query(Cost).filter(
            Cost.tenant_id == tenant,
            Cost.is_paid == False,
            Cost.due_date < today
        ).count()
        
        # Budgets actifs
        active_budgets = self.db.query(Budget).filter(
            Budget.tenant_id == tenant,
            Budget.is_active == True,
            Budget.start_date <= today,
            Budget.end_date >= today
        ).count()
        
        # Budgets en alerte        budgets_alert = 0
        budgets = self.get_budgets(tenant_id=tenant, is_active=True)
        for budget in budgets:
            budget.update_spent_amount(self.db)
            if budget.spending_percentage >= float(budget.warning_threshold):
                budgets_alert += 1
        
        # Fournisseurs actifs
        suppliers = self.db.query(Supplier).filter(
            Supplier.tenant_id == tenant,
            Supplier.status == "active"
        ).count()
        
        return {
            "monthly_costs": float(monthly_costs),
            "pending_costs": float(pending_costs),
            "overdue_costs_count": overdue_costs,
            "active_budgets": active_budgets,
            "budgets_alert": budgets_alert,
            "active_suppliers": suppliers,
            "period": {
                "month": today.month,
                "year": today.year,
                "start_date": first_day_month.isoformat(),
                "end_date": today.isoformat()
            }
        }

    # ==============================================
    # MÉTHODES PRIVÉES
    # ==============================================

    def _generate_cost_reference(self, tenant_id: UUID, category: str) -> str:
        """Génère une référence unique pour un coût"""
        today = date.today()
        prefix = f"EXP-{today.strftime('%Y%m')}"
        
        count = self.db.query(Cost).filter(
            Cost.tenant_id == tenant_id,
            Cost.reference.like(f"{prefix}%")
        ).count() + 1
        
        return f"{prefix}-{count:04d}"

    def _calculate_next_payment_date(self, current_date: date, frequency: str) -> date:
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
        return current_date

    def _update_budget_spent(self, budget_id: UUID) -> None:
        """Met à jour le montant dépensé d'un budget"""
        budget = self.db.query(Budget).filter(Budget.id == budget_id).first()
        if budget:
            budget.update_spent_amount(self.db)

    def _check_budget_overlap(
        self,
        tenant_id: UUID,
        category: str,
        start_date: date,
        end_date: date
    ) -> bool:
        """Vérifie si un budget existe déjà pour cette période et catégorie"""
        overlapping = self.db.query(Budget).filter(
            Budget.tenant_id == tenant_id,
            Budget.category == category,
            Budget.is_active == True,
            and_(
                Budget.start_date <= end_date,
                Budget.end_date >= start_date
            )
        ).first()
        
        return overlapping is not None

    def _create_credit_entry(self, cost: Cost, user_id: UUID) -> None:
        """Crée les entrées de crédit fournisseur pour un coût non payé"""
        from app.models.supplier_credit import SupplierCreditTransaction
        
        # Récupérer la configuration de crédit du fournisseur
        credit_config = self.db.query(SupplierCreditConfig).filter(
            SupplierCreditConfig.supplier_id == cost.supplier_id,
            SupplierCreditConfig.is_active == True
        ).first()
        
        if not credit_config:
            return
        
        # Créer ou récupérer la dette du fournisseur
        debt = self.db.query(SupplierDebt).filter(
            SupplierDebt.supplier_id == cost.supplier_id,
            SupplierDebt.tenant_id == cost.tenant_id
        ).first()
        
        if not debt:
            debt = SupplierDebt(
                tenant_id=cost.tenant_id,
                supplier_id=cost.supplier_id,
                total_credit_amount=Decimal('0'),
                total_repaid_amount=Decimal('0'),
                current_debt=Decimal('0'),
                first_credit_date=cost.due_date,
                status=CreditStatus.ACTIVE.value
            )
            self.db.add(debt)
            self.db.flush()
        
        # Mettre à jour la dette
        debt.total_credit_amount += cost.total_amount
        debt.current_debt += cost.total_amount
        debt.update_debt()
        
        # Créer le PurchaseCredit
        purchase_credit = PurchaseCredit(
            tenant_id=cost.tenant_id,
            purchase_id=None,
            supplier_id=cost.supplier_id,
            config_id=credit_config.id,
            debt_id=debt.id,
            credit_amount=cost.total_amount,
            repaid_amount=Decimal('0'),
            remaining_amount=cost.total_amount,
            interest_rate_applied=credit_config.interest_rate,
            payment_frequency=credit_config.payment_frequency,
            repayment_percentage=credit_config.repayment_percentage_of_sale,
            due_date=cost.due_date,
            status=CreditStatus.ACTIVE.value,
            created_by=user_id,
            notes=f"Coût créé: {cost.description}"
        )
        self.db.add(purchase_credit)
        self.db.flush()
        
        # Créer la transaction de crédit
        transaction = SupplierCreditTransaction(
            tenant_id=cost.tenant_id,
            debt_id=debt.id,
            supplier_id=cost.supplier_id,
            transaction_type="credit_purchase",
            amount=cost.total_amount,
            balance_before=debt.current_debt - cost.total_amount,
            balance_after=debt.current_debt,
            purchase_credit_id=purchase_credit.id,
            description=f"Achat à crédit: {cost.description}",
            transaction_date=cost.due_date,
            created_by=user_id
        )
        self.db.add(transaction)

    def _calculate_slope(self, x: List[int], y: List[float]) -> float:
        """Calcule la pente d'une régression linéaire simple"""
        n = len(x)
        if n <= 1:
            return 0.0
        
        x_mean = sum(x) / n
        y_mean = sum(y) / n
        
        numerator = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
        
        if denominator == 0:
            return 0.0
        
        return numerator / denominator
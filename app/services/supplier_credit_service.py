# app/services/supplier_credit_service.py
"""
Service de gestion du crédit fournisseurs
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional, Dict, Any, List
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func

from app.models.supplier_credit import (
    SupplierCreditConfig, SupplierDebt, PurchaseCredit, 
    ProductCreditItem, SaleCreditAllocation, SupplierCreditTransaction,
     CreditStatus, PaymentFrequency, ProductOwnershipStatus
)
from app.models.cost import Supplier
from app.models.purchase import Purchase, PurchaseItem
from app.models.product import Product
from app.models.sale import Sale, SaleItem
from app.models.finance import Expense
from app.models.capital import AdjustedCapital


class SupplierCreditService:
    """Service de gestion du crédit fournisseurs"""

    def __init__(self, db: Session):
        self.db = db

    # =====================================
    # CONFIGURATION FOURNISSEUR
    # =====================================

    def create_supplier_config(
        self,
        tenant_id: UUID,
        supplier_id: UUID,
        config_data: Dict[str, Any],
        user_id: Optional[UUID] = None
    ) -> SupplierCreditConfig:
        """Crée une configuration de crédit pour un fournisseur"""
        
        config = SupplierCreditConfig(
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            name=config_data.get("name"),
            description=config_data.get("description"),
            is_default=config_data.get("is_default", False),
            max_credit_amount=Decimal(str(config_data.get("max_credit_amount", 0))),
            max_credit_days=config_data.get("max_credit_days"),
            interest_rate=Decimal(str(config_data.get("interest_rate", 0))),
            late_fee_rate=Decimal(str(config_data.get("late_fee_rate", 0))),
            payment_frequency=config_data.get("payment_frequency", PaymentFrequency.PER_SALE.value),
            repayment_percentage_of_sale=Decimal(str(config_data.get("repayment_percentage_of_sale", 30))),
            min_repayment_amount=Decimal(str(config_data.get("min_repayment_amount", 0))),
            max_repayment_amount=Decimal(str(config_data.get("max_repayment_amount", 0))) if config_data.get("max_repayment_amount") else None,
            custom_due_dates=config_data.get("custom_due_dates", []),
            grace_period_days=config_data.get("grace_period_days", 0),
            repayment_priority=config_data.get("repayment_priority", 1),
            auto_repayment_enabled=config_data.get("auto_repayment_enabled", True),
            send_reminders=config_data.get("send_reminders", True),
            reminder_days_before=config_data.get("reminder_days_before", 3),
            notes=config_data.get("notes"),
            meta_data=config_data.get("meta_data", {}),
            created_by=user_id
        )
        
        # Si c'est la config par défaut, désactiver les autres
        if config.is_default:
            self.db.query(SupplierCreditConfig).filter(
                SupplierCreditConfig.tenant_id == tenant_id,
                SupplierCreditConfig.supplier_id == supplier_id,
                SupplierCreditConfig.is_default == True
            ).update({"is_default": False})
        
        self.db.add(config)
        self.db.commit()
        self.db.refresh(config)
        return config

    def get_supplier_config(self, supplier_id: UUID) -> Optional[SupplierCreditConfig]:
        """Récupère la configuration active d'un fournisseur"""
        return self.db.query(SupplierCreditConfig).filter(
            SupplierCreditConfig.supplier_id == supplier_id,
            SupplierCreditConfig.is_active == True
        ).order_by(SupplierCreditConfig.is_default.desc()).first()

    # =====================================
    # ACHAT À CRÉDIT
    # =====================================

    def create_credit_purchase(
        self,
        purchase_id: UUID,
        config_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None
    ) -> PurchaseCredit:
        """Transforme un achat en achat à crédit"""
        
        purchase = self.db.query(Purchase).filter(Purchase.id == purchase_id).first()
        if not purchase:
            raise ValueError("Achat non trouvé")
        
        # Récupérer la configuration
        config = None
        if config_id:
            config = self.db.query(SupplierCreditConfig).filter(
                SupplierCreditConfig.id == config_id
            ).first()
        else:
            config = self.get_supplier_config(purchase.supplier_id)
        
        if not config:
            raise ValueError("Aucune configuration de crédit trouvée pour ce fournisseur")
        
        # Récupérer ou créer la dette du fournisseur
        debt = self.db.query(SupplierDebt).filter(
            SupplierDebt.supplier_id == purchase.supplier_id,
            SupplierDebt.tenant_id == purchase.tenant_id
        ).first()
        
        if not debt:
            debt = SupplierDebt(
                tenant_id=purchase.tenant_id,
                supplier_id=purchase.supplier_id,
                first_credit_date=date.today()
            )
            self.db.add(debt)
            self.db.flush()
        
        # Calculer la date d'échéance
        due_date = date.today()
        if config.max_credit_days:
            due_date = date.today() + timedelta(days=config.max_credit_days)
        
        # Créer le crédit d'achat
        credit_amount = purchase.total_amount  # Le montant total à crédit
        
        credit = PurchaseCredit(
            tenant_id=purchase.tenant_id,
            purchase_id=purchase.id,
            supplier_id=purchase.supplier_id,
            config_id=config.id,
            debt_id=debt.id,
            credit_amount=credit_amount,
            interest_rate_applied=config.interest_rate,
            payment_frequency=config.payment_frequency,
            repayment_percentage=config.repayment_percentage_of_sale,
            due_date=due_date,
            grace_date=due_date + timedelta(days=config.grace_period_days) if config.grace_period_days else None,
            created_by=user_id,
            notes=f"Crédit pour achat {purchase.reference}"
        )
        
        self.db.add(credit)
        self.db.flush()
        
        # Mettre à jour les items du produit avec statut crédit
        for item in purchase.items:
            # Calculer la part crédit pour cet item
            item_ratio = item.total / purchase.total_amount if purchase.total_amount > 0 else 0
            credit_portion = credit_amount * item_ratio
            
            product_credit = ProductCreditItem(
                tenant_id=purchase.tenant_id,
                product_id=item.product_id,
                purchase_credit_id=credit.id,
                product_name=item.product_name,
                product_code=item.product_code,
                batch_number=item.batch_number,
                ownership_status=ProductOwnershipStatus.CREDIT.value,
                quantity=int(item.quantity_ordered),
                unit_cost=item.unit_price,
                total_cost=item.total,
                credit_portion=credit_portion,
                equity_portion=item.total - credit_portion,
                remaining_quantity=int(item.quantity_ordered),
                product_stock_id=item.product_stock_id if hasattr(item, 'product_stock_id') else None
            )
            self.db.add(product_credit)
            
            # Mettre à jour le produit
            product = self.db.query(Product).filter(Product.id == item.product_id).first()
            if product:
                product.has_credit_portion = True
                product.ownership_status = ProductOwnershipStatus.CREDIT.value
        
        # Mettre à jour la dette
        debt.total_credit_amount += credit_amount
        debt.current_debt += credit_amount
        debt.status = CreditStatus.ACTIVE.value
        
        # Mettre à jour l'achat
        purchase.is_credit_purchase = True
        purchase.credit_config_id = config.id
        
        # Créer la transaction
        self._create_transaction(
            debt_id=debt.id,
            supplier_id=purchase.supplier_id,
            tenant_id=purchase.tenant_id,
            transaction_type="credit_purchase",
            amount=credit_amount,
            balance_before=debt.current_debt - credit_amount,
            balance_after=debt.current_debt,
            purchase_credit_id=credit.id,
            description=f"Achat à crédit {purchase.reference}",
            created_by=user_id
        )
        
        self.db.commit()
        self.db.refresh(credit)
        return credit

    # =====================================
    # REMBOURSEMENT AUTOMATIQUE À LA VENTE
    # =====================================

    def process_sale_repayment(
        self,
        sale_id: UUID,
        user_id: Optional[UUID] = None
    ) -> List[SaleCreditAllocation]:
        """
        Traite le remboursement automatique pour une vente
        Chaque vente d'un produit à crédit déclenche un remboursement partiel
        """
        
        sale = self.db.query(Sale).filter(Sale.id == sale_id).first()
        if not sale:
            raise ValueError("Vente non trouvée")
        
        allocations = []
        
        for sale_item in sale.items:
            # Chercher le produit crédit correspondant
            product_credit_items = self.db.query(ProductCreditItem).filter(
                ProductCreditItem.product_id == sale_item.product_id,
                ProductCreditItem.is_active == True,
                ProductCreditItem.is_fully_repaid == False,
                ProductCreditItem.remaining_quantity > 0
            ).all()
            
            # Trier par priorité (FIFO: le plus ancien d'abord)
            product_credit_items.sort(key=lambda x: x.created_at)
            
            remaining_quantity = sale_item.quantity
            for credit_item in product_credit_items:
                if remaining_quantity <= 0:
                    break
                
                # Quantité à allouer à ce crédit
                alloc_quantity = min(remaining_quantity, credit_item.remaining_quantity)
                
                # Calcul du montant de la vente pour cette allocation
                alloc_sale_amount = sale_item.unit_price * alloc_quantity
                
                # Calcul du remboursement basé sur le pourcentage configuré
                config = self.get_supplier_config(credit_item.purchase_credit.supplier_id)
                repayment_percentage = config.repayment_percentage_of_sale if config else Decimal('30')
                
                repayment_amount = alloc_sale_amount * (repayment_percentage / Decimal('100'))
                
                # Partie qui va au capital propre
                capital_portion = alloc_sale_amount - repayment_amount
                
                # Créer l'allocation
                allocation = SaleCreditAllocation(
                    tenant_id=sale.tenant_id,
                    sale_id=sale.id,
                    sale_item_id=sale_item.id,
                    product_credit_item_id=credit_item.id,
                    purchase_credit_id=credit_item.purchase_credit_id,
                    supplier_id=credit_item.purchase_credit.supplier_id,
                    sale_amount=alloc_sale_amount,
                    allocated_repayment=repayment_amount,
                    capital_portion=capital_portion,
                    quantity_sold=alloc_quantity,
                    unit_sale_price=sale_item.unit_price,
                    sale_date=sale.sale_date
                )
                self.db.add(allocation)
                self.db.flush()
                
                # Mettre à jour le produit crédit
                credit_item.update_from_sale(alloc_quantity, alloc_sale_amount)
                
                # Mettre à jour le crédit d'achat
                purchase_credit = credit_item.purchase_credit
                purchase_credit.repaid_amount += repayment_amount
                purchase_credit.last_sale_trigger_date = datetime.utcnow()
                purchase_credit.update_remaining()
                
                # Mettre à jour la dette fournisseur
                debt = purchase_credit.debt
                debt.total_repaid_amount += repayment_amount
                debt.last_repayment_date = date.today()
                debt.update_debt()
                
                # Créer la transaction
                self._create_transaction(
                    debt_id=debt.id,
                    supplier_id=purchase_credit.supplier_id,
                    tenant_id=sale.tenant_id,
                    transaction_type="repayment_from_sale",
                    amount=repayment_amount,
                    balance_before=debt.current_debt + repayment_amount,
                    balance_after=debt.current_debt,
                    purchase_credit_id=purchase_credit.id,
                    sale_allocation_id=allocation.id,
                    description=f"Remboursement automatique via vente {sale.reference}",
                    created_by=user_id
                )
                
                allocations.append(allocation)
                remaining_quantity -= alloc_quantity
        
        # Mettre à jour le capital ajusté
        self.update_adjusted_capital(sale.tenant_id, sale.pharmacy_id)
        
        self.db.commit()
        return allocations

    # =====================================
    # REMBOURSEMENT MANUEL / PAIEMENT DIRECT
    # =====================================

    def manual_repayment(
        self,
        supplier_id: UUID,
        amount: Decimal,
        payment_reference: str,
        user_id: UUID,
        notes: Optional[str] = None
    ) -> SupplierCreditTransaction:
        """Enregistre un remboursement manuel au fournisseur"""
        
        debt = self.db.query(SupplierDebt).filter(
            SupplierDebt.supplier_id == supplier_id
        ).first()
        
        if not debt:
            raise ValueError("Aucune dette trouvée pour ce fournisseur")
        
        if amount > debt.current_debt:
            raise ValueError(f"Montant supérieur à la dette actuelle ({debt.current_debt})")
        
        balance_before = debt.current_debt
        debt.total_repaid_amount += amount
        debt.last_repayment_date = date.today()
        debt.update_debt()
        
        transaction = self._create_transaction(
            debt_id=debt.id,
            supplier_id=supplier_id,
            tenant_id=debt.tenant_id,
            transaction_type="manual_repayment",
            amount=amount,
            balance_before=balance_before,
            balance_after=debt.current_debt,
            description=notes or f"Remboursement manuel - Réf: {payment_reference}",
            reference=payment_reference,
            created_by=user_id
        )
        
        # Mettre à jour le capital ajusté
        self.update_adjusted_capital(debt.tenant_id)
        
        self.db.commit()
        return transaction

    # =====================================
    # CAPITAL AJUSTÉ (CAISSE - DETTES)
    # =====================================

    def update_adjusted_capital(
        self,
        tenant_id: UUID,
        pharmacy_id: Optional[UUID] = None
    ) -> AdjustedCapital:
        """Calcule et met à jour le capital ajusté"""
        
        # Récupérer les dettes fournisseurs
        debt_query = self.db.query(SupplierDebt).filter(
            SupplierDebt.tenant_id == tenant_id
        )
        if pharmacy_id:
            # Filtrer par pharmacie via les relations
            debt_query = debt_query.join(PurchaseCredit).filter(
                PurchaseCredit.tenant_id == tenant_id
            )
        
        total_debt = sum([d.current_debt for d in debt_query.all()])
        
        # Récupérer les liquidités (à adapter selon votre modèle)
        from app.models.finance import Expense
        from app.models.sale import Sale
        
        # Calculer la caisse (ventes - dépenses)
        total_sales = self.db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.sale_date == date.today()
        ).with_entities(func.sum(Sale.total_amount)).scalar() or Decimal('0')
        
        total_expenses = self.db.query(Expense).filter(
            Expense.tenant_id == tenant_id,
            Expense.expense_date == date.today()
        ).with_entities(func.sum(Expense.total_amount)).scalar() or Decimal('0')
        
        cash_in_hand = total_sales - total_expenses
        
        # Récupérer la valeur du stock
        total_stock_value = self.db.query(Product).filter(
            Product.tenant_id == tenant_id
        ).with_entities(func.sum(Product.quantity * Product.purchase_price)).scalar() or Decimal('0')
        
        # Créer ou mettre à jour le capital ajusté
        adjusted = self.db.query(AdjustedCapital).filter(
            AdjustedCapital.tenant_id == tenant_id,
            AdjustedCapital.calculation_date == date.today()
        ).first()
        
        if not adjusted:
            adjusted = AdjustedCapital(
                tenant_id=tenant_id,
                pharmacy_id=pharmacy_id or UUID(int=0),  # À adapter
                calculation_date=date.today()
            )
            self.db.add(adjusted)
        
        adjusted.cash_in_hand = cash_in_hand
        adjusted.total_supplier_debt = total_debt
        adjusted.stock_value = total_stock_value
        adjusted.calculate_adjusted_capital()
        
        self.db.commit()
        self.db.refresh(adjusted)
        return adjusted

    # =====================================
    # CALCUL DU BÉNÉFICE RÉEL
    # =====================================

    def calculate_real_profit(
        self,
        tenant_id: UUID,
        start_date: date,
        end_date: date
    ) -> Dict[str, Any]:
        """Calcule le bénéfice réel en tenant compte des dettes"""
        
        # Ventes sur la période
        total_sales = self.db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.sale_date.between(start_date, end_date)
        ).with_entities(func.sum(Sale.total_amount)).scalar() or Decimal('0')
        
        # Remboursements effectués sur la période
        total_repayments = self.db.query(SupplierCreditTransaction).filter(
            SupplierCreditTransaction.tenant_id == tenant_id,
            SupplierCreditTransaction.transaction_type.in_(["repayment_from_sale", "manual_repayment"]),
            SupplierCreditTransaction.transaction_date.between(start_date, end_date)
        ).with_entities(func.sum(SupplierCreditTransaction.amount)).scalar() or Decimal('0')
        
        # Dépenses sur la période
        total_expenses = self.db.query(Expense).filter(
            Expense.tenant_id == tenant_id,
            Expense.expense_date.between(start_date, end_date)
        ).with_entities(func.sum(Expense.total_amount)).scalar() or Decimal('0')
        
        # Coût des marchandises vendues
        total_cogs = self.db.query(SaleItem).filter(
            SaleItem.tenant_id == tenant_id,
            SaleItem.created_at.between(start_date, end_date)
        ).with_entities(func.sum(SaleItem.quantity * SaleItem.unit_purchase_price)).scalar() or Decimal('0')
        
        # Bénéfice brut
        gross_profit = total_sales - total_cogs
        
        # Bénéfice net (avec remboursements de dettes)
        net_profit = total_sales - total_cogs - total_expenses - total_repayments
        
        # Capital généré réellement
        real_capital_generated = total_sales - total_repayments
        
        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            },
            "total_sales": float(total_sales),
            "total_cogs": float(total_cogs),
            "gross_profit": float(gross_profit),
            "gross_margin": float(gross_profit / total_sales * 100) if total_sales > 0 else 0,
            "total_expenses": float(total_expenses),
            "total_repayments": float(total_repayments),
            "net_profit": float(net_profit),
            "net_margin": float(net_profit / total_sales * 100) if total_sales > 0 else 0,
            "real_capital_generated": float(real_capital_generated),
            "explanation": {
                "formula": "Bénéfice réel = Ventes - Coût des ventes - Dépenses - Remboursements dettes",
                "note": "Les remboursements de dettes sont déduits car ils représentent une sortie de capital"
            }
        }

    # =====================================
    # BALANCE FOURNISSEUR
    # =====================================

    def get_supplier_balance(self, supplier_id: UUID) -> Dict[str, Any]:
        """Obtient la balance détaillée d'un fournisseur"""
        
        debt = self.db.query(SupplierDebt).filter(
            SupplierDebt.supplier_id == supplier_id
        ).first()
        
        if not debt:
            return {
                "supplier_id": str(supplier_id),
                "total_credit": 0,
                "total_repaid": 0,
                "current_debt": 0,
                "status": "no_debt"
            }
        
        # Récupérer les crédits actifs
        active_credits = self.db.query(PurchaseCredit).filter(
            PurchaseCredit.supplier_id == supplier_id,
            PurchaseCredit.status.in_([CreditStatus.ACTIVE.value, CreditStatus.PARTIALLY_PAID.value])
        ).all()
        
        credits_detail = []
        for credit in active_credits:
            credits_detail.append({
                "purchase_id": str(credit.purchase_id),
                "credit_amount": float(credit.credit_amount),
                "repaid_amount": float(credit.repaid_amount),
                "remaining": float(credit.remaining_amount),
                "due_date": credit.due_date.isoformat(),
                "status": credit.status,
                "progress": credit.repayment_progress
            })
        
        return {
            "supplier_id": str(supplier_id),
            "total_credit": float(debt.total_credit_amount),
            "total_repaid": float(debt.total_repaid_amount),
            "current_debt": float(debt.current_debt),
            "accrued_interest": float(debt.accrued_interest),
            "late_fees": float(debt.late_fees),
            "debt_ratio": debt.debt_ratio,
            "status": debt.status,
            "first_credit_date": debt.first_credit_date.isoformat() if debt.first_credit_date else None,
            "last_repayment_date": debt.last_repayment_date.isoformat() if debt.last_repayment_date else None,
            "next_due_date": debt.next_due_date.isoformat() if debt.next_due_date else None,
            "active_credits": credits_detail
        }

    # =====================================
    # MÉTHODES UTILITAIRES PRIVÉES
    # =====================================

    def _create_transaction(
        self,
        debt_id: UUID,
        supplier_id: UUID,
        tenant_id: UUID,
        transaction_type: str,
        amount: Decimal,
        balance_before: Decimal,
        balance_after: Decimal,
        purchase_credit_id: Optional[UUID] = None,
        sale_allocation_id: Optional[UUID] = None,
        description: Optional[str] = None,
        reference: Optional[str] = None,
        created_by: Optional[UUID] = None
    ) -> SupplierCreditTransaction:
        """Crée une transaction de crédit"""
        
        transaction = SupplierCreditTransaction(
            tenant_id=tenant_id,
            debt_id=debt_id,
            supplier_id=supplier_id,
            transaction_type=transaction_type,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            purchase_credit_id=purchase_credit_id,
            sale_allocation_id=sale_allocation_id,
            description=description,
            reference=reference,
            transaction_date=date.today(),
            created_by=created_by
        )
        
        self.db.add(transaction)
        self.db.flush()
        return transaction
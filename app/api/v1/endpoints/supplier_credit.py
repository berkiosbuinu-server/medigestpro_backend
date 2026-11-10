# app/api/v1/endpoints/supplier_credit.py
"""
Endpoints pour la gestion du crédit fournisseurs
"""

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.api import deps
from app.models.user import User
from app.models.tenant import Tenant
from app.schemas.supplier_credit import (
    SupplierCreditConfigCreate, SupplierCreditConfigResponse,
    PurchaseCreditCreate, PurchaseCreditResponse,
    ManualRepaymentRequest, SupplierBalanceResponse,
    AdjustedCapitalResponse, RealProfitRequest, RealProfitResponse,
    SaleRepaymentResponse
)
from app.services.supplier_credit_service import SupplierCreditService
from app.db.session import get_db

router = APIRouter(prefix="/supplier-credit", tags=["Supplier Credit"])


# =====================================
# CONFIGURATION FOURNISSEUR
# =====================================

@router.post("/config", response_model=SupplierCreditConfigResponse)
def create_supplier_config(
    config_data: SupplierCreditConfigCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    tenant: Tenant = Depends(deps.get_current_tenant)
):
    """Crée une configuration de crédit pour un fournisseur"""
    service = SupplierCreditService(db)
    
    config = service.create_supplier_config(
        tenant_id=tenant.id,
        supplier_id=config_data.supplier_id,
        config_data=config_data.dict(exclude={"supplier_id"}),
        user_id=current_user.id
    )
    
    return config


@router.get("/config/{supplier_id}", response_model=Optional[SupplierCreditConfigResponse])
def get_supplier_config(
    supplier_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    tenant: Tenant = Depends(deps.get_current_tenant)
):
    """Récupère la configuration active d'un fournisseur"""
    service = SupplierCreditService(db)
    config = service.get_supplier_config(supplier_id)
    
    if not config:
        raise HTTPException(status_code=404, detail="Configuration non trouvée")
    
    return config


# =====================================
# ACHAT À CRÉDIT
# =====================================

@router.post("/purchase-credit/{purchase_id}", response_model=PurchaseCreditResponse)
def create_purchase_credit(
    purchase_id: UUID,
    config_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    tenant: Tenant = Depends(deps.get_current_tenant)
):
    """Transforme un achat en achat à crédit"""
    service = SupplierCreditService(db)
    
    credit = service.create_credit_purchase(
        purchase_id=purchase_id,
        config_id=config_id,
        user_id=current_user.id
    )
    
    return credit


# =====================================
# REMBOURSEMENT
# =====================================

@router.post("/process-sale-repayment/{sale_id}", response_model=List[SaleRepaymentResponse])
def process_sale_repayment(
    sale_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    tenant: Tenant = Depends(deps.get_current_tenant)
):
    """Traite le remboursement automatique après une vente"""
    service = SupplierCreditService(db)
    
    allocations = service.process_sale_repayment(
        sale_id=sale_id,
        user_id=current_user.id
    )
    
    return allocations


@router.post("/manual-repayment")
def manual_repayment(
    request: ManualRepaymentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    tenant: Tenant = Depends(deps.get_current_tenant)
):
    """Enregistre un remboursement manuel au fournisseur"""
    service = SupplierCreditService(db)
    
    transaction = service.manual_repayment(
        supplier_id=request.supplier_id,
        amount=request.amount,
        payment_reference=request.payment_reference,
        user_id=current_user.id,
        notes=request.notes
    )
    
    return {
        "success": True,
        "transaction_id": str(transaction.id),
        "message": f"Remboursement de {request.amount} enregistré"
    }


# =====================================
# BALANCE ET ÉTATS
# =====================================

@router.get("/supplier-balance/{supplier_id}", response_model=SupplierBalanceResponse)
def get_supplier_balance(
    supplier_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    tenant: Tenant = Depends(deps.get_current_tenant)
):
    """Obtient la balance détaillée d'un fournisseur"""
    service = SupplierCreditService(db)
    
    balance = service.get_supplier_balance(supplier_id)
    return balance


@router.get("/all-supplier-balances")
def get_all_supplier_balances(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    tenant: Tenant = Depends(deps.get_current_tenant)
):
    """Obtient les balances de tous les fournisseurs"""
    from app.models.cost import Supplier
    
    suppliers = db.query(Supplier).filter(Supplier.tenant_id == tenant.id).all()
    service = SupplierCreditService(db)
    
    balances = []
    for supplier in suppliers:
        balance = service.get_supplier_balance(supplier.id)
        balance["supplier_name"] = supplier.name
        balances.append(balance)
    
    return {
        "total_suppliers": len(balances),
        "total_debt": sum(b["current_debt"] for b in balances),
        "suppliers": balances
    }


@router.get("/adjusted-capital", response_model=AdjustedCapitalResponse)
def get_adjusted_capital(
    pharmacy_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    tenant: Tenant = Depends(deps.get_current_tenant)
):
    """Obtient le capital ajusté (caisse - dettes)"""
    service = SupplierCreditService(db)
    
    adjusted = service.update_adjusted_capital(
        tenant_id=tenant.id,
        pharmacy_id=pharmacy_id
    )
    
    return adjusted


@router.post("/real-profit", response_model=RealProfitResponse)
def calculate_real_profit(
    request: RealProfitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    tenant: Tenant = Depends(deps.get_current_tenant)
):
    """Calcule le bénéfice réel en tenant compte des dettes"""
    service = SupplierCreditService(db)
    
    profit = service.calculate_real_profit(
        tenant_id=tenant.id,
        start_date=request.start_date,
        end_date=request.end_date
    )
    
    return profit


# =====================================
# TABLEAU DE BORD
# =====================================

@router.get("/dashboard")
def credit_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    tenant: Tenant = Depends(deps.get_current_tenant)
):
    """Tableau de bord du crédit fournisseurs"""
    service = SupplierCreditService(db)
    
    from app.models.cost import Supplier
    from app.models.supplier_credit import SupplierDebt, PurchaseCredit
    
    # Statistiques globales
    total_debt = db.query(SupplierDebt).filter(
        SupplierDebt.tenant_id == tenant.id
    ).with_entities(func.sum(SupplierDebt.current_debt)).scalar() or 0
    
    active_credits = db.query(PurchaseCredit).filter(
        PurchaseCredit.tenant_id == tenant.id,
        PurchaseCredit.status.in_(["active", "partially_paid"])
    ).count()
    
    overdue_credits = db.query(PurchaseCredit).filter(
        PurchaseCredit.tenant_id == tenant.id,
        PurchaseCredit.due_date < date.today(),
        PurchaseCredit.status != "fully_paid"
    ).count()
    
    suppliers_with_debt = db.query(SupplierDebt).filter(
        SupplierDebt.tenant_id == tenant.id,
        SupplierDebt.current_debt > 0
    ).count()
    
    # Capital ajusté
    adjusted = service.update_adjusted_capital(tenant.id)
    
    return {
        "summary": {
            "total_supplier_debt": float(total_debt),
            "active_credits": active_credits,
            "overdue_credits": overdue_credits,
            "suppliers_with_debt": suppliers_with_debt
        },
        "adjusted_capital": {
            "gross_capital": float(adjusted.gross_capital),
            "adjusted_capital": float(adjusted.adjusted_capital),
            "equity_capital": float(adjusted.equity_capital),
            "total_supplier_debt": float(adjusted.total_supplier_debt),
            "formula": "Capital réel = Actif total - Dettes fournisseurs"
        },
        "alerts": {
            "overdue_alert": overdue_credits > 0,
            "high_debt_ratio": (float(total_debt) / float(adjusted.gross_capital) * 100) if adjusted.gross_capital > 0 else 0
        }
    }
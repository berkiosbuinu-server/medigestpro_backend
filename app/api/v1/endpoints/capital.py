# app/api/v1/endpoints/capital.py
"""
API de gestion du capital et du chiffre d'affaires
Conforme aux normes SYSCOHADA révisées
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, and_, or_, extract, case, text
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4
from datetime import datetime, date, timedelta
from decimal import Decimal
import logging
from enum import Enum

from app.db.session import get_db
from app.models.user import User
from app.models.tenant import Tenant
from app.models.pharmacy import Pharmacy
from app.models.branch import Branch
from app.models.sale import Sale, SaleItem
from app.models.product import Product
from app.models.user_pharmacy import UserPharmacy
from app.models.stock_movement import StockMovement
from app.schemas.capital import (
    CapitalResponse,
    CapitalCreate,
    CapitalUpdate,
    CapitalTransactionResponse,
    CapitalTransactionCreate,
    CapitalSummaryResponse,
    CapitalEvolutionResponse,
    TurnoverResponse,
    TurnoverDetailResponse,
    TurnoverByPeriodResponse,
    FinancialPositionResponse,
    CapitalBalanceResponse,
    CapitalReportResponse
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

# Création du router
router = APIRouter(prefix="/capital", tags=["Gestion du Capital"])
logger = logging.getLogger(__name__)

# Import des modèles de capital
try:
    from app.models.capital import (
        Capital, 
        CapitalTransaction,
        CapitalAccount,
        Turnover
    )
except ImportError:
    # Création des modèles à l'exécution si nécessaire
    from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, Numeric, Text, Date, Index
    from sqlalchemy.dialects.postgresql import UUID, JSONB
    from sqlalchemy.orm import relationship
    from app.db.base import Base
    
    class Capital(Base):
        __tablename__ = "capitals"
        
        id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
        tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
        pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
        branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)
        
        # Capital initial
        initial_capital = Column(Numeric(15, 2), nullable=False, default=0)
        current_capital = Column(Numeric(15, 2), nullable=False, default=0)
        
        # Composition du capital
        cash_capital = Column(Numeric(15, 2), nullable=False, default=0)
        stock_capital = Column(Numeric(15, 2), nullable=False, default=0)
        equipment_capital = Column(Numeric(15, 2), nullable=False, default=0)
        other_capital = Column(Numeric(15, 2), nullable=False, default=0)
        
        # Dates
        start_date = Column(Date, nullable=False)
        last_update_date = Column(Date, nullable=False)
        
        # Métadonnées
        notes = Column(Text, nullable=True)
        meta_data = Column(JSONB, default=dict)
        
        # Timestamps
        created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
        updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
        
        # Relations
        tenant = relationship("Tenant")
        pharmacy = relationship("Pharmacy")
        branch = relationship("Branch")
        transactions = relationship("CapitalTransaction", back_populates="capital", cascade="all, delete-orphan")
    
    class CapitalTransaction(Base):
        __tablename__ = "capital_transactions"
        
        id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
        capital_id = Column(UUID(as_uuid=True), ForeignKey("capitals.id"), nullable=False, index=True)
        tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
        pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
        branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)
        
        # Type de transaction
        transaction_type = Column(String(50), nullable=False)  # initial, increase, decrease, profit_added, loss_deducted
        transaction_category = Column(String(50), nullable=False)  # cash, stock, equipment, other, turnover, expense
        
        # Montants
        amount = Column(Numeric(15, 2), nullable=False)
        previous_capital = Column(Numeric(15, 2), nullable=False)
        new_capital = Column(Numeric(15, 2), nullable=False)
        
        # Références
        reference_id = Column(UUID(as_uuid=True), nullable=True)  # Sale ID, Purchase ID, etc.
        reference_type = Column(String(50), nullable=True)  # sale, purchase, expense, investment
        
        # Description
        description = Column(String(500), nullable=True)
        notes = Column(Text, nullable=True)
        
        # Dates
        transaction_date = Column(Date, nullable=False)
        
        # Timestamps
        created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
        created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
        
        # Relations
        capital = relationship("Capital", back_populates="transactions")
        tenant = relationship("Tenant")
        pharmacy = relationship("Pharmacy")
        branch = relationship("Branch")
    
    class CapitalAccount(Base):
        __tablename__ = "capital_accounts"
        
        id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
        tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
        pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
        branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)
        
        # Comptes selon SYSCOHADA
        account_code = Column(String(20), nullable=False)  # 101, 102, 103, etc.
        account_name = Column(String(200), nullable=False)
        account_type = Column(String(50), nullable=False)  # asset, liability, equity, income, expense
        
        # Solde
        balance = Column(Numeric(15, 2), nullable=False, default=0)
        debit = Column(Numeric(15, 2), nullable=False, default=0)
        credit = Column(Numeric(15, 2), nullable=False, default=0)
        
        # Période
        period_year = Column(Integer, nullable=False)
        period_month = Column(Integer, nullable=True)
        
        # Timestamps
        created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
        updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    class Turnover(Base):
        __tablename__ = "turnovers"
        
        id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
        tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
        pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id"), nullable=False, index=True)
        branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=True, index=True)
        
        # Chiffre d'affaires
        total_turnover = Column(Numeric(15, 2), nullable=False, default=0)
        net_turnover = Column(Numeric(15, 2), nullable=False, default=0)
        tax_amount = Column(Numeric(15, 2), nullable=False, default=0)
        discount_amount = Column(Numeric(15, 2), nullable=False, default=0)
        
        # Composition
        sales_count = Column(Integer, nullable=False, default=0)
        items_sold = Column(Integer, nullable=False, default=0)
        
        # Période
        period_date = Column(Date, nullable=False)
        period_type = Column(String(20), nullable=False)  # day, week, month, year
        
        # Timestamps
        created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
        updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
        
        __table_args__ = (
            Index("ix_turnovers_period", "tenant_id", "pharmacy_id", "period_date", "period_type"),
        )


# =======================
# Helpers
# =======================

def get_user_accessible_pharmacies(db: Session, user_id: UUID, tenant_id: Optional[UUID] = None) -> List[UUID]:
    """Récupère la liste des pharmacies accessibles par l'utilisateur"""
    if not user_id:
        return []
    
    query = db.query(UserPharmacy.pharmacy_id).filter(UserPharmacy.user_id == user_id)
    
    if tenant_id:
        query = query.join(Pharmacy).filter(Pharmacy.tenant_id == tenant_id)
    
    return [p.pharmacy_id for p in query.all()]


def calculate_stock_value(db: Session, tenant_id: UUID, pharmacy_id: UUID, branch_id: Optional[UUID] = None) -> Decimal:
    """Calcule la valeur du stock pour une pharmacie/succursale"""
    query = db.query(
        func.coalesce(func.sum(Product.quantity * Product.purchase_price), 0)
    ).filter(
        Product.tenant_id == tenant_id,
        Product.pharmacy_id == pharmacy_id,
        Product.is_active == True
    )
    
    if branch_id:
        query = query.filter(Product.branch_id == branch_id)
    
    result = query.scalar()
    return Decimal(str(result)) if result else Decimal('0')


def calculate_turnover_for_period(
    db: Session, 
    tenant_id: UUID, 
    pharmacy_id: UUID, 
    start_date: date, 
    end_date: date,
    branch_id: Optional[UUID] = None
) -> Dict[str, Any]:
    """Calcule le chiffre d'affaires pour une période donnée"""
    
    query = db.query(
        func.coalesce(func.sum(Sale.total_amount), 0).label("total_turnover"),
        func.coalesce(func.sum(Sale.total_tva), 0).label("total_tax"),
        func.coalesce(func.sum(Sale.total_discount), 0).label("total_discount"),
        func.count(Sale.id).label("sales_count"),
        func.coalesce(func.sum(SaleItem.quantity), 0).label("items_sold")
    ).join(
        SaleItem, SaleItem.sale_id == Sale.id
    ).filter(
        Sale.tenant_id == tenant_id,
        Sale.pharmacy_id == pharmacy_id,
        Sale.status == "completed",
        Sale.created_at >= datetime.combine(start_date, datetime.min.time()),
        Sale.created_at <= datetime.combine(end_date, datetime.max.time())
    )
    
    if branch_id:
        query = query.filter(Sale.branch_id == branch_id)
    
    result = query.first()
    
    total_turnover = Decimal(str(result.total_turnover)) if result.total_turnover else Decimal('0')
    total_tax = Decimal(str(result.total_tax)) if result.total_tax else Decimal('0')
    total_discount = Decimal(str(result.total_discount)) if result.total_discount else Decimal('0')
    
    net_turnover = total_turnover - total_tax
    
    return {
        "total_turnover": total_turnover,
        "net_turnover": net_turnover,
        "tax_amount": total_tax,
        "discount_amount": total_discount,
        "sales_count": result.sales_count or 0,
        "items_sold": int(result.items_sold) if result.items_sold else 0
    }


def calculate_total_expenses(
    db: Session, 
    tenant_id: UUID, 
    pharmacy_id: UUID, 
    start_date: date, 
    end_date: date,
    branch_id: Optional[UUID] = None
) -> Decimal:
    """Calcule le total des dépenses pour une période (achats, frais, etc.)"""
    
    # Dépenses des achats de produits
    purchase_expenses = db.query(
        func.coalesce(func.sum(Product.purchase_price * StockMovement.quantity_change), 0)
    ).filter(
        StockMovement.tenant_id == tenant_id,
        StockMovement.pharmacy_id == pharmacy_id,
        StockMovement.movement_type == "purchase",
        StockMovement.quantity_change > 0,
        func.date(StockMovement.created_at) >= start_date,
        func.date(StockMovement.created_at) <= end_date
    )
    
    if branch_id:
        purchase_expenses = purchase_expenses.filter(StockMovement.branch_id == branch_id)
    
    total_purchases = Decimal(str(purchase_expenses.scalar() or 0))
    
    # Ici on pourrait ajouter d'autres types de dépenses (salaires, loyer, etc.)
    # via une table dédiée
    
    return total_purchases


def get_or_create_capital(
    db: Session, 
    tenant_id: UUID, 
    pharmacy_id: UUID, 
    branch_id: Optional[UUID] = None,
    start_date: Optional[date] = None
) -> Capital:
    """Récupère ou crée une entrée de capital"""
    
    query = db.query(Capital).filter(
        Capital.tenant_id == tenant_id,
        Capital.pharmacy_id == pharmacy_id
    )
    
    if branch_id:
        query = query.filter(Capital.branch_id == branch_id)
    else:
        query = query.filter(Capital.branch_id.is_(None))
    
    capital = query.first()
    
    if not capital:
        capital = Capital(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy_id,
            branch_id=branch_id,
            initial_capital=Decimal('0'),
            current_capital=Decimal('0'),
            cash_capital=Decimal('0'),
            stock_capital=Decimal('0'),
            equipment_capital=Decimal('0'),
            other_capital=Decimal('0'),
            start_date=start_date or date.today(),
            last_update_date=date.today()
        )
        db.add(capital)
        db.flush()
    
    return capital


def create_capital_transaction(
    db: Session,
    capital: Capital,
    transaction_type: str,
    transaction_category: str,
    amount: Decimal,
    description: str,
    reference_id: Optional[UUID] = None,
    reference_type: Optional[str] = None,
    created_by: Optional[UUID] = None
) -> CapitalTransaction:
    """Crée une transaction de capital"""
    
    transaction = CapitalTransaction(
        capital_id=capital.id,
        tenant_id=capital.tenant_id,
        pharmacy_id=capital.pharmacy_id,
        branch_id=capital.branch_id,
        transaction_type=transaction_type,
        transaction_category=transaction_category,
        amount=amount,
        previous_capital=capital.current_capital,
        new_capital=capital.current_capital + amount,
        reference_id=reference_id,
        reference_type=reference_type,
        description=description,
        transaction_date=date.today(),
        created_by=created_by
    )
    
    db.add(transaction)
    
    # Mettre à jour le capital
    capital.current_capital += amount
    capital.last_update_date = date.today()
    
    # Mettre à jour la catégorie de capital
    if transaction_category == "cash":
        capital.cash_capital += amount
    elif transaction_category == "stock":
        capital.stock_capital += amount
    elif transaction_category == "equipment":
        capital.equipment_capital += amount
    elif transaction_category == "other":
        capital.other_capital += amount
    
    db.flush()
    
    return transaction


def create_turnover_record(
    db: Session,
    tenant_id: UUID,
    pharmacy_id: UUID,
    period_date: date,
    period_type: str,
    turnover_data: Dict[str, Any],
    branch_id: Optional[UUID] = None
) -> Turnover:
    """Crée ou met à jour un enregistrement de chiffre d'affaires"""
    
    query = db.query(Turnover).filter(
        Turnover.tenant_id == tenant_id,
        Turnover.pharmacy_id == pharmacy_id,
        Turnover.period_date == period_date,
        Turnover.period_type == period_type
    )
    
    if branch_id:
        query = query.filter(Turnover.branch_id == branch_id)
    else:
        query = query.filter(Turnover.branch_id.is_(None))
    
    turnover = query.first()
    
    if turnover:
        turnover.total_turnover = turnover_data["total_turnover"]
        turnover.net_turnover = turnover_data["net_turnover"]
        turnover.tax_amount = turnover_data["tax_amount"]
        turnover.discount_amount = turnover_data["discount_amount"]
        turnover.sales_count = turnover_data["sales_count"]
        turnover.items_sold = turnover_data["items_sold"]
    else:
        turnover = Turnover(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy_id,
            branch_id=branch_id,
            total_turnover=turnover_data["total_turnover"],
            net_turnover=turnover_data["net_turnover"],
            tax_amount=turnover_data["tax_amount"],
            discount_amount=turnover_data["discount_amount"],
            sales_count=turnover_data["sales_count"],
            items_sold=turnover_data["items_sold"],
            period_date=period_date,
            period_type=period_type
        )
        db.add(turnover)
    
    db.flush()
    
    return turnover


# =======================
# Routes Capital
# =======================

@router.post("/init", response_model=CapitalResponse, summary="Initialiser le capital d'une pharmacie")
async def init_capital(
    capital_data: CapitalCreate,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Initialise le capital d'une pharmacie ou succursale.
    Selon les normes SYSCOHADA, le capital initial est enregistré au compte 101.
    """
    
    # Vérifier les permissions
    allowed_roles = ["super_admin", "superadmin", "admin", "gerant"]
    if current_user.role.lower() not in [r.lower() for r in allowed_roles]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès non autorisé. Rôle requis: admin, super_admin, gerant"
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy = current_pharmacy if current_pharmacy else None
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie active sélectionnée"
        )
    
    # Vérifier si un capital existe déjà
    existing_capital = get_or_create_capital(
        db, tenant_id, pharmacy.id, current_branch.id if current_branch else None
    )
    
    if existing_capital.initial_capital > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Un capital a déjà été initialisé. Capital actuel: {existing_capital.initial_capital}"
        )
    
    # Calculer la valeur du stock initial
    stock_value = calculate_stock_value(
        db, tenant_id, pharmacy.id, current_branch.id if current_branch else None
    )
    
    # Répartition du capital
    cash_capital = Decimal(str(capital_data.cash_capital)) if capital_data.cash_capital else Decimal('0')
    stock_capital = stock_value
    equipment_capital = Decimal(str(capital_data.equipment_capital)) if capital_data.equipment_capital else Decimal('0')
    other_capital = Decimal(str(capital_data.other_capital)) if capital_data.other_capital else Decimal('0')
    
    total_capital = cash_capital + stock_capital + equipment_capital + other_capital
    
    # Mettre à jour le capital
    existing_capital.initial_capital = total_capital
    existing_capital.current_capital = total_capital
    existing_capital.cash_capital = cash_capital
    existing_capital.stock_capital = stock_capital
    existing_capital.equipment_capital = equipment_capital
    existing_capital.other_capital = other_capital
    existing_capital.start_date = capital_data.start_date or date.today()
    existing_capital.last_update_date = date.today()
    existing_capital.notes = capital_data.notes
    
    # Créer la transaction initiale
    transaction = create_capital_transaction(
        db=db,
        capital=existing_capital,
        transaction_type="initial",
        transaction_category="all",
        amount=total_capital,
        description=f"Capital initial - {existing_capital.start_date}",
        reference_id=pharmacy.id,
        reference_type="pharmacy",
        created_by=current_user.id
    )
    
    # Créer les comptes SYSCOHADA
    # Compte 101 - Capital social
    capital_account = db.query(CapitalAccount).filter(
        CapitalAccount.tenant_id == tenant_id,
        CapitalAccount.pharmacy_id == pharmacy.id,
        CapitalAccount.account_code == "101",
        CapitalAccount.period_year == date.today().year
    ).first()
    
    if not capital_account:
        capital_account = CapitalAccount(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy.id,
            branch_id=current_branch.id if current_branch else None,
            account_code="101",
            account_name="Capital social",
            account_type="equity",
            balance=total_capital,
            debit=Decimal('0'),
            credit=total_capital,
            period_year=date.today().year,
            period_month=capital_data.start_date.month if capital_data.start_date else date.today().month
        )
        db.add(capital_account)
    
    # Compte 531 - Caisse
    cash_account = db.query(CapitalAccount).filter(
        CapitalAccount.tenant_id == tenant_id,
        CapitalAccount.pharmacy_id == pharmacy.id,
        CapitalAccount.account_code == "531",
        CapitalAccount.period_year == date.today().year
    ).first()
    
    if not cash_account:
        cash_account = CapitalAccount(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy.id,
            branch_id=current_branch.id if current_branch else None,
            account_code="531",
            account_name="Caisse",
            account_type="asset",
            balance=cash_capital,
            debit=cash_capital,
            credit=Decimal('0'),
            period_year=date.today().year,
            period_month=capital_data.start_date.month if capital_data.start_date else date.today().month
        )
        db.add(cash_account)
    
    # Compte 31 - Stocks
    stock_account = db.query(CapitalAccount).filter(
        CapitalAccount.tenant_id == tenant_id,
        CapitalAccount.pharmacy_id == pharmacy.id,
        CapitalAccount.account_code == "31",
        CapitalAccount.period_year == date.today().year
    ).first()
    
    if not stock_account:
        stock_account = CapitalAccount(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy.id,
            branch_id=current_branch.id if current_branch else None,
            account_code="31",
            account_name="Stocks",
            account_type="asset",
            balance=stock_capital,
            debit=stock_capital,
            credit=Decimal('0'),
            period_year=date.today().year,
            period_month=capital_data.start_date.month if capital_data.start_date else date.today().month
        )
        db.add(stock_account)
    
    db.commit()
    db.refresh(existing_capital)
    
    logger.info(
        f"Capital initialisé: {total_capital} pour la pharmacie {pharmacy.name} "
        f"par {current_user.email}"
    )
    
    return CapitalResponse(
        id=existing_capital.id,
        tenant_id=existing_capital.tenant_id,
        pharmacy_id=existing_capital.pharmacy_id,
        branch_id=existing_capital.branch_id,
        initial_capital=float(existing_capital.initial_capital),
        current_capital=float(existing_capital.current_capital),
        cash_capital=float(existing_capital.cash_capital),
        stock_capital=float(existing_capital.stock_capital),
        equipment_capital=float(existing_capital.equipment_capital),
        other_capital=float(existing_capital.other_capital),
        start_date=existing_capital.start_date,
        last_update_date=existing_capital.last_update_date,
        notes=existing_capital.notes,
        created_at=existing_capital.created_at,
        updated_at=existing_capital.updated_at
    )


@router.get("/current", response_model=CapitalResponse, summary="Récupérer le capital actuel")
async def get_current_capital(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Récupère le capital actuel de la pharmacie/succursale"""
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy = current_pharmacy if current_pharmacy else None
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie active sélectionnée"
        )
    
    capital = get_or_create_capital(
        db, tenant_id, pharmacy.id, current_branch.id if current_branch else None
    )
    
    return CapitalResponse(
        id=capital.id,
        tenant_id=capital.tenant_id,
        pharmacy_id=capital.pharmacy_id,
        branch_id=capital.branch_id,
        initial_capital=float(capital.initial_capital),
        current_capital=float(capital.current_capital),
        cash_capital=float(capital.cash_capital),
        stock_capital=float(capital.stock_capital),
        equipment_capital=float(capital.equipment_capital),
        other_capital=float(capital.other_capital),
        start_date=capital.start_date,
        last_update_date=capital.last_update_date,
        notes=capital.notes,
        created_at=capital.created_at,
        updated_at=capital.updated_at
    )


@router.post("/update", response_model=CapitalResponse, summary="Mettre à jour le capital")
async def update_capital(
    capital_data: CapitalUpdate,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Met à jour le capital (ajout ou retrait).
    """
    
    allowed_roles = ["super_admin", "superadmin", "admin", "gerant"]
    if current_user.role.lower() not in [r.lower() for r in allowed_roles]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès non autorisé"
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy = current_pharmacy if current_pharmacy else None
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie active sélectionnée"
        )
    
    capital = get_or_create_capital(
        db, tenant_id, pharmacy.id, current_branch.id if current_branch else None
    )
    
    # Vérifier si le capital a déjà été initialisé
    if capital.initial_capital == 0 and capital_data.amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le capital n'a pas encore été initialisé. Utilisez /capital/init d'abord."
        )
    
    amount = Decimal(str(capital_data.amount))
    transaction_type = "increase" if amount > 0 else "decrease"
    
    # Créer la transaction
    transaction = create_capital_transaction(
        db=db,
        capital=capital,
        transaction_type=transaction_type,
        transaction_category=capital_data.category,
        amount=amount,
        description=capital_data.description or f"{transaction_type} de capital",
        created_by=current_user.id
    )
    
    db.commit()
    db.refresh(capital)
    
    logger.info(
        f"Capital {transaction_type}: {amount} pour la pharmacie {pharmacy.name} "
        f"par {current_user.email}. Nouveau capital: {capital.current_capital}"
    )
    
    return CapitalResponse(
        id=capital.id,
        tenant_id=capital.tenant_id,
        pharmacy_id=capital.pharmacy_id,
        branch_id=capital.branch_id,
        initial_capital=float(capital.initial_capital),
        current_capital=float(capital.current_capital),
        cash_capital=float(capital.cash_capital),
        stock_capital=float(capital.stock_capital),
        equipment_capital=float(capital.equipment_capital),
        other_capital=float(capital.other_capital),
        start_date=capital.start_date,
        last_update_date=capital.last_update_date,
        notes=capital.notes,
        created_at=capital.created_at,
        updated_at=capital.updated_at
    )


@router.get("/transactions", response_model=List[CapitalTransactionResponse], summary="Historique des transactions")
async def get_capital_transactions(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    transaction_type: Optional[str] = Query(None, description="Type de transaction"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """Récupère l'historique des transactions de capital"""
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy = current_pharmacy if current_pharmacy else None
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie active sélectionnée"
        )
    
    query = db.query(CapitalTransaction).filter(
        CapitalTransaction.tenant_id == tenant_id,
        CapitalTransaction.pharmacy_id == pharmacy.id
    )
    
    if current_branch:
        query = query.filter(CapitalTransaction.branch_id == current_branch.id)
    else:
        query = query.filter(CapitalTransaction.branch_id.is_(None))
    
    if start_date:
        query = query.filter(CapitalTransaction.transaction_date >= start_date)
    if end_date:
        query = query.filter(CapitalTransaction.transaction_date <= end_date)
    if transaction_type:
        query = query.filter(CapitalTransaction.transaction_type == transaction_type)
    
    transactions = query.order_by(desc(CapitalTransaction.created_at)).offset(skip).limit(limit).all()
    
    return [
        CapitalTransactionResponse(
            id=t.id,
            capital_id=t.capital_id,
            transaction_type=t.transaction_type,
            transaction_category=t.transaction_category,
            amount=float(t.amount),
            previous_capital=float(t.previous_capital),
            new_capital=float(t.new_capital),
            description=t.description,
            transaction_date=t.transaction_date,
            created_at=t.created_at,
            created_by=t.created_by
        )
        for t in transactions
    ]


# =======================
# Routes Chiffre d'affaires
# =======================

@router.get("/turnover", response_model=TurnoverResponse, summary="Chiffre d'affaires")
async def get_turnover(
    period_type: str = Query("month", description="Période: day, week, month, year"),
    year: Optional[int] = Query(None, description="Année"),
    month: Optional[int] = Query(None, description="Mois (1-12)"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère le chiffre d'affaires pour une période donnée.
    Conforme aux normes SYSCOHADA.
    """
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy = current_pharmacy if current_pharmacy else None
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie active sélectionnée"
        )
    
    # Déterminer la plage de dates
    today = date.today()
    
    if period_type == "day":
        if start_date:
            period_date = start_date
        else:
            period_date = today
        start = period_date
        end = period_date
        
    elif period_type == "week":
        if start_date:
            period_date = start_date
        else:
            period_date = today - timedelta(days=today.weekday())
        start = period_date
        end = period_date + timedelta(days=6)
        
    elif period_type == "month":
        if year and month:
            period_date = date(year, month, 1)
        elif start_date:
            period_date = start_date.replace(day=1)
        else:
            period_date = today.replace(day=1)
        start = period_date
        if period_date.month == 12:
            end = date(period_date.year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(period_date.year, period_date.month + 1, 1) - timedelta(days=1)
            
    elif period_type == "year":
        if year:
            period_date = date(year, 1, 1)
        elif start_date:
            period_date = date(start_date.year, 1, 1)
        else:
            period_date = date(today.year, 1, 1)
        start = period_date
        end = date(period_date.year, 12, 31)
        
    else:
        if start_date and end_date:
            start = start_date
            end = end_date
        else:
            start = today - timedelta(days=30)
            end = today
    
    # Calculer le chiffre d'affaires
    turnover_data = calculate_turnover_for_period(
        db, tenant_id, pharmacy.id, start, end, current_branch.id if current_branch else None
    )
    
    # Calculer les dépenses
    total_expenses = calculate_total_expenses(
        db, tenant_id, pharmacy.id, start, end, current_branch.id if current_branch else None
    )
    
    net_profit = turnover_data["net_turnover"] - total_expenses
    
    return TurnoverResponse(
        pharmacy_id=pharmacy.id,
        pharmacy_name=pharmacy.name,
        branch_id=current_branch.id if current_branch else None,
        period_type=period_type,
        period_start=start,
        period_end=end,
        total_turnover=float(turnover_data["total_turnover"]),
        net_turnover=float(turnover_data["net_turnover"]),
        tax_amount=float(turnover_data["tax_amount"]),
        discount_amount=float(turnover_data["discount_amount"]),
        total_expenses=float(total_expenses),
        net_profit=float(net_profit),
        sales_count=turnover_data["sales_count"],
        items_sold=turnover_data["items_sold"]
    )


@router.get("/turnover/by-period", response_model=TurnoverByPeriodResponse, summary="Chiffre d'affaires par période")
async def get_turnover_by_period(
    period_type: str = Query("month", description="Période: day, week, month, year"),
    months: int = Query(12, ge=1, le=24, description="Nombre de périodes"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère le chiffre d'affaires sur plusieurs périodes (tendance).
    """
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy = current_pharmacy if current_pharmacy else None
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie active sélectionnée"
        )
    
    today = date.today()
    periods = []
    data = []
    
    for i in range(months):
        if period_type == "month":
            # Mois
            period_date = today.replace(day=1) - timedelta(days=30 * (months - 1 - i))
            start = period_date.replace(day=1)
            if period_date.month == 12:
                end = date(period_date.year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(period_date.year, period_date.month + 1, 1) - timedelta(days=1)
            label = period_date.strftime("%B %Y")
            
        elif period_type == "week":
            # Semaine
            period_date = today - timedelta(days=today.weekday() + 7 * (months - 1 - i))
            start = period_date
            end = period_date + timedelta(days=6)
            label = f"Semaine {period_date.strftime('%W')} {period_date.year}"
            
        elif period_type == "year":
            # Année
            period_date = date(today.year - (months - 1 - i), 1, 1)
            start = period_date
            end = date(period_date.year, 12, 31)
            label = str(period_date.year)
            
        else:
            # Jour
            period_date = today - timedelta(days=months - 1 - i)
            start = period_date
            end = period_date
            label = period_date.strftime("%d/%m/%Y")
        
        turnover_data = calculate_turnover_for_period(
            db, tenant_id, pharmacy.id, start, end, current_branch.id if current_branch else None
        )
        
        total_expenses = calculate_total_expenses(
            db, tenant_id, pharmacy.id, start, end, current_branch.id if current_branch else None
        )
        
        net_profit = turnover_data["net_turnover"] - total_expenses
        
        periods.append({
            "period": label,
            "start_date": start,
            "end_date": end
        })
        
        data.append({
            "total_turnover": float(turnover_data["total_turnover"]),
            "net_turnover": float(turnover_data["net_turnover"]),
            "total_expenses": float(total_expenses),
            "net_profit": float(net_profit),
            "sales_count": turnover_data["sales_count"]
        })
    
    # Calculer les totaux
    total_turnover = sum(d["total_turnover"] for d in data)
    total_net_turnover = sum(d["net_turnover"] for d in data)
    total_expenses = sum(d["total_expenses"] for d in data)
    total_net_profit = sum(d["net_profit"] for d in data)
    total_sales = sum(d["sales_count"] for d in data)
    
    # Calculer la croissance
    if len(data) >= 2:
        current = data[-1]["net_turnover"]
        previous = data[-2]["net_turnover"]
        growth = ((current - previous) / previous * 100) if previous > 0 else 0
    else:
        growth = 0
    
    return TurnoverByPeriodResponse(
        pharmacy_id=pharmacy.id,
        pharmacy_name=pharmacy.name,
        branch_id=current_branch.id if current_branch else None,
        period_type=period_type,
        periods=periods,
        data=data,
        total_turnover=total_turnover,
        total_net_turnover=total_net_turnover,
        total_expenses=total_expenses,
        total_net_profit=total_net_profit,
        total_sales=total_sales,
        growth_percentage=growth
    )


@router.get("/turnover/detail", response_model=TurnoverDetailResponse, summary="Détail du chiffre d'affaires")
async def get_turnover_detail(
    start_date: date = Query(..., description="Date de début"),
    end_date: date = Query(..., description="Date de fin"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Détail du chiffre d'affaires par produit et par catégorie.
    """
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy = current_pharmacy if current_pharmacy else None
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie active sélectionnée"
        )
    
    # Récupérer les ventes détaillées
    sales_items = db.query(
        Product.id.label("product_id"),
        Product.name.label("product_name"),
        Product.code.label("product_code"),
        Product.category.label("category"),
        func.sum(SaleItem.quantity).label("quantity_sold"),
        func.sum(SaleItem.total).label("total_amount"),
        func.avg(SaleItem.unit_price).label("avg_price")
    ).join(
        SaleItem, SaleItem.product_id == Product.id
    ).join(
        Sale, Sale.id == SaleItem.sale_id
    ).filter(
        Sale.tenant_id == tenant_id,
        Sale.pharmacy_id == pharmacy.id,
        Sale.status == "completed",
        Sale.created_at >= datetime.combine(start_date, datetime.min.time()),
        Sale.created_at <= datetime.combine(end_date, datetime.max.time())
    )
    
    if current_branch:
        sales_items = sales_items.filter(Sale.branch_id == current_branch.id)
    
    sales_items = sales_items.group_by(
        Product.id, Product.name, Product.code, Product.category
    ).order_by(desc("total_amount")).all()
    
    # Statistiques globales
    total_revenue = Decimal('0')
    total_tax = Decimal('0')
    total_discount = Decimal('0')
    total_items = 0
    total_sales = 0
    
    # Par produit
    by_product = []
    # Par catégorie
    by_category = {}
    
    for item in sales_items:
        revenue = Decimal(str(item.total_amount))
        total_revenue += revenue
        total_items += int(item.quantity_sold)
        total_sales += 1
        
        by_product.append({
            "product_id": str(item.product_id),
            "product_name": item.product_name,
            "product_code": item.product_code,
            "quantity_sold": int(item.quantity_sold),
            "total_amount": float(revenue),
            "avg_price": float(item.avg_price)
        })
        
        category = item.category or "Non classé"
        if category not in by_category:
            by_category[category] = {
                "category": category,
                "total_amount": 0,
                "quantity_sold": 0
            }
        by_category[category]["total_amount"] += float(revenue)
        by_category[category]["quantity_sold"] += int(item.quantity_sold)
    
    # Calcul de la taxe (TVA)
    total_tax = total_revenue * Decimal('0.16')  # 16% par défaut
    net_revenue = total_revenue - total_tax
    
    return TurnoverDetailResponse(
        pharmacy_id=pharmacy.id,
        pharmacy_name=pharmacy.name,
        branch_id=current_branch.id if current_branch else None,
        period_start=start_date,
        period_end=end_date,
        total_revenue=float(total_revenue),
        net_revenue=float(net_revenue),
        total_tax=float(total_tax),
        total_items_sold=total_items,
        total_sales_count=total_sales,
        by_product=by_product,
        by_category=list(by_category.values())
    )


# =======================
# Routes Évolution du capital
# =======================

@router.get("/evolution", response_model=CapitalEvolutionResponse, summary="Évolution du capital")
async def get_capital_evolution(
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère l'évolution du capital sur une période.
    """
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy = current_pharmacy if current_pharmacy else None
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie active sélectionnée"
        )
    
    # Définir les dates
    if not end_date:
        end_date = date.today()
    if not start_date:
        start_date = end_date - timedelta(days=365)
    
    # Récupérer les transactions
    query = db.query(CapitalTransaction).filter(
        CapitalTransaction.tenant_id == tenant_id,
        CapitalTransaction.pharmacy_id == pharmacy.id,
        CapitalTransaction.transaction_date >= start_date,
        CapitalTransaction.transaction_date <= end_date
    )
    
    if current_branch:
        query = query.filter(CapitalTransaction.branch_id == current_branch.id)
    else:
        query = query.filter(CapitalTransaction.branch_id.is_(None))
    
    transactions = query.order_by(CapitalTransaction.transaction_date).all()
    
    # Données d'évolution
    evolution = []
    current_capital = Decimal('0')
    
    # Capital initial
    initial_capital = None
    for t in transactions:
        if t.transaction_type == "initial":
            initial_capital = t.amount
            current_capital = t.amount
            evolution.append({
                "date": t.transaction_date,
                "capital": float(current_capital),
                "change": float(t.amount),
                "change_type": "initial",
                "description": t.description
            })
            break
    
    # Autres transactions
    for t in transactions:
        if t.transaction_type == "initial":
            continue
        current_capital += t.amount
        evolution.append({
            "date": t.transaction_date,
            "capital": float(current_capital),
            "change": float(t.amount),
            "change_type": t.transaction_type,
            "description": t.description
        })
    
    # Calculer le chiffre d'affaires sur la période
    turnover_data = calculate_turnover_for_period(
        db, tenant_id, pharmacy.id, start_date, end_date, current_branch.id if current_branch else None
    )
    
    # Calculer les dépenses
    total_expenses = calculate_total_expenses(
        db, tenant_id, pharmacy.id, start_date, end_date, current_branch.id if current_branch else None
    )
    
    return CapitalEvolutionResponse(
        pharmacy_id=pharmacy.id,
        pharmacy_name=pharmacy.name,
        branch_id=current_branch.id if current_branch else None,
        start_date=start_date,
        end_date=end_date,
        evolution=evolution,
        initial_capital=float(initial_capital) if initial_capital else 0,
        final_capital=float(current_capital) if evolution else 0,
        total_turnover=float(turnover_data["total_turnover"]),
        total_expenses=float(total_expenses),
        net_profit=float(turnover_data["net_turnover"] - total_expenses),
        capital_variation=float(current_capital - (initial_capital or 0))
    )


# =======================
# Routes Résumé financier
# =======================

@router.get("/summary", response_model=CapitalSummaryResponse, summary="Résumé financier")
async def get_capital_summary(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère un résumé financier complet avec:
    - Capital actuel
    - Chiffre d'affaires (jour, mois, année)
    - Bénéfice net
    - Évolution
    """
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy = current_pharmacy if current_pharmacy else None
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie active sélectionnée"
        )
    
    # Capital
    capital = get_or_create_capital(
        db, tenant_id, pharmacy.id, current_branch.id if current_branch else None
    )
    
    today = date.today()
    first_day_month = today.replace(day=1)
    first_day_year = date(today.year, 1, 1)
    
    # Chiffre d'affaires du jour
    today_turnover = calculate_turnover_for_period(
        db, tenant_id, pharmacy.id, today, today, current_branch.id if current_branch else None
    )
    
    # Chiffre d'affaires du mois
    month_turnover = calculate_turnover_for_period(
        db, tenant_id, pharmacy.id, first_day_month, today, current_branch.id if current_branch else None
    )
    
    # Chiffre d'affaires de l'année
    year_turnover = calculate_turnover_for_period(
        db, tenant_id, pharmacy.id, first_day_year, today, current_branch.id if current_branch else None
    )
    
    # Dépenses
    month_expenses = calculate_total_expenses(
        db, tenant_id, pharmacy.id, first_day_month, today, current_branch.id if current_branch else None
    )
    
    year_expenses = calculate_total_expenses(
        db, tenant_id, pharmacy.id, first_day_year, today, current_branch.id if current_branch else None
    )
    
    # Bénéfices
    month_net_profit = month_turnover["net_turnover"] - month_expenses
    year_net_profit = year_turnover["net_turnover"] - year_expenses
    
    # Évolution par rapport au mois dernier
    last_month_start = (first_day_month - timedelta(days=1)).replace(day=1)
    last_month_end = first_day_month - timedelta(days=1)
    
    last_month_turnover = calculate_turnover_for_period(
        db, tenant_id, pharmacy.id, last_month_start, last_month_end, current_branch.id if current_branch else None
    )
    
    month_growth = ((month_turnover["net_turnover"] - last_month_turnover["net_turnover"]) / 
                    last_month_turnover["net_turnover"] * 100) if last_month_turnover["net_turnover"] > 0 else 0
    
    return CapitalSummaryResponse(
        pharmacy_id=pharmacy.id,
        pharmacy_name=pharmacy.name,
        branch_id=current_branch.id if current_branch else None,
        capital={
            "initial": float(capital.initial_capital),
            "current": float(capital.current_capital),
            "cash": float(capital.cash_capital),
            "stock": float(capital.stock_capital),
            "equipment": float(capital.equipment_capital),
            "other": float(capital.other_capital)
        },
        turnover_today={
            "total": float(today_turnover["total_turnover"]),
            "net": float(today_turnover["net_turnover"]),
            "sales_count": today_turnover["sales_count"]
        },
        turnover_month={
            "total": float(month_turnover["total_turnover"]),
            "net": float(month_turnover["net_turnover"]),
            "sales_count": month_turnover["sales_count"],
            "expenses": float(month_expenses),
            "net_profit": float(month_net_profit),
            "growth": float(month_growth)
        },
        turnover_year={
            "total": float(year_turnover["total_turnover"]),
            "net": float(year_turnover["net_turnover"]),
            "sales_count": year_turnover["sales_count"],
            "expenses": float(year_expenses),
            "net_profit": float(year_net_profit)
        },
        last_update=datetime.utcnow()
    )


# =======================
# Routes Rapport SYSCOHADA
# =======================

@router.get("/report/syscohada", response_model=CapitalReportResponse, summary="Rapport SYSCOHADA")
async def get_syscohada_report(
    year: int = Query(..., description="Année du rapport"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Génère un rapport financier selon les normes SYSCOHADA révisées.
    Inclut:
    - Bilan (Actif/Passif)
    - Compte de résultat
    - État des flux de trésorerie
    - Tableau de variation des capitaux propres
    """
    
    allowed_roles = ["super_admin", "superadmin", "admin", "gerant", "comptable"]
    if current_user.role.lower() not in [r.lower() for r in allowed_roles]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès non autorisé"
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy = current_pharmacy if current_pharmacy else None
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie active sélectionnée"
        )
    
    # Dates
    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)
    
    # Capital
    capital = get_or_create_capital(
        db, tenant_id, pharmacy.id, current_branch.id if current_branch else None
    )
    
    # Chiffre d'affaires annuel
    turnover_data = calculate_turnover_for_period(
        db, tenant_id, pharmacy.id, start_date, end_date, current_branch.id if current_branch else None
    )
    
    # Dépenses annuelles
    total_expenses = calculate_total_expenses(
        db, tenant_id, pharmacy.id, start_date, end_date, current_branch.id if current_branch else None
    )
    
    # Valeur du stock actuel
    stock_value = calculate_stock_value(
        db, tenant_id, pharmacy.id, current_branch.id if current_branch else None
    )
    
    # Résultat net
    net_result = turnover_data["net_turnover"] - total_expenses
    
    # Variation des capitaux propres
    capital_variation = capital.current_capital - capital.initial_capital
    
    # Bilan simplifié
    balance = {
        "assets": {
            "current_assets": {
                "cash": float(capital.cash_capital),
                "inventory": float(stock_value),
                "receivables": 0.0,  # À implémenter avec les dettes clients
                "total": float(capital.cash_capital + stock_value)
            },
            "non_current_assets": {
                "equipment": float(capital.equipment_capital),
                "other": float(capital.other_capital),
                "total": float(capital.equipment_capital + capital.other_capital)
            },
            "total_assets": float(capital.current_capital)
        },
        "liabilities": {
            "current_liabilities": {
                "payables": 0.0,  # À implémenter avec les dettes fournisseurs
                "tax_payable": float(turnover_data["tax_amount"]),
                "total": float(turnover_data["tax_amount"])
            },
            "equity": {
                "share_capital": float(capital.initial_capital),
                "retained_earnings": float(capital.current_capital - capital.initial_capital),
                "total": float(capital.current_capital)
            },
            "total_liabilities": float(capital.current_capital)
        }
    }
    
    # Compte de résultat
    income_statement = {
        "revenue": {
            "net_turnover": float(turnover_data["net_turnover"]),
            "other_income": 0.0,
            "total_revenue": float(turnover_data["net_turnover"])
        },
        "expenses": {
            "cost_of_goods_sold": float(total_expenses),
            "operating_expenses": 0.0,
            "total_expenses": float(total_expenses)
        },
        "net_result": float(net_result)
    }
    
    # Flux de trésorerie
    cash_flow = {
        "operating_activities": {
            "net_result": float(net_result),
            "adjustments": 0.0,
            "cash_from_operations": float(net_result)
        },
        "investing_activities": 0.0,
        "financing_activities": float(capital_variation),
        "net_cash_flow": float(net_result + capital_variation)
    }
    
    # Variation des capitaux propres
    equity_changes = {
        "beginning_equity": float(capital.initial_capital),
        "net_result": float(net_result),
        "capital_increase": float(max(0, capital_variation)),
        "dividends": 0.0,
        "ending_equity": float(capital.current_capital)
    }
    
    return CapitalReportResponse(
        pharmacy_id=pharmacy.id,
        pharmacy_name=pharmacy.name,
        branch_id=current_branch.id if current_branch else None,
        period_start=start_date,
        period_end=end_date,
        balance=balance,
        income_statement=income_statement,
        cash_flow=cash_flow,
        equity_changes=equity_changes,
        ratios={
            "gross_margin_rate": float((turnover_data["net_turnover"] - total_expenses) / turnover_data["net_turnover"] * 100) if turnover_data["net_turnover"] > 0 else 0,
            "net_margin_rate": float(net_result / turnover_data["net_turnover"] * 100) if turnover_data["net_turnover"] > 0 else 0,
            "return_on_equity": float(net_result / capital.current_capital * 100) if capital.current_capital > 0 else 0,
            "current_ratio": float((capital.cash_capital + stock_value) / turnover_data["tax_amount"]) if turnover_data["tax_amount"] > 0 else 0
        },
        generated_at=datetime.utcnow()
    )


@router.get("/balances", response_model=List[CapitalBalanceResponse], summary="Soldes des comptes")
async def get_account_balances(
    year: int = Query(..., description="Année"),
    month: Optional[int] = Query(None, description="Mois (optionnel)"),
    account_type: Optional[str] = Query(None, description="Type de compte: asset, liability, equity, income, expense"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère les soldes des comptes selon la nomenclature SYSCOHADA.
    """
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy = current_pharmacy if current_pharmacy else None
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie active sélectionnée"
        )
    
    query = db.query(CapitalAccount).filter(
        CapitalAccount.tenant_id == tenant_id,
        CapitalAccount.pharmacy_id == pharmacy.id,
        CapitalAccount.period_year == year
    )
    
    if current_branch:
        query = query.filter(CapitalAccount.branch_id == current_branch.id)
    else:
        query = query.filter(CapitalAccount.branch_id.is_(None))
    
    if month:
        query = query.filter(CapitalAccount.period_month == month)
    
    if account_type:
        query = query.filter(CapitalAccount.account_type == account_type)
    
    accounts = query.order_by(CapitalAccount.account_code).all()
    
    return [
        CapitalBalanceResponse(
            account_code=acc.account_code,
            account_name=acc.account_name,
            account_type=acc.account_type,
            balance=float(acc.balance),
            debit=float(acc.debit),
            credit=float(acc.credit),
            period_year=acc.period_year,
            period_month=acc.period_month
        )
        for acc in accounts
    ]


# =======================
# Routes Admin multi-pharmacies
# =======================

@router.get("/admin/all-pharmacies", summary="Récupérer le capital de toutes les pharmacies")
async def get_all_pharmacies_capital(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère le capital et le chiffre d'affaires de toutes les pharmacies du tenant.
    Réservé aux administrateurs.
    """
    
    if current_user.role.lower() not in ["super_admin", "superadmin", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs"
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    # Récupérer toutes les pharmacies
    pharmacies = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant_id,
        Pharmacy.is_active == True
    ).all()
    
    today = date.today()
    first_day_year = date(today.year, 1, 1)
    
    result = []
    
    for pharmacy in pharmacies:
        # Capital
        capital = get_or_create_capital(db, tenant_id, pharmacy.id)
        
        # Chiffre d'affaires de l'année
        turnover_data = calculate_turnover_for_period(
            db, tenant_id, pharmacy.id, first_day_year, today
        )
        
        # Dépenses
        total_expenses = calculate_total_expenses(
            db, tenant_id, pharmacy.id, first_day_year, today
        )
        
        result.append({
            "pharmacy_id": str(pharmacy.id),
            "pharmacy_name": pharmacy.name,
            "capital": {
                "initial": float(capital.initial_capital),
                "current": float(capital.current_capital)
            },
            "turnover_year": {
                "total": float(turnover_data["total_turnover"]),
                "net": float(turnover_data["net_turnover"])
            },
            "expenses": float(total_expenses),
            "net_profit": float(turnover_data["net_turnover"] - total_expenses)
        })
    
    # Totaux
    total_capital = sum(r["capital"]["current"] for r in result)
    total_turnover = sum(r["turnover_year"]["total"] for r in result)
    total_net_profit = sum(r["net_profit"] for r in result)
    
    return {
        "pharmacies": result,
        "summary": {
            "total_pharmacies": len(result),
            "total_capital": total_capital,
            "total_turnover": total_turnover,
            "total_net_profit": total_net_profit
        },
        "generated_at": datetime.utcnow().isoformat()
    }


@router.post("/admin/sync-all", summary="Synchroniser le capital de toutes les pharmacies")
async def sync_all_pharmacies_capital(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Synchronise le capital de toutes les pharmacies avec les données réelles (stock, ventes).
    Exécution en arrière-plan.
    """
    
    if current_user.role.lower() not in ["super_admin", "superadmin", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs"
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    # Récupérer toutes les pharmacies
    pharmacies = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant_id,
        Pharmacy.is_active == True
    ).all()
    
    def sync_task():
        from app.db.session import SessionLocal
        sync_db = SessionLocal()
        try:
            for pharmacy in pharmacies:
                # Calculer la valeur du stock
                stock_value = calculate_stock_value(sync_db, tenant_id, pharmacy.id)
                
                # Mettre à jour le capital
                capital = get_or_create_capital(sync_db, tenant_id, pharmacy.id)
                capital.stock_capital = stock_value
                capital.current_capital = capital.cash_capital + stock_value + capital.equipment_capital + capital.other_capital
                capital.last_update_date = date.today()
                
                sync_db.commit()
                
                logger.info(f"Synchronisation du capital pour {pharmacy.name}: {capital.current_capital}")
                
        except Exception as e:
            logger.error(f"Erreur lors de la synchronisation: {e}")
            sync_db.rollback()
        finally:
            sync_db.close()
    
    background_tasks.add_task(sync_task)
    
    return {
        "message": "Synchronisation du capital en cours pour toutes les pharmacies",
        "pharmacies_count": len(pharmacies),
        "status": "processing"
    }


# =======================
# Route de test
# =======================

@router.get("/test", include_in_schema=False)
async def test_capital(
    current_user: User = Depends(get_current_active_user)
):
    """
    Endpoint de test pour vérifier que l'API fonctionne.
    """
    return {
        "message": "Module Capital opérationnel",
        "version": "1.0.0",
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role
        },
        "features": [
            "Gestion du capital selon SYSCOHADA",
            "Suivi du chiffre d'affaires",
            "Évolution du capital",
            "Rapports financiers",
            "Multi-pharmacies et succursales"
        ],
        "timestamp": datetime.utcnow().isoformat()
    }
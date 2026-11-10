# app/api/v1/endpoints/capital.py
"""
API de gestion du Capital et Chiffre d'Affaires
Conforme aux normes SYSCOHADA révisées
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, and_, or_, extract, case
from typing import List, Optional, Dict, Any, Union
from uuid import UUID
import uuid
from datetime import datetime, timedelta, date
from decimal import Decimal
import logging
from pydantic import BaseModel, Field
from enum import Enum

from app.db.session import get_db
from app.models.capital import (
    Capital, CapitalTransaction, CapitalAccount, Turnover,
    get_account_codes, get_default_accounts
)
from app.models.product import Product
from app.models.sale import Sale, SaleItem
from app.models.cost import Cost
from app.models.debt import Debt
from app.models.tenant import Tenant
from app.models.user import User
from app.models.pharmacy import Pharmacy
from app.models.branch import Branch
from app.models.user_pharmacy import UserPharmacy

from app.api.deps import (
    get_current_tenant,
    get_current_user,
    get_current_active_user,
    get_current_pharmacy_entity,
    get_current_branch_entity,
    require_permission,
    can_user_access_pharmacy
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/capital", tags=["Capital"])


# =======================
# SCHÉMAS PYDANTIC
# =======================

class CapitalCreate(BaseModel):
    """Schéma pour la création d'un capital"""
    initial_capital: float = Field(..., ge=0, description="Capital initial")
    cash_capital: float = Field(0, ge=0, description="Capital en caisse")
    stock_capital: float = Field(0, ge=0, description="Capital en stock")
    equipment_capital: float = Field(0, ge=0, description="Capital en équipement")
    other_capital: float = Field(0, ge=0, description="Autres capitaux")
    start_date: date = Field(default_factory=date.today, description="Date de début")
    branch_id: Optional[UUID] = Field(None, description="ID de la succursale")
    notes: Optional[str] = None


class CapitalUpdate(BaseModel):
    """Schéma pour la mise à jour d'un capital"""
    cash_capital: Optional[float] = Field(None, ge=0)
    stock_capital: Optional[float] = Field(None, ge=0)
    equipment_capital: Optional[float] = Field(None, ge=0)
    other_capital: Optional[float] = Field(None, ge=0)
    notes: Optional[str] = None


class CapitalAddRequest(BaseModel):
    """Schéma pour ajouter du capital"""
    amount: float = Field(..., gt=0, description="Montant à ajouter")
    category: str = Field(..., description="cash, stock, equipment, other")
    description: Optional[str] = None
    reference_id: Optional[UUID] = None
    reference_type: Optional[str] = None


class CapitalWithdrawRequest(BaseModel):
    """Schéma pour retirer du capital"""
    amount: float = Field(..., gt=0, description="Montant à retirer")
    category: str = Field(..., description="cash, stock, equipment, other")
    description: Optional[str] = None
    reference_id: Optional[UUID] = None
    reference_type: Optional[str] = None


class CapitalTransactionFilter(BaseModel):
    """Filtres pour les transactions"""
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    transaction_type: Optional[str] = None
    transaction_category: Optional[str] = None
    branch_id: Optional[UUID] = None


class CapitalPerformanceResponse(BaseModel):
    """Réponse pour les performances du capital"""
    current_capital: float
    initial_capital: float
    variation: float
    growth_rate: float
    stock_value: float
    cash_value: float
    equipment_value: float
    other_value: float
    total_sales: float
    total_expenses: float
    total_debts: float
    net_profit: float
    roi: float


class TurnoverStatsResponse(BaseModel):
    """Statistiques de chiffre d'affaires"""
    total_turnover: float
    net_turnover: float
    tax_amount: float
    discount_amount: float
    sales_count: int
    items_sold: int
    period_type: str
    period_start: date
    period_end: date
    daily_average: float
    weekly_average: float
    monthly_average: float
    comparison: Dict[str, Any]


# =======================
# HELPERS
# =======================

def _ensure_pharmacy_in_tenant(current_tenant: Optional[Tenant], current_pharmacy: Optional[Pharmacy]) -> Pharmacy:
    """Vérifie que la pharmacie appartient bien au tenant courant."""
    if current_pharmacy is None:
        raise HTTPException(status_code=400, detail="Aucune pharmacie active sélectionnée")
    
    if current_tenant and getattr(current_pharmacy, "tenant_id", None) != current_tenant.id:
        raise HTTPException(status_code=403, detail="La pharmacie sélectionnée n'appartient pas au tenant courant")
    
    return current_pharmacy


def _check_permission(current_user: User, required_roles: List[str]) -> None:
    """Vérifie si l'utilisateur a les permissions nécessaires."""
    user_role = current_user.role.lower() if current_user.role else ""
    if user_role not in [r.lower() for r in required_roles]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès non autorisé"
        )


def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Convertit une valeur en Decimal."""
    try:
        if value is None:
            return default
        return Decimal(str(value))
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    """Convertit une valeur en float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_user_accessible_pharmacies(db: Session, user_id: UUID, tenant_id: Optional[UUID] = None) -> List[UUID]:
    """Récupère la liste des pharmacies accessibles par l'utilisateur"""
    if not user_id:
        return []
    
    query = db.query(UserPharmacy.pharmacy_id).filter(UserPharmacy.user_id == user_id)
    
    if tenant_id:
        query = query.join(Pharmacy).filter(Pharmacy.tenant_id == tenant_id)
    
    return [p.pharmacy_id for p in query.all()]


def calculate_stock_value(db: Session, tenant_id: UUID, pharmacy_id: UUID, branch_id: Optional[UUID] = None) -> Decimal:
    """Calcule la valeur totale du stock"""
    query = db.query(func.sum(Product.quantity * Product.purchase_price)).filter(
        Product.tenant_id == tenant_id,
        Product.pharmacy_id == pharmacy_id,
        Product.is_active == True
    )
    
    if branch_id:
        query = query.filter(Product.branch_id == branch_id)
    
    result = query.scalar()
    return Decimal(str(result or 0))


def calculate_total_sales(db: Session, tenant_id: UUID, pharmacy_id: UUID, 
                          branch_id: Optional[UUID] = None, 
                          start_date: Optional[date] = None,
                          end_date: Optional[date] = None) -> Decimal:
    """Calcule le total des ventes sur une période"""
    query = db.query(func.sum(Sale.total_amount)).filter(
        Sale.tenant_id == tenant_id,
        Sale.pharmacy_id == pharmacy_id,
        Sale.status == "completed"
    )
    
    if branch_id:
        query = query.filter(Sale.branch_id == branch_id)
    
    if start_date:
        query = query.filter(func.date(Sale.created_at) >= start_date)
    if end_date:
        query = query.filter(func.date(Sale.created_at) <= end_date)
    
    result = query.scalar()
    return Decimal(str(result or 0))


def calculate_total_expenses(db: Session, tenant_id: UUID, pharmacy_id: UUID,
                              branch_id: Optional[UUID] = None,
                              start_date: Optional[date] = None,
                              end_date: Optional[date] = None) -> Decimal:
    """Calcule le total des dépenses sur une période"""
    query = db.query(func.sum(Cost.total_amount)).filter(
        Cost.tenant_id == tenant_id,
        Cost.is_paid == True,
        Cost.status == "paid"
    )
    
    if branch_id:
        query = query.filter(Cost.branch_id == branch_id)
    
    if start_date:
        query = query.filter(Cost.payment_date >= start_date)
    if end_date:
        query = query.filter(Cost.payment_date <= end_date)
    
    result = query.scalar()
    return Decimal(str(result or 0))


def calculate_total_debts(db: Session, tenant_id: UUID, pharmacy_id: UUID,
                           branch_id: Optional[UUID] = None) -> Decimal:
    """Calcule le total des dettes impayées"""
    query = db.query(func.sum(Debt.remaining_amount)).filter(
        Debt.tenant_id == tenant_id,
        Debt.is_active == True,
        Debt.status.in_(["pending", "partially_paid", "overdue"])
    )
    
    if branch_id:
        query = query.filter(Debt.branch_id == branch_id)
    
    result = query.scalar()
    return Decimal(str(result or 0))


def get_or_create_capital(db: Session, tenant_id: UUID, pharmacy_id: UUID, 
                          branch_id: Optional[UUID] = None) -> Capital:
    """Récupère ou crée un capital pour la pharmacie/branche"""
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
        # Créer un capital par défaut
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
            start_date=date.today(),
            last_update_date=date.today()
        )
        db.add(capital)
        db.flush()
    
    return capital


def create_transaction(db: Session, capital_id: UUID, tenant_id: UUID, pharmacy_id: UUID,
                       transaction_type: str, transaction_category: str, amount: Decimal,
                       previous_capital: Decimal, new_capital: Decimal,
                       reference_id: Optional[UUID] = None, reference_type: Optional[str] = None,
                       description: Optional[str] = None, notes: Optional[str] = None,
                       created_by: Optional[UUID] = None, branch_id: Optional[UUID] = None) -> CapitalTransaction:
    """Crée une transaction sur le capital"""
    transaction = CapitalTransaction(
        capital_id=capital_id,
        tenant_id=tenant_id,
        pharmacy_id=pharmacy_id,
        branch_id=branch_id,
        transaction_type=transaction_type,
        transaction_category=transaction_category,
        amount=amount,
        previous_capital=previous_capital,
        new_capital=new_capital,
        reference_id=reference_id,
        reference_type=reference_type,
        description=description,
        notes=notes,
        transaction_date=date.today(),
        created_by=created_by
    )
    db.add(transaction)
    return transaction


def create_accounting_period(db: Session, tenant_id: UUID, pharmacy_id: UUID, 
                              year: int, branch_id: Optional[UUID] = None):
    """Crée les comptes comptables pour une année"""
    # Vérifier si les comptes existent déjà
    existing = db.query(CapitalAccount).filter(
        CapitalAccount.tenant_id == tenant_id,
        CapitalAccount.pharmacy_id == pharmacy_id,
        CapitalAccount.branch_id == branch_id,
        CapitalAccount.period_year == year
    ).first()
    
    if existing:
        return
    
    # Créer les comptes par défaut
    accounts = get_default_accounts(tenant_id, pharmacy_id, year, branch_id)
    for account in accounts:
        db.add(account)
    db.flush()


# =======================
# ROUTES PRINCIPALES
# =======================

@router.get("/", summary="Récupérer le capital actuel")
async def get_capital(
    branch_id: Optional[UUID] = Query(None, description="ID de la succursale"),
    include_transactions: bool = Query(False, description="Inclure les transactions"),
    limit_transactions: int = Query(50, ge=1, le=500, description="Limite des transactions"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère le capital actuel de la pharmacie ou d'une succursale.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien", "vendeur"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Vérifier les permissions pour les branches
        if branch_id:
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id,
                Branch.parent_pharmacy_id == pharmacy.id
            ).first()
            if not branch:
                raise HTTPException(status_code=404, detail="Succursale non trouvée")
            
            if current_user.role not in ["super_admin", "superadmin", "admin", "gerant"]:
                if current_user.active_branch_id != branch_id:
                    raise HTTPException(status_code=403, detail="Accès non autorisé à cette succursale")
        
        capital = get_or_create_capital(db, tenant_id, pharmacy.id, branch_id)
        
        # Calculer la valeur réelle du stock
        stock_value = calculate_stock_value(db, tenant_id, pharmacy.id, branch_id)
        
        # Mettre à jour le capital en stock si nécessaire
        if capital.stock_capital != stock_value:
            capital.stock_capital = stock_value
            capital.current_capital = capital.cash_capital + capital.stock_capital + capital.equipment_capital + capital.other_capital
            capital.last_update_date = date.today()
            db.commit()
            db.refresh(capital)
        
        response = capital.to_dict()
        response["stock_value_real"] = float(stock_value)
        response["stock_variance"] = float(stock_value - capital.stock_capital)
        
        if include_transactions:
            transactions_query = db.query(CapitalTransaction).filter(
                CapitalTransaction.capital_id == capital.id
            ).order_by(desc(CapitalTransaction.transaction_date), desc(CapitalTransaction.created_at))
            
            response["transactions"] = [
                t.to_dict() for t in transactions_query.limit(limit_transactions).all()
            ]
            response["transactions_count"] = transactions_query.count()
        
        return response
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur récupération capital")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.post("/initialize", summary="Initialiser le capital")
async def initialize_capital(
    capital_data: CapitalCreate,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Initialise le capital de la pharmacie.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Vérifier si un capital existe déjà
        existing = db.query(Capital).filter(
            Capital.tenant_id == tenant_id,
            Capital.pharmacy_id == pharmacy.id,
            Capital.branch_id == capital_data.branch_id if capital_data.branch_id else Capital.branch_id.is_(None)
        ).first()
        
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Un capital existe déjà pour cette entité. Utilisez l'API de mise à jour."
            )
        
        # Calculer la valeur du stock
        stock_value = calculate_stock_value(db, tenant_id, pharmacy.id, capital_data.branch_id)
        
        # Si le stock_value est plus grand que le stock_capital fourni, utiliser la valeur réelle
        if stock_value > Decimal(str(capital_data.stock_capital)):
            capital_data.stock_capital = float(stock_value)
        
        # Créer le capital
        capital = Capital(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy.id,
            branch_id=capital_data.branch_id,
            initial_capital=Decimal(str(capital_data.initial_capital)),
            current_capital=Decimal(str(capital_data.initial_capital)),
            cash_capital=Decimal(str(capital_data.cash_capital)),
            stock_capital=Decimal(str(capital_data.stock_capital)),
            equipment_capital=Decimal(str(capital_data.equipment_capital)),
            other_capital=Decimal(str(capital_data.other_capital)),
            start_date=capital_data.start_date,
            last_update_date=date.today(),
            notes=capital_data.notes
        )
        
        db.add(capital)
        db.flush()
        
        # Créer la transaction initiale
        create_transaction(
            db=db,
            capital_id=capital.id,
            tenant_id=tenant_id,
            pharmacy_id=pharmacy.id,
            branch_id=capital_data.branch_id,
            transaction_type="initial",
            transaction_category="all",
            amount=Decimal(str(capital_data.initial_capital)),
            previous_capital=Decimal('0'),
            new_capital=Decimal(str(capital_data.initial_capital)),
            description=f"Capital initial de {capital_data.initial_capital}",
            created_by=current_user.id
        )
        
        # Créer les comptes comptables pour l'année en cours
        create_accounting_period(db, tenant_id, pharmacy.id, date.today().year, capital_data.branch_id)
        
        db.commit()
        db.refresh(capital)
        
        logger.info(f"Capital initialisé: {capital.initial_capital} par {current_user.email}")
        
        return {
            "message": "Capital initialisé avec succès",
            "capital": capital.to_dict()
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur initialisation capital")
        raise HTTPException(status_code=500, detail=f"Erreur initialisation capital: {exc}")


@router.put("/{capital_id}", summary="Mettre à jour le capital")
async def update_capital(
    capital_id: UUID,
    capital_data: CapitalUpdate,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Met à jour les composantes du capital.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        capital = db.query(Capital).filter(
            Capital.id == capital_id,
            Capital.tenant_id == tenant_id,
            Capital.pharmacy_id == pharmacy.id
        ).first()
        
        if not capital:
            raise HTTPException(status_code=404, detail="Capital non trouvé")
        
        previous_capital = capital.current_capital
        
        # Mettre à jour les champs
        if capital_data.cash_capital is not None:
            capital.cash_capital = Decimal(str(capital_data.cash_capital))
        if capital_data.stock_capital is not None:
            capital.stock_capital = Decimal(str(capital_data.stock_capital))
        if capital_data.equipment_capital is not None:
            capital.equipment_capital = Decimal(str(capital_data.equipment_capital))
        if capital_data.other_capital is not None:
            capital.other_capital = Decimal(str(capital_data.other_capital))
        if capital_data.notes is not None:
            capital.notes = capital_data.notes
        
        # Recalculer le capital actuel
        capital.current_capital = capital.cash_capital + capital.stock_capital + capital.equipment_capital + capital.other_capital
        capital.last_update_date = date.today()
        
        # Créer une transaction si le capital a changé
        if capital.current_capital != previous_capital:
            change = capital.current_capital - previous_capital
            transaction_type = "increase" if change > 0 else "decrease"
            
            # Déterminer la catégorie
            category = "other"
            if capital_data.cash_capital is not None:
                category = "cash"
            elif capital_data.stock_capital is not None:
                category = "stock"
            elif capital_data.equipment_capital is not None:
                category = "equipment"
            
            create_transaction(
                db=db,
                capital_id=capital.id,
                tenant_id=tenant_id,
                pharmacy_id=pharmacy.id,
                branch_id=capital.branch_id,
                transaction_type=transaction_type,
                transaction_category=category,
                amount=abs(change),
                previous_capital=previous_capital,
                new_capital=capital.current_capital,
                description=f"Mise à jour manuelle du capital",
                created_by=current_user.id
            )
        
        db.commit()
        db.refresh(capital)
        
        logger.info(f"Capital mis à jour: {capital.current_capital} par {current_user.email}")
        
        return {
            "message": "Capital mis à jour avec succès",
            "capital": capital.to_dict()
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur mise à jour capital")
        raise HTTPException(status_code=500, detail=f"Erreur mise à jour capital: {exc}")


@router.post("/add", summary="Ajouter du capital")
async def add_capital(
    request: CapitalAddRequest,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Ajoute du capital (investissement supplémentaire).
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        capital = get_or_create_capital(db, tenant_id, pharmacy.id, request.branch_id)
        
        previous_capital = capital.current_capital
        amount = Decimal(str(request.amount))
        
        capital.add_capital(amount, request.category, request.description)
        
        # Créer la transaction
        create_transaction(
            db=db,
            capital_id=capital.id,
            tenant_id=tenant_id,
            pharmacy_id=pharmacy.id,
            branch_id=request.branch_id,
            transaction_type="increase",
            transaction_category=request.category,
            amount=amount,
            previous_capital=previous_capital,
            new_capital=capital.current_capital,
            reference_id=request.reference_id,
            reference_type=request.reference_type,
            description=request.description or f"Ajout de capital: {amount} ({request.category})",
            created_by=current_user.id
        )
        
        db.commit()
        db.refresh(capital)
        
        logger.info(f"Capital ajouté: {amount} ({request.category}) par {current_user.email}")
        
        return {
            "message": f"Capital ajouté avec succès: {amount}",
            "capital": capital.to_dict(),
            "transaction": {
                "previous_capital": float(previous_capital),
                "new_capital": float(capital.current_capital),
                "amount_added": float(amount),
                "category": request.category
            }
        }
        
    except HTTPException:
        db.rollback()
        raise
    except ValueError as ve:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur ajout capital")
        raise HTTPException(status_code=500, detail=f"Erreur ajout capital: {exc}")


@router.post("/withdraw", summary="Retirer du capital")
async def withdraw_capital(
    request: CapitalWithdrawRequest,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Retire du capital (prélèvement).
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        capital = get_or_create_capital(db, tenant_id, pharmacy.id, request.branch_id)
        
        previous_capital = capital.current_capital
        amount = Decimal(str(request.amount))
        
        capital.remove_capital(amount, request.category, request.description)
        
        # Créer la transaction
        create_transaction(
            db=db,
            capital_id=capital.id,
            tenant_id=tenant_id,
            pharmacy_id=pharmacy.id,
            branch_id=request.branch_id,
            transaction_type="decrease",
            transaction_category=request.category,
            amount=amount,
            previous_capital=previous_capital,
            new_capital=capital.current_capital,
            reference_id=request.reference_id,
            reference_type=request.reference_type,
            description=request.description or f"Retrait de capital: {amount} ({request.category})",
            created_by=current_user.id
        )
        
        db.commit()
        db.refresh(capital)
        
        logger.info(f"Capital retiré: {amount} ({request.category}) par {current_user.email}")
        
        return {
            "message": f"Capital retiré avec succès: {amount}",
            "capital": capital.to_dict(),
            "transaction": {
                "previous_capital": float(previous_capital),
                "new_capital": float(capital.current_capital),
                "amount_withdrawn": float(amount),
                "category": request.category
            }
        }
        
    except HTTPException:
        db.rollback()
        raise
    except ValueError as ve:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur retrait capital")
        raise HTTPException(status_code=500, detail=f"Erreur retrait capital: {exc}")


@router.get("/performance", response_model=CapitalPerformanceResponse, summary="Performance du capital")
async def get_capital_performance(
    branch_id: Optional[UUID] = Query(None, description="ID de la succursale"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Analyse la performance du capital:
    - Comparaison capital initial vs actuel
    - Valeur du stock
    - Chiffre d'affaires
    - Dépenses
    - Dettes
    - ROI (Retour sur investissement)
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        capital = get_or_create_capital(db, tenant_id, pharmacy.id, branch_id)
        
        # Valeur réelle du stock
        stock_value = calculate_stock_value(db, tenant_id, pharmacy.id, branch_id)
        
        # Chiffre d'affaires sur la période
        total_sales = calculate_total_sales(db, tenant_id, pharmacy.id, branch_id, start_date, end_date)
        
        # Dépenses sur la période
        total_expenses = calculate_total_expenses(db, tenant_id, pharmacy.id, branch_id, start_date, end_date)
        
        # Dettes impayées
        total_debts = calculate_total_debts(db, tenant_id, pharmacy.id, branch_id)
        
        # Bénéfice net (CA - Dépenses)
        net_profit = total_sales - total_expenses
        
        # ROI (Retour sur investissement)
        if capital.initial_capital > 0:
            roi = ((capital.current_capital - capital.initial_capital) / capital.initial_capital) * 100
        else:
            roi = 0.0
        
        return CapitalPerformanceResponse(
            current_capital=float(capital.current_capital),
            initial_capital=float(capital.initial_capital),
            variation=float(capital.current_capital - capital.initial_capital),
            growth_rate=capital.capital_growth_rate,
            stock_value=float(stock_value),
            cash_value=float(capital.cash_capital),
            equipment_value=float(capital.equipment_capital),
            other_value=float(capital.other_capital),
            total_sales=float(total_sales),
            total_expenses=float(total_expenses),
            total_debts=float(total_debts),
            net_profit=float(net_profit),
            roi=float(roi)
        )
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur performance capital")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/evolution", summary="Évolution du capital dans le temps")
async def get_capital_evolution(
    branch_id: Optional[UUID] = Query(None, description="ID de la succursale"),
    period: str = Query("month", description="Période: day, week, month, year"),
    months: int = Query(12, ge=1, le=36, description="Nombre de mois à analyser"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Retourne l'évolution du capital dans le temps.
    Utile pour visualiser la progression ou régression.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        capital = get_or_create_capital(db, tenant_id, pharmacy.id, branch_id)
        
        # Récupérer les transactions
        transactions = db.query(CapitalTransaction).filter(
            CapitalTransaction.capital_id == capital.id,
            CapitalTransaction.created_at >= datetime.utcnow() - timedelta(days=months*30)
        ).order_by(CapitalTransaction.transaction_date).all()
        
        # Construire l'évolution
        evolution = []
        current_value = float(capital.initial_capital)
        
        # Grouper par période
        from collections import defaultdict
        period_data = defaultdict(lambda: {"date": None, "capital": 0, "transactions": []})
        
        for t in transactions:
            if period == "day":
                key = t.transaction_date.isoformat()
                date_obj = t.transaction_date
            elif period == "week":
                year, week, _ = t.transaction_date.isocalendar()
                key = f"{year}-W{week:02d}"
                # Premier jour de la semaine
                date_obj = t.transaction_date - timedelta(days=t.transaction_date.weekday())
            elif period == "month":
                key = t.transaction_date.strftime("%Y-%m")
                date_obj = t.transaction_date.replace(day=1)
            else:  # year
                key = str(t.transaction_date.year)
                date_obj = t.transaction_date.replace(month=1, day=1)
            
            if period_data[key]["date"] is None:
                period_data[key]["date"] = date_obj
            
            period_data[key]["capital"] = float(t.new_capital)
            period_data[key]["transactions"].append(t.to_dict())
        
        # Trier par date
        for key in sorted(period_data.keys()):
            data = period_data[key]
            evolution.append({
                "period": key,
                "date": data["date"].isoformat() if data["date"] else None,
                "capital": data["capital"],
                "transactions_count": len(data["transactions"]),
                "transactions": data["transactions"][:10]  # Limiter pour la performance
            })
        
        # Calculer les tendances
        if len(evolution) >= 2:
            first_capital = evolution[0]["capital"]
            last_capital = evolution[-1]["capital"]
            trend = "up" if last_capital > first_capital else "down" if last_capital < first_capital else "stable"
            variation_percent = ((last_capital - first_capital) / first_capital * 100) if first_capital > 0 else 0
        else:
            trend = "stable"
            variation_percent = 0
        
        return {
            "capital_id": str(capital.id),
            "pharmacy_id": str(pharmacy.id),
            "branch_id": str(branch_id) if branch_id else None,
            "period": period,
            "months_analyzed": months,
            "trend": trend,
            "variation_percent": round(variation_percent, 2),
            "evolution": evolution
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur évolution capital")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/transactions", summary="Historique des transactions")
async def get_capital_transactions(
    branch_id: Optional[UUID] = Query(None, description="ID de la succursale"),
    transaction_type: Optional[str] = Query(None, description="Type: initial, increase, decrease"),
    transaction_category: Optional[str] = Query(None, description="Catégorie: cash, stock, equipment, other"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère l'historique des transactions sur le capital.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        capital = get_or_create_capital(db, tenant_id, pharmacy.id, branch_id)
        
        query = db.query(CapitalTransaction).filter(
            CapitalTransaction.capital_id == capital.id
        )
        
        if transaction_type:
            query = query.filter(CapitalTransaction.transaction_type == transaction_type)
        
        if transaction_category:
            query = query.filter(CapitalTransaction.transaction_category == transaction_category)
        
        if start_date:
            query = query.filter(CapitalTransaction.transaction_date >= start_date)
        
        if end_date:
            query = query.filter(CapitalTransaction.transaction_date <= end_date)
        
        total = query.count()
        transactions = query.order_by(desc(CapitalTransaction.transaction_date), desc(CapitalTransaction.created_at)).offset(skip).limit(limit).all()
        
        # Calculer les totaux par catégorie
        totals = db.query(
            CapitalTransaction.transaction_category,
            func.sum(CapitalTransaction.amount).label("total_amount")
        ).filter(
            CapitalTransaction.capital_id == capital.id
        ).group_by(CapitalTransaction.transaction_category).all()
        
        return {
            "capital_id": str(capital.id),
            "total_transactions": total,
            "skip": skip,
            "limit": limit,
            "transactions": [t.to_dict() for t in transactions],
            "totals_by_category": [
                {"category": cat, "total": float(amt)} for cat, amt in totals if amt
            ]
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur récupération transactions")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


# =======================
# ROUTES POUR LE CHIFFRE D'AFFAIRES
# =======================

@router.get("/turnover", response_model=TurnoverStatsResponse, summary="Statistiques du chiffre d'affaires")
async def get_turnover_stats(
    branch_id: Optional[UUID] = Query(None, description="ID de la succursale"),
    period_type: str = Query("month", description="Type de période: day, week, month, year"),
    period_date: Optional[date] = Query(None, description="Date de référence"),
    compare_previous: bool = Query(True, description="Comparer avec la période précédente"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère les statistiques du chiffre d'affaires.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien", "vendeur"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer la période
        ref_date = period_date or date.today()
        
        if period_type == "day":
            start_date = ref_date
            end_date = ref_date
            previous_start = ref_date - timedelta(days=1)
            previous_end = previous_start
        elif period_type == "week":
            # Premier jour de la semaine (lundi)
            start_date = ref_date - timedelta(days=ref_date.weekday())
            end_date = start_date + timedelta(days=6)
            previous_start = start_date - timedelta(days=7)
            previous_end = end_date - timedelta(days=7)
        elif period_type == "month":
            start_date = ref_date.replace(day=1)
            if ref_date.month == 12:
                end_date = ref_date.replace(year=ref_date.year+1, month=1, day=1) - timedelta(days=1)
            else:
                end_date = ref_date.replace(month=ref_date.month+1, day=1) - timedelta(days=1)
            previous_start = start_date.replace(month=start_date.month-1 if start_date.month > 1 else 12, 
                                                year=start_date.year if start_date.month > 1 else start_date.year-1)
            previous_end = end_date.replace(month=end_date.month-1 if end_date.month > 1 else 12,
                                            year=end_date.year if end_date.month > 1 else end_date.year-1)
        else:  # year
            start_date = ref_date.replace(month=1, day=1)
            end_date = ref_date.replace(month=12, day=31)
            previous_start = start_date.replace(year=start_date.year-1)
            previous_end = end_date.replace(year=end_date.year-1)
        
        # Récupérer ou créer l'enregistrement de turnover
        turnover = db.query(Turnover).filter(
            Turnover.tenant_id == tenant_id,
            Turnover.pharmacy_id == pharmacy.id,
            Turnover.branch_id == branch_id if branch_id else Turnover.branch_id.is_(None),
            Turnover.period_date == start_date,
            Turnover.period_type == period_type
        ).first()
        
        if turnover:
            total_turnover = float(turnover.total_turnover)
            net_turnover = float(turnover.net_turnover)
            tax_amount = float(turnover.tax_amount)
            discount_amount = float(turnover.discount_amount)
            sales_count = turnover.sales_count
            items_sold = turnover.items_sold
        else:
            # Calculer à partir des ventes
            sales_query = db.query(
                func.sum(Sale.total_amount).label("total"),
                func.sum(Sale.total_amount - Sale.tax_amount).label("net"),
                func.sum(Sale.tax_amount).label("tax"),
                func.sum(Sale.discount_amount).label("discount"),
                func.count(Sale.id).label("count"),
                func.sum(SaleItem.quantity).label("items")
            ).join(SaleItem).filter(
                Sale.tenant_id == tenant_id,
                Sale.pharmacy_id == pharmacy.id,
                Sale.status == "completed",
                func.date(Sale.created_at) >= start_date,
                func.date(Sale.created_at) <= end_date
            )
            
            if branch_id:
                sales_query = sales_query.filter(Sale.branch_id == branch_id)
            
            result = sales_query.first()
            
            total_turnover = float(result.total or 0)
            net_turnover = float(result.net or 0)
            tax_amount = float(result.tax or 0)
            discount_amount = float(result.discount or 0)
            sales_count = int(result.count or 0)
            items_sold = int(result.items or 0)
        
        # Calculer les moyennes
        if period_type == "day":
            daily_average = total_turnover
            weekly_average = total_turnover * 7
            monthly_average = total_turnover * 30
        elif period_type == "week":
            daily_average = total_turnover / 7
            weekly_average = total_turnover
            monthly_average = total_turnover * 4
        elif period_type == "month":
            days_in_month = (end_date - start_date).days + 1
            daily_average = total_turnover / days_in_month if days_in_month > 0 else 0
            weekly_average = total_turnover / (days_in_month / 7) if days_in_month > 0 else 0
            monthly_average = total_turnover
        else:  # year
            daily_average = total_turnover / 365
            weekly_average = total_turnover / 52
            monthly_average = total_turnover / 12
        
        # Comparaison avec période précédente
        comparison = {}
        if compare_previous:
            previous_turnover = db.query(Turnover).filter(
                Turnover.tenant_id == tenant_id,
                Turnover.pharmacy_id == pharmacy.id,
                Turnover.branch_id == branch_id if branch_id else Turnover.branch_id.is_(None),
                Turnover.period_date == previous_start,
                Turnover.period_type == period_type
            ).first()
            
            if previous_turnover:
                previous_total = float(previous_turnover.total_turnover)
                if previous_total > 0:
                    variation = ((total_turnover - previous_total) / previous_total) * 100
                else:
                    variation = 100 if total_turnover > 0 else 0
                
                comparison = {
                    "previous_total": previous_total,
                    "variation": round(variation, 2),
                    "trend": "up" if variation > 0 else "down" if variation < 0 else "stable"
                }
            else:
                # Calculer à partir des ventes de la période précédente
                previous_sales_query = db.query(func.sum(Sale.total_amount)).filter(
                    Sale.tenant_id == tenant_id,
                    Sale.pharmacy_id == pharmacy.id,
                    Sale.status == "completed",
                    func.date(Sale.created_at) >= previous_start,
                    func.date(Sale.created_at) <= previous_end
                )
                
                if branch_id:
                    previous_sales_query = previous_sales_query.filter(Sale.branch_id == branch_id)
                
                previous_total = float(previous_sales_query.scalar() or 0)
                
                if previous_total > 0:
                    variation = ((total_turnover - previous_total) / previous_total) * 100
                else:
                    variation = 100 if total_turnover > 0 else 0
                
                comparison = {
                    "previous_total": previous_total,
                    "variation": round(variation, 2),
                    "trend": "up" if variation > 0 else "down" if variation < 0 else "stable"
                }
        
        return TurnoverStatsResponse(
            total_turnover=total_turnover,
            net_turnover=net_turnover,
            tax_amount=tax_amount,
            discount_amount=discount_amount,
            sales_count=sales_count,
            items_sold=items_sold,
            period_type=period_type,
            period_start=start_date,
            period_end=end_date,
            daily_average=round(daily_average, 2),
            weekly_average=round(weekly_average, 2),
            monthly_average=round(monthly_average, 2),
            comparison=comparison
        )
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur statistiques CA")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/turnover/trend", summary="Tendance du chiffre d'affaires")
async def get_turnover_trend(
    branch_id: Optional[UUID] = Query(None, description="ID de la succursale"),
    months: int = Query(12, ge=1, le=36, description="Nombre de mois"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Retourne la tendance du chiffre d'affaires sur plusieurs mois.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        end_date = date.today()
        start_date = end_date - timedelta(days=months*30)
        
        # Ventes par mois
        monthly_sales = db.query(
            extract('year', Sale.created_at).label('year'),
            extract('month', Sale.created_at).label('month'),
            func.sum(Sale.total_amount).label('total'),
            func.count(Sale.id).label('count')
        ).filter(
            Sale.tenant_id == tenant_id,
            Sale.pharmacy_id == pharmacy.id,
            Sale.status == "completed",
            func.date(Sale.created_at) >= start_date
        )
        
        if branch_id:
            monthly_sales = monthly_sales.filter(Sale.branch_id == branch_id)
        
        monthly_sales = monthly_sales.group_by(
            extract('year', Sale.created_at),
            extract('month', Sale.created_at)
        ).order_by(
            extract('year', Sale.created_at),
            extract('month', Sale.created_at)
        ).all()
        
        # Dépenses par mois
        monthly_expenses = db.query(
            extract('year', Cost.payment_date).label('year'),
            extract('month', Cost.payment_date).label('month'),
            func.sum(Cost.total_amount).label('total')
        ).filter(
            Cost.tenant_id == tenant_id,
            Cost.is_paid == True,
            Cost.status == "paid",
            Cost.payment_date >= start_date
        )
        
        if branch_id:
            monthly_expenses = monthly_expenses.filter(Cost.branch_id == branch_id)
        
        monthly_expenses = monthly_expenses.group_by(
            extract('year', Cost.payment_date),
            extract('month', Cost.payment_date)
        ).all()
        
        # Créer un dictionnaire des dépenses par mois
        expenses_by_month = {}
        for exp in monthly_expenses:
            key = f"{int(exp.year)}-{int(exp.month):02d}"
            expenses_by_month[key] = float(exp.total or 0)
        
        # Construire la tendance
        trend = []
        for sale in monthly_sales:
            year = int(sale.year)
            month = int(sale.month)
            key = f"{year}-{month:02d}"
            
            month_start = date(year, month, 1)
            if month == 12:
                month_end = date(year+1, 1, 1) - timedelta(days=1)
            else:
                month_end = date(year, month+1, 1) - timedelta(days=1)
            
            total = float(sale.total or 0)
            expenses = expenses_by_month.get(key, 0)
            profit = total - expenses
        
            trend.append({
                "year": year,
                "month": month,
                "month_name": month_start.strftime("%B"),
                "period": key,
                "total_turnover": total,
                "expenses": expenses,
                "profit": profit,
                "sales_count": int(sale.count or 0),
                "days_in_month": (month_end - month_start).days + 1
            })
        
        # Calculer la tendance globale
        if len(trend) >= 2:
            first_total = trend[0]["total_turnover"]
            last_total = trend[-1]["total_turnover"]
            overall_trend = "up" if last_total > first_total else "down" if last_total < first_total else "stable"
            overall_variation = ((last_total - first_total) / first_total * 100) if first_total > 0 else 0
        else:
            overall_trend = "stable"
            overall_variation = 0
        
        # Moyenne mobile sur 3 mois
        for i, item in enumerate(trend):
            if i >= 2:
                moving_avg = (trend[i-2]["total_turnover"] + trend[i-1]["total_turnover"] + item["total_turnover"]) / 3
                item["moving_average_3m"] = round(moving_avg, 2)
            else:
                item["moving_average_3m"] = item["total_turnover"]
        
        return {
            "pharmacy_id": str(pharmacy.id),
            "branch_id": str(branch_id) if branch_id else None,
            "months_analyzed": months,
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
            "overall_trend": overall_trend,
            "overall_variation": round(overall_variation, 2),
            "trend": trend
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur tendance CA")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.post("/turnover/sync", summary="Synchroniser le chiffre d'affaires")
async def sync_turnover(
    branch_id: Optional[UUID] = Query(None, description="ID de la succursale"),
    force_update: bool = Query(False, description="Forcer la mise à jour de toutes les périodes"),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Synchronise les données de chiffre d'affaires pour toutes les périodes.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer les périodes à synchroniser
        periods_to_sync = []
        
        if force_update:
            # Synchroniser toutes les périodes depuis le début
            first_sale = db.query(func.min(func.date(Sale.created_at))).filter(
                Sale.tenant_id == tenant_id,
                Sale.pharmacy_id == pharmacy.id,
                Sale.status == "completed"
            ).scalar()
            
            if first_sale:
                start_date = first_sale
            else:
                start_date = date.today() - timedelta(days=365)
        else:
            # Synchroniser seulement les 3 derniers mois
            start_date = date.today() - timedelta(days=90)
        
        end_date = date.today()
        
        # Générer toutes les périodes
        current = start_date
        period_types = ["day", "week", "month", "year"]
        
        while current <= end_date:
            for period_type in period_types:
                if period_type == "day":
                    period_start = current
                    period_end = current
                elif period_type == "week":
                    period_start = current - timedelta(days=current.weekday())
                    period_end = period_start + timedelta(days=6)
                elif period_type == "month":
                    period_start = current.replace(day=1)
                    if current.month == 12:
                        period_end = current.replace(year=current.year+1, month=1, day=1) - timedelta(days=1)
                    else:
                        period_end = current.replace(month=current.month+1, day=1) - timedelta(days=1)
                else:  # year
                    period_start = current.replace(month=1, day=1)
                    period_end = current.replace(month=12, day=31)
                
                # Calculer le CA pour cette période
                sales_query = db.query(
                    func.sum(Sale.total_amount).label("total"),
                    func.sum(Sale.total_amount - Sale.tax_amount).label("net"),
                    func.sum(Sale.tax_amount).label("tax"),
                    func.sum(Sale.discount_amount).label("discount"),
                    func.count(Sale.id).label("count"),
                    func.sum(SaleItem.quantity).label("items")
                ).join(SaleItem).filter(
                    Sale.tenant_id == tenant_id,
                    Sale.pharmacy_id == pharmacy.id,
                    Sale.status == "completed",
                    func.date(Sale.created_at) >= period_start,
                    func.date(Sale.created_at) <= period_end
                )
                
                if branch_id:
                    sales_query = sales_query.filter(Sale.branch_id == branch_id)
                
                result = sales_query.first()
                
                # Mettre à jour ou créer l'enregistrement
                turnover = db.query(Turnover).filter(
                    Turnover.tenant_id == tenant_id,
                    Turnover.pharmacy_id == pharmacy.id,
                    Turnover.branch_id == branch_id if branch_id else Turnover.branch_id.is_(None),
                    Turnover.period_date == period_start,
                    Turnover.period_type == period_type
                ).first()
                
                if turnover:
                    turnover.total_turnover = Decimal(str(result.total or 0))
                    turnover.net_turnover = Decimal(str(result.net or 0))
                    turnover.tax_amount = Decimal(str(result.tax or 0))
                    turnover.discount_amount = Decimal(str(result.discount or 0))
                    turnover.sales_count = int(result.count or 0)
                    turnover.items_sold = int(result.items or 0)
                else:
                    turnover = Turnover(
                        tenant_id=tenant_id,
                        pharmacy_id=pharmacy.id,
                        branch_id=branch_id,
                        total_turnover=Decimal(str(result.total or 0)),
                        net_turnover=Decimal(str(result.net or 0)),
                        tax_amount=Decimal(str(result.tax or 0)),
                        discount_amount=Decimal(str(result.discount or 0)),
                        sales_count=int(result.count or 0),
                        items_sold=int(result.items or 0),
                        period_date=period_start,
                        period_type=period_type
                    )
                    db.add(turnover)
            
            # Passer au jour suivant
            current += timedelta(days=1)
        
        db.commit()
        
        logger.info(f"Synchronisation CA terminée par {current_user.email}")
        
        return {
            "message": "Synchronisation du chiffre d'affaires terminée",
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
            "force_update": force_update,
            "branch_id": str(branch_id) if branch_id else None
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur synchronisation CA")
        raise HTTPException(status_code=500, detail=f"Erreur synchronisation CA: {exc}")


# =======================
# ROUTES POUR LES COMPTES COMPTABLES
# =======================

@router.get("/accounts", summary="Comptes comptables")
async def get_accounts(
    branch_id: Optional[UUID] = Query(None, description="ID de la succursale"),
    year: Optional[int] = Query(None, description="Année comptable"),
    account_type: Optional[str] = Query(None, description="asset, liability, equity, income, expense"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère les comptes comptables selon la nomenclature SYSCOHADA.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        current_year = year or date.today().year
        
        # Créer les comptes si nécessaire
        create_accounting_period(db, tenant_id, pharmacy.id, current_year, branch_id)
        
        query = db.query(CapitalAccount).filter(
            CapitalAccount.tenant_id == tenant_id,
            CapitalAccount.pharmacy_id == pharmacy.id,
            CapitalAccount.branch_id == branch_id if branch_id else CapitalAccount.branch_id.is_(None),
            CapitalAccount.period_year == current_year
        )
        
        if account_type:
            query = query.filter(CapitalAccount.account_type == account_type)
        
        accounts = query.order_by(CapitalAccount.account_code).all()
        
        # Grouper par type
        grouped = {}
        for account in accounts:
            if account.account_type not in grouped:
                grouped[account.account_type] = []
            grouped[account.account_type].append(account.to_dict())
        
        return {
            "pharmacy_id": str(pharmacy.id),
            "branch_id": str(branch_id) if branch_id else None,
            "year": current_year,
            "accounts": grouped,
            "total_balance": sum(float(a.balance) for a in accounts),
            "total_debit": sum(float(a.debit) for a in accounts),
            "total_credit": sum(float(a.credit) for a in accounts)
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur récupération comptes")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/accounts/{account_code}", summary="Détails d'un compte comptable")
async def get_account_details(
    account_code: str,
    branch_id: Optional[UUID] = Query(None, description="ID de la succursale"),
    year: Optional[int] = Query(None, description="Année comptable"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère les détails d'un compte comptable spécifique.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant", "pharmacien"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        current_year = year or date.today().year
        
        account = db.query(CapitalAccount).filter(
            CapitalAccount.tenant_id == tenant_id,
            CapitalAccount.pharmacy_id == pharmacy.id,
            CapitalAccount.branch_id == branch_id if branch_id else CapitalAccount.branch_id.is_(None),
            CapitalAccount.period_year == current_year,
            CapitalAccount.account_code == account_code
        ).first()
        
        if not account:
            raise HTTPException(status_code=404, detail="Compte non trouvé")
        
        return account.to_dict()
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur récupération compte")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


# =======================
# ROUTES POUR LES RAPPORTS FINANCIERS
# =======================

@router.get("/financial-report", summary="Rapport financier complet")
async def get_financial_report(
    branch_id: Optional[UUID] = Query(None, description="ID de la succursale"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    format: str = Query("json", description="Format: json, csv, excel"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user)
):
    """
    Génère un rapport financier complet incluant:
    - Bilan (Actif, Passif, Capitaux propres)
    - Compte de résultat (Produits, Charges, Résultat)
    - Trésorerie
    - Indicateurs de performance
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
        
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        tenant_id = current_tenant.id if current_tenant else None
        
        # Dates par défaut (mois en cours)
        if not end_date:
            end_date = date.today()
        if not start_date:
            start_date = end_date.replace(day=1)
        
        # Récupérer le capital
        capital = get_or_create_capital(db, tenant_id, pharmacy.id, branch_id)
        
        # Valeur du stock
        stock_value = calculate_stock_value(db, tenant_id, pharmacy.id, branch_id)
        
        # Chiffre d'affaires
        total_sales = calculate_total_sales(db, tenant_id, pharmacy.id, branch_id, start_date, end_date)
        
        # Dépenses
        total_expenses = calculate_total_expenses(db, tenant_id, pharmacy.id, branch_id, start_date, end_date)
        
        # Dettes
        total_debts = calculate_total_debts(db, tenant_id, pharmacy.id, branch_id)
        
        # Créances (à implémenter si nécessaire)
        total_receivables = Decimal('0')
        
        # Calculer les totaux
        total_assets = stock_value + capital.cash_capital + capital.equipment_capital + capital.other_capital + total_receivables
        total_liabilities = total_debts
        equity = total_assets - total_liabilities
        
        # Résultat
        gross_profit = total_sales - total_expenses
        net_profit = gross_profit
        
        # Indicateurs
        if capital.initial_capital > 0:
            roe = (net_profit / capital.initial_capital) * 100  # Return on Equity
        else:
            roe = 0
        
        if total_sales > 0:
            profit_margin = (net_profit / total_sales) * 100
        else:
            profit_margin = 0
        
        report = {
            "pharmacy": {
                "id": str(pharmacy.id),
                "name": pharmacy.name,
                "license_number": pharmacy.license_number
            },
            "branch": {
                "id": str(branch_id) if branch_id else None,
                "name": None
            },
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": (end_date - start_date).days + 1
            },
            "balance_sheet": {
                "assets": {
                    "stock_value": float(stock_value),
                    "cash_capital": float(capital.cash_capital),
                    "equipment_capital": float(capital.equipment_capital),
                    "other_assets": float(capital.other_capital),
                    "receivables": float(total_receivables),
                    "total_assets": float(total_assets)
                },
                "liabilities": {
                    "debts": float(total_debts),
                    "total_liabilities": float(total_liabilities)
                },
                "equity": {
                    "initial_capital": float(capital.initial_capital),
                    "current_capital": float(capital.current_capital),
                    "retained_earnings": float(capital.current_capital - capital.initial_capital),
                    "total_equity": float(equity)
                }
            },
            "income_statement": {
                "revenue": {
                    "total_sales": float(total_sales),
                    "total_revenue": float(total_sales)
                },
                "expenses": {
                    "total_expenses": float(total_expenses),
                    "expenses_breakdown": []
                },
                "profit": {
                    "gross_profit": float(gross_profit),
                    "net_profit": float(net_profit),
                    "profit_margin": round(profit_margin, 2)
                }
            },
            "cash_flow": {
                "beginning_cash": float(capital.initial_capital),
                "cash_in": float(total_sales),
                "cash_out": float(total_expenses),
                "ending_cash": float(capital.current_capital)
            },
            "key_metrics": {
                "roe": round(roe, 2),
                "profit_margin": round(profit_margin, 2),
                "stock_turnover": 0,  # À calculer
                "debt_to_equity": float(total_debts / equity) if equity > 0 else 0,
                "current_ratio": float(total_assets / total_liabilities) if total_liabilities > 0 else 0
            }
        }
        
        # Ajouter les détails des dépenses par catégorie
        expense_breakdown = db.query(
            Cost.category,
            func.sum(Cost.total_amount).label("total")
        ).filter(
            Cost.tenant_id == tenant_id,
            Cost.pharmacy_id == pharmacy.id,
            Cost.branch_id == branch_id if branch_id else Cost.branch_id.is_(None),
            Cost.is_paid == True,
            Cost.status == "paid",
            Cost.payment_date >= start_date,
            Cost.payment_date <= end_date
        ).group_by(Cost.category).all()
        
        report["income_statement"]["expenses"]["expenses_breakdown"] = [
            {"category": cat, "amount": float(total)} for cat, total in expense_breakdown
        ]
        
        # Export si demandé
        if format != "json":
            import pandas as pd
            import io
            
            # Créer un DataFrame pour l'export
            export_data = []
            
            # Bilan
            export_data.append(["=== BILAN ===", ""])
            export_data.append(["ACTIF", "Montant"])
            export_data.append(["Stock", report["balance_sheet"]["assets"]["stock_value"]])
            export_data.append(["Caisse", report["balance_sheet"]["assets"]["cash_capital"]])
            export_data.append(["Équipement", report["balance_sheet"]["assets"]["equipment_capital"]])
            export_data.append(["Autres actifs", report["balance_sheet"]["assets"]["other_assets"]])
            export_data.append(["Total Actif", report["balance_sheet"]["assets"]["total_assets"]])
            export_data.append([""])
            export_data.append(["PASSIF", ""])
            export_data.append(["Dettes", report["balance_sheet"]["liabilities"]["debts"]])
            export_data.append(["Total Passif", report["balance_sheet"]["liabilities"]["total_liabilities"]])
            export_data.append([""])
            export_data.append(["CAPITAUX PROPRES", ""])
            export_data.append(["Capital initial", report["balance_sheet"]["equity"]["initial_capital"]])
            export_data.append(["Capital actuel", report["balance_sheet"]["equity"]["current_capital"]])
            export_data.append(["Total Capitaux Propres", report["balance_sheet"]["equity"]["total_equity"]])
            export_data.append([""])
            export_data.append(["=== COMPTE DE RÉSULTAT ===", ""])
            export_data.append(["PRODUITS", ""])
            export_data.append(["Chiffre d'affaires", report["income_statement"]["revenue"]["total_sales"]])
            export_data.append(["Total Produits", report["income_statement"]["revenue"]["total_revenue"]])
            export_data.append([""])
            export_data.append(["CHARGES", ""])
            for exp in report["income_statement"]["expenses"]["expenses_breakdown"]:
                export_data.append([exp["category"], exp["amount"]])
            export_data.append(["Total Charges", report["income_statement"]["expenses"]["total_expenses"]])
            export_data.append([""])
            export_data.append(["RÉSULTAT", ""])
            export_data.append(["Bénéfice net", report["income_statement"]["profit"]["net_profit"]])
            export_data.append(["Marge bénéficiaire", f"{report['income_statement']['profit']['profit_margin']}%"])
            export_data.append([""])
            export_data.append(["=== INDICATEURS ===", ""])
            export_data.append(["ROE", f"{report['key_metrics']['roe']}%"])
            export_data.append(["Dette / Capitaux propres", report["key_metrics"]["debt_to_equity"]])
            export_data.append(["Ratio de liquidité", report["key_metrics"]["current_ratio"]])
            
            df = pd.DataFrame(export_data)
            
            if format == "csv":
                output = io.StringIO()
                df.to_csv(output, index=False, header=False, encoding='utf-8-sig')
                content = output.getvalue().encode('utf-8')
                media_type = "text/csv"
                filename = f"rapport_financier_{start_date}_{end_date}.csv"
            else:  # excel
                output = io.BytesIO()
                df.to_excel(output, index=False, header=False, engine="openpyxl")
                content = output.getvalue()
                media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                filename = f"rapport_financier_{start_date}_{end_date}.xlsx"
            
            from fastapi.responses import Response
            return Response(
                content=content,
                media_type=media_type,
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
        
        return report
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur génération rapport financier")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")
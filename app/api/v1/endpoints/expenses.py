# app/api/v1/endpoints/expenses.py
from typing import List, Optional, Any, Dict
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from datetime import datetime, date
import uuid

from app.api import deps
from app.db.session import get_db
from app.models.user import User
from app.models.pharmacy import Pharmacy
from app.schemas.expense import (
    ExpenseCreate, ExpenseUpdate, ExpenseResponse, ExpenseListResponse,
    ExpenseApprove, ExpenseByBranchResponse, ExpenseByUserResponse,
    ExpenseSummaryResponse, ExpenseFilters
)
from app.services.expense_service import ExpenseService
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# =====================================
# CRUD OPERATIONS
# =====================================

@router.get("/", response_model=ExpenseListResponse)
def get_expenses(
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    branch_id: Optional[str] = Query(None, description="Filtrer par branche"),
    user_id: Optional[str] = Query(None, description="Filtrer par utilisateur"),
    expense_type: Optional[str] = Query(None, description="Type de dépense"),
    approval_status: Optional[str] = Query(None, description="Statut d'approbation"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    min_amount: Optional[float] = Query(None, description="Montant minimum"),
    max_amount: Optional[float] = Query(None, description="Montant maximum"),
    search: Optional[str] = Query(None, description="Recherche textuelle"),
    page: int = Query(1, ge=1, description="Page"),
    per_page: int = Query(20, ge=1, le=100, description="Éléments par page"),
    sort_by: str = Query("expense_date", description="Tri par colonne"),
    sort_desc: bool = Query(True, description="Tri décroissant")
):
    """
    Récupère la liste des dépenses avec filtres et pagination
    """
    # Construire les filtres
    filters = ExpenseFilters(
        branch_id=branch_id,
        user_id=user_id,
        expense_type=expense_type,
        approval_status=approval_status,
        start_date=start_date,
        end_date=end_date,
        min_amount=min_amount,
        max_amount=max_amount,
        search=search
    )
    
    # Récupérer les dépenses
    expenses, total = ExpenseService.get_expenses(
        db=db,
        tenant_id=current_user.tenant_id,
        filters=filters,
        page=page,
        per_page=per_page,
        sort_by=sort_by,
        sort_desc=sort_desc
    )
    
    # Enrichir avec les noms des branches et utilisateurs
    items = []
    for expense in expenses:
        item = ExpenseResponse.model_validate(expense)
        # Ajouter les noms (optionnel, à optimiser avec des jointures)
        if expense.branch_id:
            branch = expense.branch
            item.branch_name = branch.name if branch else None
        if expense.user_id:
            user = expense.user
            item.user_name = user.nom_complet if user else None
        items.append(item)
    
    return ExpenseListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page
    )

@router.post("/", response_model=ExpenseResponse, status_code=status.HTTP_201_CREATED)
def create_expense(
    expense_data: ExpenseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    """
    Crée une nouvelle dépense
    """
    try:
        expense = ExpenseService.create_expense(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            expense_data=expense_data
        )
        return ExpenseResponse.model_validate(expense)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get("/{expense_id}", response_model=ExpenseResponse)
def get_expense(
    expense_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    """
    Récupère les détails d'une dépense
    """
    expense = ExpenseService.get_expense(
        db=db,
        expense_id=expense_id,
        tenant_id=current_user.tenant_id
    )
    
    if not expense:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dépense non trouvée")
    
    response = ExpenseResponse.model_validate(expense)
    if expense.branch_id:
        branch = expense.branch
        response.branch_name = branch.name if branch else None
    if expense.user_id:
        user = expense.user
        response.user_name = user.nom_complet if user else None
    if expense.approved_by:
        approver = expense.approver
        response.approver_name = approver.nom_complet if approver else None
    
    return response

@router.put("/{expense_id}", response_model=ExpenseResponse)
def update_expense(
    expense_id: str,
    expense_data: ExpenseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    """
    Met à jour une dépense (uniquement si non approuvée)
    """
    try:
        expense = ExpenseService.update_expense(
            db=db,
            expense_id=expense_id,
            tenant_id=current_user.tenant_id,
            expense_data=expense_data
        )
        
        if not expense:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dépense non trouvée")
        
        return ExpenseResponse.model_validate(expense)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(
    expense_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    """
    Supprime une dépense (uniquement si non approuvée)
    """
    try:
        deleted = ExpenseService.delete_expense(
            db=db,
            expense_id=expense_id,
            tenant_id=current_user.tenant_id
        )
        
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dépense non trouvée")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

# =====================================
# APPROVAL OPERATIONS
# =====================================

@router.post("/{expense_id}/approve", response_model=ExpenseResponse)
def approve_expense(
    expense_id: str,
    approval_data: ExpenseApprove,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    """
    Approuve ou rejette une dépense
    """
    try:
        expense = ExpenseService.approve_expense(
            db=db,
            expense_id=expense_id,
            tenant_id=current_user.tenant_id,
            approver_id=current_user.id,
            approved=approval_data.approved,
            rejection_reason=approval_data.rejection_reason
        )
        
        if not expense:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dépense non trouvée")
        
        return ExpenseResponse.model_validate(expense)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

# =====================================
# REPORTS
# =====================================

@router.get("/reports/by-branch", response_model=List[ExpenseByBranchResponse])
def get_expenses_by_branch_report(
    start_date: date = Query(..., description="Date de début"),
    end_date: date = Query(..., description="Date de fin"),
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    """
    Rapport des dépenses par branche
    """
    if start_date > end_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La date de début doit être antérieure à la date de fin")
    
    results = ExpenseService.get_expenses_by_branch(
        db=db,
        tenant_id=current_user.tenant_id,
        start_date=start_date,
        end_date=end_date
    )
    
    return [ExpenseByBranchResponse(**r) for r in results]

@router.get("/reports/by-user", response_model=List[ExpenseByUserResponse])
def get_expenses_by_user_report(
    start_date: date = Query(..., description="Date de début"),
    end_date: date = Query(..., description="Date de fin"),
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    """
    Rapport des dépenses par utilisateur
    """
    if start_date > end_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La date de début doit être antérieure à la date de fin")
    
    results = ExpenseService.get_expenses_by_user(
        db=db,
        tenant_id=current_user.tenant_id,
        start_date=start_date,
        end_date=end_date
    )
    
    return [ExpenseByUserResponse(**r) for r in results]

@router.get("/reports/summary", response_model=ExpenseSummaryResponse)
def get_expenses_summary(
    start_date: date = Query(..., description="Date de début"),
    end_date: date = Query(..., description="Date de fin"),
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    """
    Résumé global des dépenses
    """
    if start_date > end_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La date de début doit être antérieure à la date de fin")
    
    summary = ExpenseService.get_summary(
        db=db,
        tenant_id=current_user.tenant_id,
        start_date=start_date,
        end_date=end_date
    )
    
    return ExpenseSummaryResponse(**summary)

@router.get("/{expense_id}/details")
def get_expense_details(
    expense_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user)
):
    """
    Récupère les détails complets d'une dépense avec les infos des relations
    """
    details = ExpenseService.get_expense_with_details(
        db=db,
        expense_id=expense_id,
        tenant_id=current_user.tenant_id
    )
    
    if not details:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dépense non trouvée")
    
    return details


@router.post("/batch", response_model=Dict[str, Any])
async def batch_create_expenses(
    batch_data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    current_pharmacy: Optional[Pharmacy] = Depends(deps.get_current_pharmacy_entity),
):
    """
    Endpoint batch pour synchroniser plusieurs dépenses.
    Utilisé par le sync_manager mobile.
    """
    try:
        expenses_data = batch_data.get("expenses", [])
        branch_id = batch_data.get("branch_id")
        batch_id = batch_data.get("batch_id", str(uuid.uuid4()))
        
        if not current_pharmacy:
            return {
                "success": False,
                "error": "Aucune pharmacie active",
                "synced_ids": []
            }
        
        synced_ids = []
        errors = []
        
        for expense_data in expenses_data:
            try:
                # Valider et convertir les données
                expense_create = ExpenseCreate(
                    expense_type=expense_data.get("expense_type", "other"),
                    amount=float(expense_data.get("amount", 0)),
                    description=expense_data.get("description", ""),
                    expense_date=expense_data.get("expense_date", datetime.now().date().isoformat()),
                    supplier=expense_data.get("supplier"),
                    payment_method=expense_data.get("payment_method", "cash"),
                    notes=expense_data.get("notes", ""),
                    invoice_number=expense_data.get("invoice_number"),
                    branch_id=branch_id or expense_data.get("branch_id")
                )
                
                expense = ExpenseService.create_expense(
                    db=db,
                    tenant_id=current_user.tenant_id,
                    user_id=current_user.id,
                    expense_data=expense_create
                )
                
                synced_ids.append(expense_data.get("local_id", str(expense.id)))
                
            except Exception as e:
                errors.append({
                    "local_id": expense_data.get("local_id"),
                    "error": str(e)
                })
        
        db.commit()
        
        return {
            "success": True,
            "synced_ids": synced_ids,
            "errors": errors,
            "batch_id": batch_id,
            "total_synced": len(synced_ids),
            "total_errors": len(errors)
        }
        
    except Exception as e:
        logger.error(f"Erreur batch expenses: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "synced_ids": []
        }
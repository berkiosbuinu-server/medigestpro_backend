# app/api/routes/debts.py
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from typing import List, Optional
from uuid import UUID
from datetime import datetime, date, timedelta
import logging
from fastapi import BackgroundTasks  
from app.tasks.debts import send_debt_reminders_task  
from app.db.session import get_db
from app.models.debt import Debt, DebtPayment
from app.models.customer import Customer
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.debt import (
    DebtInDB, DebtPaymentCreate, DebtPaymentInDB,
    DebtSummary, DebtAnalytics
)
from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission

router = APIRouter(prefix="/debts", tags=["Dettes"])
logger = logging.getLogger(__name__)

@router.get("/", response_model=List[DebtInDB])
@require_permission("debts_view")
def list_debts(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    customer_id: Optional[UUID] = None,
    status: Optional[str] = None,
    overdue_only: bool = Query(False, description="Afficher uniquement les dettes en retard"),
    min_amount: Optional[float] = Query(None, ge=0),
    max_amount: Optional[float] = Query(None, ge=0),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
):
    """
    Liste les dettes avec filtres
    """
    query = db.query(Debt).filter(Debt.tenant_id == current_tenant.id)
    
    # Appliquer les filtres
    if customer_id:
        query = query.filter(Debt.customer_id == customer_id)
    
    if status:
        query = query.filter(Debt.status == status)
    
    if overdue_only:
        today = date.today()
        query = query.filter(
            Debt.due_date < today,
            Debt.remaining_amount > 0,
            Debt.status.in_(["pending", "partial"])
        )
    
    if min_amount is not None:
        query = query.filter(Debt.total_amount >= min_amount)
    
    if max_amount is not None:
        query = query.filter(Debt.total_amount <= max_amount)
    
    if start_date:
        query = query.filter(Debt.created_at >= start_date)
    
    if end_date:
        query = query.filter(Debt.created_at <= end_date)
    
    # Trier par date d'échéance croissante
    debts = query.order_by(
        Debt.due_date.asc()
    ).offset(skip).limit(limit).all()
    
    return debts

@router.post("/{debt_id}/payments", response_model=DebtPaymentInDB)
@require_permission("debts_manage")
def record_payment(
    debt_id: UUID,
    payment_data: DebtPaymentCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Enregistre un paiement pour une dette
    """
    # Récupérer la dette
    debt = db.query(Debt).filter(
        Debt.id == debt_id,
        Debt.tenant_id == current_tenant.id
    ).first()
    
    if not debt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dette non trouvée"
        )
    
    if debt.status == "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cette dette est déjà entièrement payée"
        )
    
    if payment_data.amount > debt.remaining_amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Le montant du paiement ({payment_data.amount}) dépasse le solde restant ({debt.remaining_amount})"
        )
    
    try:
        # Créer le paiement
        payment = DebtPayment(
            tenant_id=current_tenant.id,
            debt_id=debt_id,
            amount=payment_data.amount,
            payment_method=payment_data.payment_method,
            payment_date=payment_data.payment_date,
            reference=payment_data.reference,
            notes=payment_data.notes,
            received_by=current_user.id
        )
        
        db.add(payment)
        
        # Mettre à jour le statut de la dette
        debt.remaining_amount -= payment_data.amount
        debt.total_paid += payment_data.amount
        
        if debt.remaining_amount <= 0:
            debt.status = "paid"
            debt.paid_at = datetime.utcnow()
        elif debt.remaining_amount < debt.total_amount:
            debt.status = "partial"
        
        # Vérifier si c'est en retard après paiement
        if payment_data.payment_date > debt.due_date:
            debt.is_overdue = True
        
        db.commit()
        db.refresh(payment)
        
        logger.info(f"Paiement enregistré pour la dette {debt.debt_number}: {payment_data.amount}")
        
        return payment
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de l'enregistrement du paiement: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'enregistrement du paiement"
        )

@router.get("/summary", response_model=DebtSummary)
@require_permission("debts_view")
def get_debt_summary(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Obtient un résumé des dettes
    """
    today = date.today()
    
    # Calculer les statistiques
    total_debts = db.query(func.sum(Debt.total_amount)).filter(
        Debt.tenant_id == current_tenant.id,
        Debt.status.in_(["pending", "partial"])
    ).scalar() or 0.0
    
    total_received = db.query(func.sum(Debt.total_paid)).filter(
        Debt.tenant_id == current_tenant.id
    ).scalar() or 0.0
    
    total_overdue = db.query(func.sum(Debt.remaining_amount)).filter(
        Debt.tenant_id == current_tenant.id,
        Debt.is_overdue == True,
        Debt.remaining_amount > 0
    ).scalar() or 0.0
    
    total_clients = db.query(func.count(func.distinct(Debt.customer_id))).filter(
        Debt.tenant_id == current_tenant.id,
        Debt.remaining_amount > 0
    ).scalar() or 0
    
    # Dettes par statut
    status_counts = db.query(
        Debt.status,
        func.count(Debt.id).label('count'),
        func.sum(Debt.remaining_amount).label('amount')
    ).filter(
        Debt.tenant_id == current_tenant.id
    ).group_by(Debt.status).all()
    
    status_summary = {
        status: {"count": count, "amount": amount or 0.0}
        for status, count, amount in status_counts
    }
    
    return DebtSummary(
        total_amount=total_debts,
        total_received=total_received,
        total_overdue=total_overdue,
        total_clients=total_clients,
        status_summary=status_summary
    )

@router.get("/analytics", response_model=DebtAnalytics)
@require_permission("debts_view")
def get_debt_analytics(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    period: str = Query("month", pattern="^(day|week|month|quarter|year)$"),
    limit: int = Query(10, ge=1, le=50)
):
    """
    Obtient des analyses sur les dettes
    """
    # Dernières dettes créées
    recent_debts = db.query(Debt).filter(
        Debt.tenant_id == current_tenant.id
    ).order_by(
        Debt.created_at.desc()
    ).limit(limit).all()
    
    # Dettes les plus anciennes
    oldest_debts = db.query(Debt).filter(
        Debt.tenant_id == current_tenant.id,
        Debt.remaining_amount > 0
    ).order_by(
        Debt.due_date.asc()
    ).limit(limit).all()
    
    # Clients avec les plus grandes dettes
    top_debtors = db.query(
        Customer,
        func.sum(Debt.remaining_amount).label('total_debt')
    ).join(
        Debt, Customer.id == Debt.customer_id
    ).filter(
        Debt.tenant_id == current_tenant.id,
        Debt.remaining_amount > 0
    ).group_by(Customer.id).order_by(
        func.sum(Debt.remaining_amount).desc()
    ).limit(limit).all()
    
    # Distribution par méthode de paiement
    payment_methods = db.query(
        DebtPayment.payment_method,
        func.count(DebtPayment.id).label('count'),
        func.sum(DebtPayment.amount).label('amount')
    ).filter(
        DebtPayment.tenant_id == current_tenant.id
    ).group_by(DebtPayment.payment_method).all()
    
    return DebtAnalytics(
        recent_debts=recent_debts,
        oldest_debts=oldest_debts,
        top_debtors=top_debtors,
        payment_methods=payment_methods
    )

@router.post("/reminders/send")
@require_permission("debts_manage")
def send_debt_reminders(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Envoie des rappels pour les dettes en retard
    """
    today = date.today()
    
    # Trouver les dettes en retard
    overdue_debts = db.query(Debt).join(Customer).filter(
        Debt.tenant_id == current_tenant.id,
        Debt.due_date < today,
        Debt.remaining_amount > 0,
        Debt.status.in_(["pending", "partial"])
    ).all()
    
    if not overdue_debts:
        return {"message": "Aucune dette en retard à rappeler"}
    
    # Lancer l'envoi des rappels en arrière-plan
    background_tasks.add_task(
        send_debt_reminders_task,
        debts=overdue_debts,
        tenant_id=current_tenant.id,
        user_id=current_user.id
    )
    
    return {
        "message": f"Rappels programmés pour {len(overdue_debts)} dettes en retard",
        "count": len(overdue_debts)
    }

# app/api/v1/endpoints/invoices.py
"""
Endpoints pour la gestion des factures (invoices)
Accessible aux administrateurs et aux propriétaires de pharmacies
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_, distinct
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, date, timedelta
import logging

from app.db.session import get_db
from app.models.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.pharmacy import Pharmacy
from app.models.user import User
from app.models.tenant import Tenant
from app.models.user_pharmacy import UserPharmacy
from app.schemas.invoice import (
    InvoiceResponse,
    InvoiceDetailResponse,
    InvoiceListResponse,
    InvoiceCreate,
    InvoiceUpdate,
    InvoiceFilter,
    InvoiceStatsResponse,
    InvoicePaymentCreate,
    InvoicePaymentResponse
)
from app.api.deps import (
    get_current_tenant,
    get_current_user,
    get_current_active_user,
    require_permission
)
from app.services.invoice_service import (
    create_invoice,
    update_invoice,
    get_invoice,
    get_invoices,
    generate_invoice_pdf
)

router = APIRouter(prefix="/invoices", tags=["Factures"])
logger = logging.getLogger(__name__)


# =======================
# Helpers
# =======================

def get_user_accessible_pharmacy_ids(
    db: Session,
    user_id: UUID,
    tenant_id: Optional[UUID] = None
) -> List[UUID]:
    """
    Récupère la liste des IDs des pharmacies accessibles par l'utilisateur.
    Les super-admin et admin voient toutes les pharmacies.
    """
    from app.models.user import UserRole
    
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        return []
    
    # Super-admin et admin voient toutes les pharmacies
    if user.role in [UserRole.SUPER_ADMIN, UserRole.ADMIN, "super_admin", "superadmin", "admin"]:
        query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
        if tenant_id:
            query = query.filter(Pharmacy.tenant_id == tenant_id)
        return [p.id for p in query.all()]
    
    # Autres utilisateurs: uniquement leurs pharmacies associées
    query = db.query(UserPharmacy.pharmacy_id).filter(UserPharmacy.user_id == user_id)
    if tenant_id:
        query = query.join(Pharmacy).filter(Pharmacy.tenant_id == tenant_id)
    
    return [p.pharmacy_id for p in query.all()]


def get_pharmacy_invoice_stats(
    db: Session,
    pharmacy_id: UUID,
    tenant_id: UUID,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> Dict[str, Any]:
    """Calcule les statistiques des factures pour une pharmacie."""
    
    query = db.query(Invoice).filter(
        Invoice.pharmacy_id == pharmacy_id,
        Invoice.tenant_id == tenant_id
    )
    
    if start_date:
        query = query.filter(Invoice.issue_date >= start_date)
    if end_date:
        query = query.filter(Invoice.issue_date <= end_date)
    
    total_invoices = query.count()
    total_amount = query.with_entities(func.coalesce(func.sum(Invoice.total_amount), 0)).scalar() or 0
    
    # Par statut
    paid_count = query.filter(Invoice.status == InvoiceStatus.PAID).count()
    paid_amount = query.filter(Invoice.status == InvoiceStatus.PAID).with_entities(
        func.coalesce(func.sum(Invoice.total_amount), 0)
    ).scalar() or 0
    
    pending_count = query.filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.DRAFT])).count()
    pending_amount = query.filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.DRAFT])).with_entities(
        func.coalesce(func.sum(Invoice.total_amount), 0)
    ).scalar() or 0
    
    overdue_count = query.filter(Invoice.status == InvoiceStatus.OVERDUE).count()
    overdue_amount = query.filter(Invoice.status == InvoiceStatus.OVERDUE).with_entities(
        func.coalesce(func.sum(Invoice.total_amount), 0)
    ).scalar() or 0
    
    return {
        "total_invoices": total_invoices,
        "total_amount": float(total_amount),
        "paid": {
            "count": paid_count,
            "amount": float(paid_amount)
        },
        "pending": {
            "count": pending_count,
            "amount": float(pending_amount)
        },
        "overdue": {
            "count": overdue_count,
            "amount": float(overdue_amount)
        }
    }


# =======================
# Routes principales
# =======================

@router.get("/", response_model=InvoiceListResponse)
async def get_invoices_list(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    # Pagination
    skip: int = Query(0, ge=0, description="Nombre d'éléments à sauter"),
    limit: int = Query(100, ge=1, le=500, description="Nombre d'éléments par page"),
    # Filtres
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    status: Optional[str] = Query(None, description="Filtrer par statut (draft, sent, paid, overdue, cancelled)"),
    invoice_type: Optional[str] = Query(None, description="Filtrer par type (subscription, one_time, renewal)"),
    start_date: Optional[date] = Query(None, description="Date de début (issue_date)"),
    end_date: Optional[date] = Query(None, description="Date de fin (issue_date)"),
    due_start_date: Optional[date] = Query(None, description="Date d'échéance début"),
    due_end_date: Optional[date] = Query(None, description="Date d'échéance fin"),
    search: Optional[str] = Query(None, description="Recherche par numéro de facture, description"),
    # Tri
    sort_by: str = Query("issue_date", description="Champ de tri (issue_date, due_date, total_amount, invoice_number)"),
    sort_order: str = Query("desc", description="Ordre de tri (asc, desc)"),
):
    """
    Récupère la liste des factures accessibles à l'utilisateur connecté.
    
    - Super-admin et admin voient toutes les factures (ou filtrées par pharmacy_id)
    - Vendeurs/caissiers voient uniquement les factures de leurs pharmacies associées
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer les pharmacies accessibles
        accessible_pharmacy_ids = get_user_accessible_pharmacy_ids(db, current_user.id, tenant_id)
        
        if not accessible_pharmacy_ids:
            return InvoiceListResponse(
                items=[],
                total=0,
                page=skip // limit + 1 if limit > 0 else 1,
                size=0,
                has_more=False,
                page_size=limit
            )
        
        # Construire la requête
        query = db.query(Invoice).filter(
            Invoice.tenant_id == tenant_id,
            Invoice.pharmacy_id.in_(accessible_pharmacy_ids)
        )
        
        # Filtre par pharmacie spécifique (si fournie et accessible)
        if pharmacy_id:
            if pharmacy_id not in accessible_pharmacy_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            query = query.filter(Invoice.pharmacy_id == pharmacy_id)
        
        # Filtres
        if status:
            try:
                query = query.filter(Invoice.status == InvoiceStatus(status))
            except ValueError:
                query = query.filter(Invoice.status == status)
        
        if invoice_type:
            try:
                query = query.filter(Invoice.invoice_type == InvoiceType(invoice_type))
            except ValueError:
                query = query.filter(Invoice.invoice_type == invoice_type)
        
        if start_date:
            query = query.filter(Invoice.issue_date >= start_date)
        if end_date:
            query = query.filter(Invoice.issue_date <= end_date)
        
        if due_start_date:
            query = query.filter(Invoice.due_date >= due_start_date)
        if due_end_date:
            query = query.filter(Invoice.due_date <= due_end_date)
        
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Invoice.invoice_number.ilike(search_term),
                    Invoice.description.ilike(search_term),
                    Invoice.subscription_plan.ilike(search_term)
                )
            )
        
        # Compter le total
        total = query.count()
        
        # Appliquer le tri
        sort_column = getattr(Invoice, sort_by, Invoice.issue_date)
        if sort_order.lower() == "desc":
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(sort_column)
        
        # Pagination
        invoices = query.offset(skip).limit(limit).all()
        
        # Construire la réponse
        items = []
        for invoice in invoices:
            # Récupérer le nom de la pharmacie
            pharmacy = db.query(Pharmacy).filter(Pharmacy.id == invoice.pharmacy_id).first()
            
            items.append(InvoiceResponse(
                id=invoice.id,
                invoice_number=invoice.invoice_number,
                invoice_type=invoice.invoice_type.value,
                pharmacy_id=invoice.pharmacy_id,
                pharmacy_name=pharmacy.name if pharmacy else None,
                tenant_id=invoice.tenant_id,
                period_start=invoice.period_start,
                period_end=invoice.period_end,
                subtotal=float(invoice.subtotal) if invoice.subtotal else 0,
                tax_rate=float(invoice.tax_rate) if invoice.tax_rate else 0,
                tax_amount=float(invoice.tax_amount) if invoice.tax_amount else 0,
                discount_amount=float(invoice.discount_amount) if invoice.discount_amount else 0,
                total_amount=float(invoice.total_amount) if invoice.total_amount else 0,
                currency=invoice.currency,
                status=invoice.status.value,
                issue_date=invoice.issue_date,
                due_date=invoice.due_date,
                paid_at=invoice.paid_at,
                description=invoice.description,
                subscription_plan=invoice.subscription_plan,
                billing_cycle=invoice.billing_cycle,
                payment_method=invoice.payment_method,
                payment_reference=invoice.payment_reference,
                total_paid=float(invoice.total_paid) if hasattr(invoice, 'total_paid') else 0,
                remaining_amount=float(invoice.remaining_amount) if hasattr(invoice, 'remaining_amount') else float(invoice.total_amount),
                is_overdue=invoice.is_overdue(),
                days_overdue=invoice.days_overdue(),
                created_at=invoice.created_at,
                updated_at=invoice.updated_at
            ))
        
        return InvoiceListResponse(
            items=items,
            total=total,
            page=skip // limit + 1 if limit > 0 else 1,
            size=len(items),
            has_more=(skip + limit) < total,
            page_size=limit
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération factures: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération des factures: {str(e)}"
        )


@router.get("/{invoice_id}", response_model=InvoiceDetailResponse)
async def get_invoice_by_id(
    invoice_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """
    Récupère les détails d'une facture spécifique par son ID.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Récupérer la facture
        invoice = db.query(Invoice).filter(
            Invoice.id == invoice_id,
            Invoice.tenant_id == tenant_id
        ).first()
        
        if not invoice:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Facture non trouvée"
            )
        
        # Vérifier l'accès à la pharmacie
        accessible_pharmacy_ids = get_user_accessible_pharmacy_ids(db, current_user.id, tenant_id)
        if invoice.pharmacy_id not in accessible_pharmacy_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé à cette facture"
            )
        
        # Récupérer les informations de la pharmacie
        pharmacy = db.query(Pharmacy).filter(Pharmacy.id == invoice.pharmacy_id).first()
        
        # Récupérer les paiements
        payments = []
        if hasattr(invoice, 'payments'):
            for payment in invoice.payments:
                payments.append(InvoicePaymentResponse(
                    id=payment.id,
                    invoice_id=payment.invoice_id,
                    amount=float(payment.amount),
                    payment_method=payment.payment_method,
                    payment_reference=payment.payment_reference,
                    payment_date=payment.payment_date,
                    status=payment.status,
                    notes=payment.notes,
                    created_at=payment.created_at
                ))
        
        return InvoiceDetailResponse(
            id=invoice.id,
            invoice_number=invoice.invoice_number,
            invoice_type=invoice.invoice_type.value,
            pharmacy_id=invoice.pharmacy_id,
            pharmacy_name=pharmacy.name if pharmacy else None,
            tenant_id=invoice.tenant_id,
            period_start=invoice.period_start,
            period_end=invoice.period_end,
            subtotal=float(invoice.subtotal) if invoice.subtotal else 0,
            tax_rate=float(invoice.tax_rate) if invoice.tax_rate else 0,
            tax_amount=float(invoice.tax_amount) if invoice.tax_amount else 0,
            discount_amount=float(invoice.discount_amount) if invoice.discount_amount else 0,
            total_amount=float(invoice.total_amount) if invoice.total_amount else 0,
            currency=invoice.currency,
            status=invoice.status.value,
            issue_date=invoice.issue_date,
            due_date=invoice.due_date,
            paid_at=invoice.paid_at,
            description=invoice.description,
            subscription_plan=invoice.subscription_plan,
            billing_cycle=invoice.billing_cycle,
            payment_method=invoice.payment_method,
            payment_reference=invoice.payment_reference,
            invoice_metadata=invoice.invoice_metadata,
            total_paid=float(invoice.total_paid) if hasattr(invoice, 'total_paid') else 0,
            remaining_amount=float(invoice.remaining_amount) if hasattr(invoice, 'remaining_amount') else float(invoice.total_amount),
            is_overdue=invoice.is_overdue(),
            days_overdue=invoice.days_overdue(),
            payments=payments,
            created_at=invoice.created_at,
            updated_at=invoice.updated_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération facture {invoice_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération de la facture: {str(e)}"
        )


@router.get("/by-number/{invoice_number}", response_model=InvoiceDetailResponse)
async def get_invoice_by_number(
    invoice_number: str,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """
    Récupère une facture par son numéro.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        invoice = db.query(Invoice).filter(
            Invoice.invoice_number == invoice_number,
            Invoice.tenant_id == tenant_id
        ).first()
        
        if not invoice:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Facture avec le numéro {invoice_number} non trouvée"
            )
        
        # Vérifier l'accès
        accessible_pharmacy_ids = get_user_accessible_pharmacy_ids(db, current_user.id, tenant_id)
        if invoice.pharmacy_id not in accessible_pharmacy_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé à cette facture"
            )
        
        pharmacy = db.query(Pharmacy).filter(Pharmacy.id == invoice.pharmacy_id).first()
        
        payments = []
        if hasattr(invoice, 'payments'):
            for payment in invoice.payments:
                payments.append(InvoicePaymentResponse(
                    id=payment.id,
                    invoice_id=payment.invoice_id,
                    amount=float(payment.amount),
                    payment_method=payment.payment_method,
                    payment_reference=payment.payment_reference,
                    payment_date=payment.payment_date,
                    status=payment.status,
                    notes=payment.notes,
                    created_at=payment.created_at
                ))
        
        return InvoiceDetailResponse(
            id=invoice.id,
            invoice_number=invoice.invoice_number,
            invoice_type=invoice.invoice_type.value,
            pharmacy_id=invoice.pharmacy_id,
            pharmacy_name=pharmacy.name if pharmacy else None,
            tenant_id=invoice.tenant_id,
            period_start=invoice.period_start,
            period_end=invoice.period_end,
            subtotal=float(invoice.subtotal) if invoice.subtotal else 0,
            tax_rate=float(invoice.tax_rate) if invoice.tax_rate else 0,
            tax_amount=float(invoice.tax_amount) if invoice.tax_amount else 0,
            discount_amount=float(invoice.discount_amount) if invoice.discount_amount else 0,
            total_amount=float(invoice.total_amount) if invoice.total_amount else 0,
            currency=invoice.currency,
            status=invoice.status.value,
            issue_date=invoice.issue_date,
            due_date=invoice.due_date,
            paid_at=invoice.paid_at,
            description=invoice.description,
            subscription_plan=invoice.subscription_plan,
            billing_cycle=invoice.billing_cycle,
            payment_method=invoice.payment_method,
            payment_reference=invoice.payment_reference,
            invoice_metadata=invoice.invoice_metadata,
            total_paid=float(invoice.total_paid) if hasattr(invoice, 'total_paid') else 0,
            remaining_amount=float(invoice.remaining_amount) if hasattr(invoice, 'remaining_amount') else float(invoice.total_amount),
            is_overdue=invoice.is_overdue(),
            days_overdue=invoice.days_overdue(),
            payments=payments,
            created_at=invoice.created_at,
            updated_at=invoice.updated_at
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération facture {invoice_number}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération de la facture: {str(e)}"
        )


@router.get("/my/invoices", response_model=InvoiceListResponse)
async def get_my_invoices(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    # Pagination
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    # Filtres spécifiques pour le dashboard utilisateur
    include_overdue: bool = Query(True, description="Inclure les factures en retard"),
    include_paid: bool = Query(True, description="Inclure les factures payées"),
    include_pending: bool = Query(True, description="Inclure les factures en attente"),
    days_before_due: Optional[int] = Query(None, description="Factures avec échéance dans X jours"),
):
    """
    Récupère les factures de l'utilisateur connecté (pour son dashboard).
    Endpoint simplifié pour le client mobile/desktop.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        accessible_pharmacy_ids = get_user_accessible_pharmacy_ids(db, current_user.id, tenant_id)
        
        if not accessible_pharmacy_ids:
            return InvoiceListResponse(
                items=[],
                total=0,
                page=1,
                size=0,
                has_more=False,
                page_size=limit
            )
        
        query = db.query(Invoice).filter(
            Invoice.tenant_id == tenant_id,
            Invoice.pharmacy_id.in_(accessible_pharmacy_ids)
        )
        
        # Filtres de statut
        status_filters = []
        if include_overdue:
            status_filters.append(Invoice.status == InvoiceStatus.OVERDUE)
        if include_paid:
            status_filters.append(Invoice.status == InvoiceStatus.PAID)
        if include_pending:
            status_filters.append(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.DRAFT]))
        
        if status_filters:
            query = query.filter(or_(*status_filters))
        
        # Factures avec échéance proche
        if days_before_due:
            target_date = datetime.now().date() + timedelta(days=days_before_due)
            query = query.filter(
                Invoice.due_date <= target_date,
                Invoice.status != InvoiceStatus.PAID
            )
        
        total = query.count()
        
        invoices = query.order_by(desc(Invoice.due_date)).offset(skip).limit(limit).all()
        
        items = []
        for invoice in invoices:
            pharmacy = db.query(Pharmacy).filter(Pharmacy.id == invoice.pharmacy_id).first()
            
            items.append(InvoiceResponse(
                id=invoice.id,
                invoice_number=invoice.invoice_number,
                invoice_type=invoice.invoice_type.value,
                pharmacy_id=invoice.pharmacy_id,
                pharmacy_name=pharmacy.name if pharmacy else None,
                tenant_id=invoice.tenant_id,
                period_start=invoice.period_start,
                period_end=invoice.period_end,
                subtotal=float(invoice.subtotal) if invoice.subtotal else 0,
                tax_rate=float(invoice.tax_rate) if invoice.tax_rate else 0,
                tax_amount=float(invoice.tax_amount) if invoice.tax_amount else 0,
                discount_amount=float(invoice.discount_amount) if invoice.discount_amount else 0,
                total_amount=float(invoice.total_amount) if invoice.total_amount else 0,
                currency=invoice.currency,
                status=invoice.status.value,
                issue_date=invoice.issue_date,
                due_date=invoice.due_date,
                paid_at=invoice.paid_at,
                description=invoice.description,
                subscription_plan=invoice.subscription_plan,
                billing_cycle=invoice.billing_cycle,
                total_paid=float(invoice.total_paid) if hasattr(invoice, 'total_paid') else 0,
                remaining_amount=float(invoice.remaining_amount) if hasattr(invoice, 'remaining_amount') else float(invoice.total_amount),
                is_overdue=invoice.is_overdue(),
                days_overdue=invoice.days_overdue(),
                created_at=invoice.created_at,
                updated_at=invoice.updated_at
            ))
        
        return InvoiceListResponse(
            items=items,
            total=total,
            page=skip // limit + 1 if limit > 0 else 1,
            size=len(items),
            has_more=(skip + limit) < total,
            page_size=limit
        )
        
    except Exception as e:
        logger.error(f"Erreur récupération mes factures: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération des factures: {str(e)}"
        )


@router.get("/stats/overview")
async def get_invoices_stats(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
):
    """
    Récupère les statistiques globales des factures.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        accessible_pharmacy_ids = get_user_accessible_pharmacy_ids(db, current_user.id, tenant_id)
        
        if not accessible_pharmacy_ids:
            return {
                "total_invoices": 0,
                "total_amount": 0,
                "paid": {"count": 0, "amount": 0},
                "pending": {"count": 0, "amount": 0},
                "overdue": {"count": 0, "amount": 0},
                "by_pharmacy": []
            }
        
        # Si une pharmacie spécifique est demandée
        if pharmacy_id:
            if pharmacy_id not in accessible_pharmacy_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            stats = get_pharmacy_invoice_stats(db, pharmacy_id, tenant_id)
            return stats
        
        # Statistiques globales
        total_invoices = 0
        total_amount = 0.0
        paid_count = 0
        paid_amount = 0.0
        pending_count = 0
        pending_amount = 0.0
        overdue_count = 0
        overdue_amount = 0.0
        by_pharmacy = []
        
        for pharm_id in accessible_pharmacy_ids:
            stats = get_pharmacy_invoice_stats(db, pharm_id, tenant_id)
            
            pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharm_id).first()
            
            by_pharmacy.append({
                "pharmacy_id": str(pharm_id),
                "pharmacy_name": pharmacy.name if pharmacy else "Inconnue",
                **stats
            })
            
            total_invoices += stats["total_invoices"]
            total_amount += stats["total_amount"]
            paid_count += stats["paid"]["count"]
            paid_amount += stats["paid"]["amount"]
            pending_count += stats["pending"]["count"]
            pending_amount += stats["pending"]["amount"]
            overdue_count += stats["overdue"]["count"]
            overdue_amount += stats["overdue"]["amount"]
        
        return {
            "total_invoices": total_invoices,
            "total_amount": total_amount,
            "paid": {"count": paid_count, "amount": paid_amount},
            "pending": {"count": pending_count, "amount": pending_amount},
            "overdue": {"count": overdue_count, "amount": overdue_amount},
            "by_pharmacy": by_pharmacy
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération stats factures: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération des statistiques: {str(e)}"
        )


@router.get("/{invoice_id}/pdf")
async def download_invoice_pdf(
    invoice_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """
    Télécharge le PDF d'une facture.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        invoice = db.query(Invoice).filter(
            Invoice.id == invoice_id,
            Invoice.tenant_id == tenant_id
        ).first()
        
        if not invoice:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Facture non trouvée"
            )
        
        # Vérifier l'accès
        accessible_pharmacy_ids = get_user_accessible_pharmacy_ids(db, current_user.id, tenant_id)
        if invoice.pharmacy_id not in accessible_pharmacy_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé à cette facture"
            )
        
        # Générer le PDF
        pdf_data = await generate_invoice_pdf(db, invoice)
        
        from fastapi.responses import Response
        return Response(
            content=pdf_data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename=facture_{invoice.invoice_number}.pdf"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur génération PDF facture {invoice_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur génération du PDF: {str(e)}"
        )


# =======================
# Endpoint de test
# =======================

@router.get("/test", include_in_schema=False)
async def test_invoices(
    current_user: User = Depends(get_current_active_user)
):
    """
    Endpoint de test pour vérifier que le module factures est opérationnel.
    """
    return {
        "message": "Module Factures opérationnel",
        "version": "1.0.0",
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role.value if hasattr(current_user.role, 'value') else str(current_user.role)
        },
        "features": [
            "Liste des factures avec filtres",
            "Détail d'une facture",
            "Recherche par numéro",
            "Statistiques globales",
            "Export PDF",
            "Gestion des paiements"
        ],
        "timestamp": datetime.utcnow().isoformat()
    }# app/api/v1/endpoints/invoices.py
"""
Endpoints pour la gestion des factures (invoices)
Ces factures sont en réalité les ventes (sales) avec numéro de facture
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_, distinct
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, date, timedelta
import logging

from app.db.session import get_db
from app.models.sale import Sale, SaleItem
from app.models.pharmacy import Pharmacy
from app.models.user import User
from app.models.tenant import Tenant
from app.models.user_pharmacy import UserPharmacy
from app.schemas.invoice import (
    InvoiceResponse,
    InvoiceDetailResponse,
    InvoiceListResponse,
    InvoiceCreate,
    InvoiceUpdate,
    InvoiceFilter,
    InvoiceStatsResponse,
    InvoicePaymentCreate,
    InvoicePaymentResponse
)
from app.api.deps import (
    get_current_tenant,
    get_current_user,
    get_current_active_user,
    require_permission
)

router = APIRouter(prefix="/invoices", tags=["Factures"])
logger = logging.getLogger(__name__)


# =======================
# Helpers
# =======================

def get_user_accessible_pharmacy_ids(
    db: Session,
    user_id: UUID,
    tenant_id: Optional[UUID] = None
) -> List[UUID]:
    """
    Récupère la liste des IDs des pharmacies accessibles par l'utilisateur.
    Les super-admin et admin voient toutes les pharmacies.
    """
    from app.models.user import UserRole
    
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        return []
    
    # Super-admin et admin voient toutes les pharmacies
    if user.role in [UserRole.SUPER_ADMIN, UserRole.ADMIN, "super_admin", "superadmin", "admin"]:
        query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
        if tenant_id:
            query = query.filter(Pharmacy.tenant_id == tenant_id)
        return [p.id for p in query.all()]
    
    # Autres utilisateurs: uniquement leurs pharmacies associées
    query = db.query(UserPharmacy.pharmacy_id).filter(UserPharmacy.user_id == user_id)
    if tenant_id:
        query = query.join(Pharmacy).filter(Pharmacy.tenant_id == tenant_id)
    
    return [p.pharmacy_id for p in query.all()]


def sale_to_invoice_response(sale: Sale, pharmacy: Pharmacy = None) -> InvoiceResponse:
    """Convertit une vente (sale) en réponse de facture"""
    
    if not pharmacy:
        pharmacy = sale.pharmacy
    
    return InvoiceResponse(
        id=sale.id,
        invoice_number=sale.invoice_number or f"SALE-{sale.reference}",
        invoice_type="sale",
        pharmacy_id=sale.pharmacy_id,
        pharmacy_name=pharmacy.name if pharmacy else None,
        tenant_id=sale.tenant_id,
        period_start=sale.created_at,
        period_end=sale.created_at,
        subtotal=float(sale.subtotal) if sale.subtotal else 0,
        tax_rate=0,
        tax_amount=float(sale.total_tva) if sale.total_tva else 0,
        discount_amount=float(sale.total_discount) if sale.total_discount else 0,
        total_amount=float(sale.total_amount) if sale.total_amount else 0,
        currency="FC",
        status=sale.status or "completed",
        issue_date=sale.created_at,
        due_date=sale.created_at,
        paid_at=sale.payment_date if sale.payment_method != "credit" else None,
        description=f"Facture {sale.invoice_number} - {sale.customer_name or 'Client'}",
        subscription_plan=None,
        billing_cycle=None,
        payment_method=sale.payment_method,
        payment_reference=sale.reference_payment,
        total_paid=float(sale.total_amount) if sale.status == "completed" else 0,
        remaining_amount=0 if sale.status == "completed" else float(sale.total_amount),
        is_overdue=False,
        days_overdue=0,
        created_at=sale.created_at,
        updated_at=sale.updated_at
    )


def sale_to_invoice_detail(sale: Sale, pharmacy: Pharmacy = None, items: List[SaleItem] = None) -> InvoiceDetailResponse:
    """Convertit une vente en détail de facture avec ses items"""
    
    if not pharmacy:
        pharmacy = sale.pharmacy
    
    if items is None:
        items = sale.items or []
    
    # Convertir les items de vente en format de paiement (pour compatibilité)
    payments = []
    if sale.payment_method and sale.total_amount:
        payments.append(InvoicePaymentResponse(
            id=sale.id,
            invoice_id=sale.id,
            amount=float(sale.total_amount),
            payment_method=sale.payment_method,
            payment_reference=sale.reference_payment,
            payment_date=sale.payment_date or sale.created_at,
            status="success" if sale.status == "completed" else "pending",
            notes=None,
            created_at=sale.created_at
        ))
    
    return InvoiceDetailResponse(
        id=sale.id,
        invoice_number=sale.invoice_number or f"SALE-{sale.reference}",
        invoice_type="sale",
        pharmacy_id=sale.pharmacy_id,
        pharmacy_name=pharmacy.name if pharmacy else None,
        tenant_id=sale.tenant_id,
        period_start=sale.created_at,
        period_end=sale.created_at,
        subtotal=float(sale.subtotal) if sale.subtotal else 0,
        tax_rate=0,
        tax_amount=float(sale.total_tva) if sale.total_tva else 0,
        discount_amount=float(sale.total_discount) if sale.total_discount else 0,
        total_amount=float(sale.total_amount) if sale.total_amount else 0,
        currency="FC",
        status=sale.status or "completed",
        issue_date=sale.created_at,
        due_date=sale.created_at,
        paid_at=sale.payment_date if sale.payment_method != "credit" else None,
        description=f"Facture {sale.invoice_number} - {sale.customer_name or 'Client'}",
        subscription_plan=None,
        billing_cycle=None,
        payment_method=sale.payment_method,
        payment_reference=sale.reference_payment,
        invoice_metadata={
            "sale_reference": sale.reference,
            "customer_phone": sale.customer_phone,
            "seller_name": sale.seller_name,
            "items_count": len(items) if items else 0
        },
        total_paid=float(sale.total_amount) if sale.status == "completed" else 0,
        remaining_amount=0 if sale.status == "completed" else float(sale.total_amount),
        is_overdue=False,
        days_overdue=0,
        payments=payments,
        created_at=sale.created_at,
        updated_at=sale.updated_at
    )


# =======================
# Routes principales
# =======================

@router.get("/", response_model=InvoiceListResponse)
async def get_invoices_list(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    # Pagination
    skip: int = Query(0, ge=0, description="Nombre d'éléments à sauter"),
    limit: int = Query(100, ge=1, le=500, description="Nombre d'éléments par page"),
    # Filtres
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    status: Optional[str] = Query(None, description="Filtrer par statut (completed, pending, cancelled)"),
    start_date: Optional[date] = Query(None, description="Date de début (created_at)"),
    end_date: Optional[date] = Query(None, description="Date de fin (created_at)"),
    search: Optional[str] = Query(None, description="Recherche par numéro de facture, client"),
    # Tri
    sort_by: str = Query("created_at", description="Champ de tri (created_at, total_amount, invoice_number)"),
    sort_order: str = Query("desc", description="Ordre de tri (asc, desc)"),
):
    """
    Récupère la liste des factures (ventes avec numéro de facture)
    accessibles à l'utilisateur connecté.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer les pharmacies accessibles
        accessible_pharmacy_ids = get_user_accessible_pharmacy_ids(db, current_user.id, tenant_id)
        
        if not accessible_pharmacy_ids:
            return InvoiceListResponse(
                items=[],
                total=0,
                page=skip // limit + 1 if limit > 0 else 1,
                size=0,
                has_more=False,
                page_size=limit
            )
        
        # Construire la requête sur la table sales
        query = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.pharmacy_id.in_(accessible_pharmacy_ids),
            Sale.invoice_number.isnot(None),  # Seulement les ventes avec facture
            Sale.invoice_number != "",
            Sale.status != "deleted"
        )
        
        # Filtre par pharmacie spécifique
        if pharmacy_id:
            if pharmacy_id not in accessible_pharmacy_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            query = query.filter(Sale.pharmacy_id == pharmacy_id)
        
        # Filtre par statut
        if status:
            query = query.filter(Sale.status == status)
        
        # Filtres de date
        if start_date:
            query = query.filter(func.date(Sale.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(Sale.created_at) <= end_date)
        
        # Recherche textuelle
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Sale.invoice_number.ilike(search_term),
                    Sale.customer_name.ilike(search_term),
                    Sale.reference.ilike(search_term),
                    Sale.customer_phone.ilike(search_term)
                )
            )
        
        # Compter le total
        total = query.count()
        
        # Appliquer le tri
        sort_column = getattr(Sale, sort_by, Sale.created_at)
        if sort_order.lower() == "desc":
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(sort_column)
        
        # Pagination
        sales = query.offset(skip).limit(limit).all()
        
        # Construire la réponse
        items = []
        for sale in sales:
            # Récupérer la pharmacie si pas déjà chargée
            pharmacy = sale.pharmacy
            if not pharmacy and sale.pharmacy_id:
                pharmacy = db.query(Pharmacy).filter(Pharmacy.id == sale.pharmacy_id).first()
            
            items.append(sale_to_invoice_response(sale, pharmacy))
        
        return InvoiceListResponse(
            items=items,
            total=total,
            page=skip // limit + 1 if limit > 0 else 1,
            size=len(items),
            has_more=(skip + limit) < total,
            page_size=limit
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération factures: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération des factures: {str(e)}"
        )


@router.get("/{sale_id}", response_model=InvoiceDetailResponse)
async def get_invoice_by_id(
    sale_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """
    Récupère les détails d'une facture (vente) spécifique par son ID.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        sale = db.query(Sale).filter(
            Sale.id == sale_id,
            Sale.tenant_id == tenant_id,
            Sale.invoice_number.isnot(None),
            Sale.invoice_number != ""
        ).first()
        
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Facture non trouvée"
            )
        
        # Vérifier l'accès à la pharmacie
        accessible_pharmacy_ids = get_user_accessible_pharmacy_ids(db, current_user.id, tenant_id)
        if sale.pharmacy_id not in accessible_pharmacy_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé à cette facture"
            )
        
        # Récupérer les items de la vente
        items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
        
        # Récupérer la pharmacie
        pharmacy = sale.pharmacy
        if not pharmacy and sale.pharmacy_id:
            pharmacy = db.query(Pharmacy).filter(Pharmacy.id == sale.pharmacy_id).first()
        
        return sale_to_invoice_detail(sale, pharmacy, items)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération facture {sale_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération de la facture: {str(e)}"
        )


@router.get("/by-number/{invoice_number}", response_model=InvoiceDetailResponse)
async def get_invoice_by_number(
    invoice_number: str,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """
    Récupère une facture par son numéro.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        sale = db.query(Sale).filter(
            Sale.invoice_number == invoice_number,
            Sale.tenant_id == tenant_id
        ).first()
        
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Facture avec le numéro {invoice_number} non trouvée"
            )
        
        # Vérifier l'accès
        accessible_pharmacy_ids = get_user_accessible_pharmacy_ids(db, current_user.id, tenant_id)
        if sale.pharmacy_id not in accessible_pharmacy_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé à cette facture"
            )
        
        # Récupérer les items
        items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
        
        # Récupérer la pharmacie
        pharmacy = sale.pharmacy
        if not pharmacy and sale.pharmacy_id:
            pharmacy = db.query(Pharmacy).filter(Pharmacy.id == sale.pharmacy_id).first()
        
        return sale_to_invoice_detail(sale, pharmacy, items)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération facture {invoice_number}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération de la facture: {str(e)}"
        )


@router.get("/my/invoices", response_model=InvoiceListResponse)
async def get_my_invoices(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
):
    """
    Récupère les factures de l'utilisateur connecté (pour son dashboard).
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        accessible_pharmacy_ids = get_user_accessible_pharmacy_ids(db, current_user.id, tenant_id)
        
        if not accessible_pharmacy_ids:
            return InvoiceListResponse(
                items=[],
                total=0,
                page=1,
                size=0,
                has_more=False,
                page_size=limit
            )
        
        query = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.pharmacy_id.in_(accessible_pharmacy_ids),
            Sale.invoice_number.isnot(None),
            Sale.invoice_number != "",
            Sale.status == "completed"
        )
        
        if start_date:
            query = query.filter(func.date(Sale.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(Sale.created_at) <= end_date)
        
        total = query.count()
        sales = query.order_by(desc(Sale.created_at)).offset(skip).limit(limit).all()
        
        items = []
        for sale in sales:
            pharmacy = sale.pharmacy
            if not pharmacy and sale.pharmacy_id:
                pharmacy = db.query(Pharmacy).filter(Pharmacy.id == sale.pharmacy_id).first()
            items.append(sale_to_invoice_response(sale, pharmacy))
        
        return InvoiceListResponse(
            items=items,
            total=total,
            page=skip // limit + 1 if limit > 0 else 1,
            size=len(items),
            has_more=(skip + limit) < total,
            page_size=limit
        )
        
    except Exception as e:
        logger.error(f"Erreur récupération mes factures: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération des factures: {str(e)}"
        )


@router.get("/stats/overview")
async def get_invoices_stats(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    pharmacy_id: Optional[UUID] = Query(None),
):
    """
    Récupère les statistiques globales des factures (ventes).
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        accessible_pharmacy_ids = get_user_accessible_pharmacy_ids(db, current_user.id, tenant_id)
        
        if not accessible_pharmacy_ids:
            return {
                "total_invoices": 0,
                "total_amount": 0,
                "paid": {"count": 0, "amount": 0},
                "pending": {"count": 0, "amount": 0},
                "overdue": {"count": 0, "amount": 0},
                "by_pharmacy": []
            }
        
        query = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.pharmacy_id.in_(accessible_pharmacy_ids),
            Sale.invoice_number.isnot(None),
            Sale.invoice_number != ""
        )
        
        if pharmacy_id:
            if pharmacy_id not in accessible_pharmacy_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            query = query.filter(Sale.pharmacy_id == pharmacy_id)
        
        total_invoices = query.count()
        total_amount = query.with_entities(func.coalesce(func.sum(Sale.total_amount), 0)).scalar() or 0
        
        completed_count = query.filter(Sale.status == "completed").count()
        completed_amount = query.filter(Sale.status == "completed").with_entities(
            func.coalesce(func.sum(Sale.total_amount), 0)
        ).scalar() or 0
        
        pending_count = query.filter(Sale.status == "pending").count()
        pending_amount = query.filter(Sale.status == "pending").with_entities(
            func.coalesce(func.sum(Sale.total_amount), 0)
        ).scalar() or 0
        
        return {
            "total_invoices": total_invoices,
            "total_amount": float(total_amount),
            "paid": {"count": completed_count, "amount": float(completed_amount)},
            "pending": {"count": pending_count, "amount": float(pending_amount)},
            "overdue": {"count": 0, "amount": 0},
            "by_pharmacy": []
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération stats factures: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération des statistiques: {str(e)}"
        )


@router.get("/test", include_in_schema=False)
async def test_invoices(
    current_user: User = Depends(get_current_active_user)
):
    """
    Endpoint de test pour vérifier que le module factures est opérationnel.
    """
    return {
        "message": "Module Factures opérationnel (basé sur les ventes)",
        "version": "2.0.0",
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role.value if hasattr(current_user.role, 'value') else str(current_user.role)
        },
        "features": [
            "Liste des factures depuis la table sales",
            "Détail d'une facture avec ses items",
            "Recherche par numéro de facture",
            "Statistiques globales",
            "Support offline/online"
        ],
        "timestamp": datetime.utcnow().isoformat()
    }
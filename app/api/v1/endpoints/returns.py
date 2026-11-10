# app/api/v1/endpoints/returns.py
"""
API de gestion des retours produits, annulations, échanges et remboursements
Support complet avec mise à jour automatique du stock
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, and_, or_, between, extract
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, date, timedelta
import logging
from decimal import Decimal
from sqlalchemy import asc

from app.db.session import get_db
from app.models.return_product import Return, ReturnItem, ReturnStatus, ReturnType, ReturnReason
from app.models.sale import Sale, SaleItem
from app.models.product import Product
from app.models.stock_movement import StockMovement
from app.models.customer import Customer
from app.models.user import User
from app.models.pharmacy import Pharmacy
from app.models.tenant import Tenant
from app.schemas.return_product import (
    ReturnCreate, ReturnUpdate, ReturnResponse, ReturnListResponse,
    ReturnItemCreate, ReturnStatsResponse, ReturnFilterParams,
    ExchangeRequest, RefundRequest, BulkReturnRequest,
    ReturnApprovalRequest, ReturnProcessRequest, ReturnSearchResponse
)
from app.api.deps import (
    get_current_tenant,
    get_current_user,
    get_current_active_user,
    get_current_pharmacy_entity,
    require_permission
)

import uuid
from app.services.sale_deletion_service import (
    generate_return_number as _generate_return_number,
    apply_sale_return,
    SaleDeletionError,
)

router = APIRouter(prefix="/returns", tags=["Retours produits"])
logger = logging.getLogger(__name__)


# ========================
# FONCTIONS UTILITAIRES
# ========================

def generate_return_number(db: Session, tenant_id: UUID, pharmacy_id: UUID) -> str:
    """Génère un numéro de retour unique"""
    return _generate_return_number(db, tenant_id, pharmacy_id)


async def restore_stock_for_return(
    db: Session,
    return_obj: Return,
    tenant_id: UUID,
    user_id: UUID
) -> int:
    """
    Restaure le stock pour un retour approuvé
    Retourne le nombre d'articles restaurés
    """
    restored_count = 0
    
    for item in return_obj.items:
        if item.quantity_restored >= item.quantity:
            continue
        
        product = db.query(Product).filter(
            Product.id == item.product_id,
            Product.tenant_id == tenant_id,
            Product.pharmacy_id == return_obj.pharmacy_id
        ).first()
        
        if not product:
            logger.warning(f"Produit {item.product_id} non trouvé pour restauration")
            continue
        
        quantity_to_restore = item.quantity - item.quantity_restored
        
        # Sauvegarder l'ancienne quantité
        old_quantity = product.quantity
        
        # Restaurer le stock
        product.quantity += quantity_to_restore
        product.available_quantity = max(0, product.quantity - (product.reserved_quantity or 0))
        product.last_adjustment_date = datetime.utcnow()
        product.refresh_statuses()
        
        # Mettre à jour l'item
        item.quantity_restored += quantity_to_restore
        restored_count += quantity_to_restore
        
        # Créer un mouvement de stock
        movement = StockMovement(
            tenant_id=tenant_id,
            product_id=product.id,
            pharmacy_id=return_obj.pharmacy_id,
            branch_id=return_obj.branch_id,
            quantity_before=old_quantity,
            quantity_after=product.quantity,
            quantity_change=quantity_to_restore,
            movement_type="return_restore",
            reason=f"Retour produit - {return_obj.return_number}",
            reference=return_obj.return_number,
            sale_id=return_obj.sale_id,
            created_by=user_id
        )
        db.add(movement)
    
    return_obj.stock_restored = True
    return_obj.stock_restored_date = datetime.utcnow()
    
    return restored_count


async def update_customer_credit(
    db: Session,
    customer_id: UUID,
    refund_amount: Decimal,
    is_addition: bool = False
) -> None:
    """Met à jour le crédit client après un remboursement"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer:
        if is_addition:
            # Ajouter au crédit (remboursement)
            customer.credit_balance = (customer.credit_balance or 0) + refund_amount
        else:
            # Déduire du crédit (remboursement sur dette)
            customer.dette_actuelle = max(0, (customer.dette_actuelle or 0) - refund_amount)


# ========================
# ROUTES PRINCIPALES
# ========================

@router.post("/", response_model=ReturnResponse, status_code=status.HTTP_201_CREATED)
async def create_return(
    return_data: ReturnCreate,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    Crée une nouvelle demande de retour produit.
    Supporte les retours clients, fournisseurs et internes.
    """
    try:
        # Vérifier les permissions
        if current_user.role.lower() not in ["super_admin", "superadmin", "admin", "gerant", "pharmacien", "vendeur"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission insuffisante pour créer un retour"
            )
        
        if not current_pharmacy:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucune pharmacie active sélectionnée"
            )
        
        tenant_id = current_tenant.id if current_tenant else None
        
        # Vérifier que la vente existe (si fournie)
        sale = None
        if return_data.sale_id:
            sale = db.query(Sale).filter(
                Sale.id == return_data.sale_id,
                Sale.tenant_id == tenant_id,
                Sale.pharmacy_id == current_pharmacy.id
            ).first()
            
            if not sale:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Vente non trouvée"
                )
        
        # Générer le numéro de retour
        return_number = generate_return_number(db, tenant_id, current_pharmacy.id)
        
        # Créer le retour
        new_return = Return(
            tenant_id=tenant_id,
            pharmacy_id=current_pharmacy.id,
            branch_id=return_data.branch_id,
            return_number=return_number,
            reference=return_data.reference,
            return_type=return_data.return_type,
            status=ReturnStatus.PENDING,
            reason=return_data.reason,
            sale_id=return_data.sale_id,
            purchase_id=return_data.purchase_id,
            invoice_number=return_data.invoice_number,
            customer_id=return_data.customer_id,
            customer_name=return_data.customer_name,
            customer_phone=return_data.customer_phone,
            customer_email=return_data.customer_email,
            supplier_id=return_data.supplier_id,
            supplier_name=return_data.supplier_name,
            return_date=return_data.return_date or datetime.utcnow(),
            requested_date=datetime.utcnow(),
            notes=return_data.notes,
            created_by=current_user.id,
            restocking_fee_percent=return_data.restocking_fee_percent,
            meta_data=return_data.meta_data or {}
        )
        
        db.add(new_return)
        db.flush()
        
        # Créer les items de retour
        subtotal = Decimal("0")
        tax_amount = Decimal("0")
        
        for item_data in return_data.items:
            # Récupérer le produit
            product = db.query(Product).filter(
                Product.id == item_data.product_id,
                Product.tenant_id == tenant_id,
                Product.pharmacy_id == current_pharmacy.id,
                Product.is_active == True
            ).first()
            
            if not product:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Produit {item_data.product_id} non trouvé"
                )
            
            # Récupérer l'item de vente original si disponible
            sale_item = None
            if sale and item_data.sale_item_id:
                sale_item = db.query(SaleItem).filter(
                    SaleItem.id == item_data.sale_item_id,
                    SaleItem.sale_id == sale.id
                ).first()
            
            # Calculer les prix
            unit_price = sale_item.unit_price if sale_item else product.selling_price
            tva_rate = sale_item.tva_rate if sale_item else (product.tva_rate if product.has_tva else 0)
            
            item_subtotal = unit_price * Decimal(str(item_data.quantity))
            item_tva = item_subtotal * (tva_rate / Decimal("100"))
            item_total = item_subtotal + item_tva
            
            return_item = ReturnItem(
                tenant_id=tenant_id,
                return_id=new_return.id,
                product_id=item_data.product_id,
                product_code=product.code,
                product_name=product.name,
                product_barcode=product.barcode,
                batch_number=item_data.batch_number or product.batch_number,
                expiry_date=item_data.expiry_date or product.expiry_date,
                quantity=item_data.quantity,
                unit_price=unit_price,
                original_unit_price=sale_item.unit_price if sale_item else None,
                discount_percent=item_data.discount_percent or 0,
                tva_rate=tva_rate,
                tva_amount=item_tva,
                subtotal=item_subtotal,
                total=item_total,
                reason=item_data.reason or return_data.reason,
                reason_description=item_data.reason_description,
                condition=item_data.condition,
                condition_notes=item_data.condition_notes,
                sale_item_id=item_data.sale_item_id,
                meta_data=item_data.meta_data or {}
            )
            
            db.add(return_item)
            subtotal += item_subtotal
            tax_amount += item_tva
        
        new_return.subtotal = subtotal
        new_return.tax_amount = tax_amount
        new_return.total_amount = subtotal + tax_amount
        
        # Calculer les frais de restockage
        if new_return.restocking_fee_percent and new_return.restocking_fee_percent > 0:
            new_return.restocking_fee = new_return.total_amount * (new_return.restocking_fee_percent / Decimal("100"))
        
        db.commit()
        db.refresh(new_return)
        
        logger.info(f"Retour créé: {new_return.return_number} par {current_user.email}")
        
        return ReturnResponse(
            message="Demande de retour créée avec succès",
            return_obj=new_return,
            requires_approval=new_return.return_type in [ReturnType.CUSTOMER, ReturnType.SUPPLIER]
        )
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création retour: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur création retour: {str(e)}"
        )


@router.get("/", response_model=ReturnListResponse)
async def list_returns(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    # Pagination
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    # Filtres
    status: Optional[str] = Query(None, description="Statut du retour"),
    return_type: Optional[str] = Query(None, description="Type de retour"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    search: Optional[str] = Query(None, description="Recherche par numéro, facture, client"),
    customer_id: Optional[UUID] = Query(None, description="Filtrer par client"),
    sale_id: Optional[UUID] = Query(None, description="Filtrer par vente"),
    # Filtres rapides
    period: Optional[str] = Query(None, description="Période: today, yesterday, this_week, this_month"),
    sort_by: str = Query("created_at", description="Champ de tri"),
    sort_order: str = Query("desc", description="Ordre de tri")
):
    """
    Récupère la liste des retours avec filtres avancés.
    Supporte les filtres rapides: aujourd'hui, hier, cette semaine, ce mois.
    """
    try:
        if not current_pharmacy:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucune pharmacie active sélectionnée"
            )
        
        tenant_id = current_tenant.id if current_tenant else None
        
        # Construction de la requête
        query = db.query(Return).filter(
            Return.tenant_id == tenant_id,
            Return.pharmacy_id == current_pharmacy.id,
            Return.is_active == True
        )
        
        # Filtre par période rapide
        today = date.today()
        if period:
            if period == "today":
                start_date = today
                end_date = today
            elif period == "yesterday":
                start_date = today - timedelta(days=1)
                end_date = today - timedelta(days=1)
            elif period == "this_week":
                start_date = today - timedelta(days=today.weekday())
                end_date = today
            elif period == "this_month":
                start_date = today.replace(day=1)
                end_date = today
        
        # Filtres de date
        if start_date:
            query = query.filter(func.date(Return.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(Return.created_at) <= end_date)
        
        # Filtres par statut et type
        if status:
            query = query.filter(Return.status == status)
        if return_type:
            query = query.filter(Return.return_type == return_type)
        
        # Recherche textuelle
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Return.return_number.ilike(search_term),
                    Return.reference.ilike(search_term),
                    Return.invoice_number.ilike(search_term),
                    Return.customer_name.ilike(search_term),
                    Return.customer_phone.ilike(search_term),
                    Return.supplier_name.ilike(search_term)
                )
            )
        
        # Filtres spécifiques
        if customer_id:
            query = query.filter(Return.customer_id == customer_id)
        if sale_id:
            query = query.filter(Return.sale_id == sale_id)
        
        # Compter le total
        total = query.count()
        
        # Trier
        sort_column = getattr(Return, sort_by, Return.created_at)
        if sort_order.lower() == "desc":
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(asc(sort_column))
        
        # Pagination
        returns = query.offset(skip).limit(limit).all()
        
        # Charger les items pour chaque retour
        result_returns = []
        for return_obj in returns:
            items = db.query(ReturnItem).filter(
                ReturnItem.return_id == return_obj.id
            ).all()
            result_returns.append({
                "return": return_obj,
                "items": items,
                "items_count": len(items),
                "total_quantity": sum(i.quantity for i in items)
            })
        
        return ReturnListResponse(
            total=total,
            page=skip // limit + 1 if limit > 0 else 1,
            page_size=limit,
            data=result_returns,
            filters_applied={
                "period": period,
                "status": status,
                "return_type": return_type,
                "search": search
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération liste retours: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération retours: {str(e)}"
        )


@router.get("/search", response_model=ReturnSearchResponse)
async def search_returns(
    q: str = Query(..., description="Terme de recherche"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    limit: int = Query(20, ge=1, le=100)
):
    """
    Recherche rapide de retours par:
    - Numéro de facture
    - Montant payé
    - Nom du client
    - Date
    """
    try:
        if not current_pharmacy:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucune pharmacie active sélectionnée"
            )
        
        tenant_id = current_tenant.id if current_tenant else None
        
        query = db.query(Return).filter(
            Return.tenant_id == tenant_id,
            Return.pharmacy_id == current_pharmacy.id,
            Return.is_active == True
        )
        
        # Recherche multidimensionnelle
        search_term = f"%{q}%"
        query = query.filter(
            or_(
                Return.return_number.ilike(search_term),
                Return.invoice_number.ilike(search_term),
                Return.customer_name.ilike(search_term),
                Return.customer_phone.ilike(search_term),
                Return.reference.ilike(search_term)
            )
        )
        
        # Recherche par montant (si q est un nombre)
        try:
            amount = Decimal(q)
            query = query.filter(Return.total_amount == amount)
        except:
            pass
        
        # Recherche par date
        try:
            search_date = datetime.strptime(q, "%Y-%m-%d").date()
            query = query.filter(func.date(Return.created_at) == search_date)
        except:
            pass
        
        returns = query.order_by(desc(Return.created_at)).limit(limit).all()
        
        results = []
        for return_obj in returns:
            results.append({
                "id": str(return_obj.id),
                "return_number": return_obj.return_number,
                "invoice_number": return_obj.invoice_number,
                "customer_name": return_obj.customer_name,
                "total_amount": float(return_obj.total_amount),
                "status": return_obj.status.value if return_obj.status else None,
                "created_at": return_obj.created_at.isoformat(),
                "items_count": len(return_obj.items) if return_obj.items else 0
            })
        
        return ReturnSearchResponse(
            query=q,
            total=len(results),
            results=results
        )
        
    except Exception as e:
        logger.error(f"Erreur recherche retours: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur recherche: {str(e)}"
        )


@router.get("/report")
async def returns_report(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    period: Optional[str] = Query(None, description="today, yesterday, this_week, this_month"),
    status_filter: str = Query("processed", alias="status", description="Statut des retours (défaut: processed)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
):
    """
    Rapport des retours.
    Une vente retournée est supprimée des ventes et des rapports : elle n'existe
    plus que dans ce rapport (instantané `original_sale` conservé dans le retour).
    """
    if not current_pharmacy:
        raise HTTPException(status_code=400, detail="Aucune pharmacie active sélectionnée")

    tenant_id = current_tenant.id if current_tenant else None
    today = date.today()
    if period == "today":
        start_date = end_date = today
    elif period == "yesterday":
        start_date = end_date = today - timedelta(days=1)
    elif period == "this_week":
        start_date, end_date = today - timedelta(days=today.weekday()), today
    elif period == "this_month":
        start_date, end_date = today.replace(day=1), today

    query = db.query(Return).filter(
        Return.tenant_id == tenant_id,
        Return.pharmacy_id == current_pharmacy.id,
        Return.is_active == True,
        Return.return_type == ReturnType.CUSTOMER,
    )
    if status_filter and status_filter != "all":
        query = query.filter(Return.status == status_filter)
    if start_date:
        query = query.filter(func.date(Return.return_date) >= start_date)
    if end_date:
        query = query.filter(func.date(Return.return_date) <= end_date)

    total = query.count()
    all_amounts = query.with_entities(
        func.coalesce(func.sum(Return.total_amount), 0),
        func.coalesce(func.sum(Return.refund_amount), 0),
    ).first()
    rows = query.order_by(desc(Return.return_date)).offset(skip).limit(limit).all()

    data = []
    for r in rows:
        meta = r.meta_data or {}
        data.append({
            "id": str(r.id),
            "return_number": r.return_number,
            "return_date": r.return_date.isoformat() if r.return_date else None,
            "status": r.status.value if r.status else None,
            "reason": r.reason.value if r.reason else None,
            "invoice_number": r.invoice_number,
            "customer_name": r.customer_name,
            "total_amount": float(r.total_amount or 0),
            "refund_amount": float(r.refund_amount or 0),
            "refund_method": r.refund_method,
            "stock_restored": r.stock_restored,
            "sale_deleted": meta.get("sale_deleted", False),
            "original_sale": meta.get("original_sale"),
            "items": [
                {
                    "product_name": i.product_name,
                    "quantity": i.quantity,
                    "unit_price": float(i.unit_price or 0),
                    "total": float(i.total or 0),
                }
                for i in r.items
            ],
        })

    return {
        "total": total,
        "total_returned_amount": float(all_amounts[0]),
        "total_refunded_amount": float(all_amounts[1]),
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "data": data,
    }


@router.get("/{return_id}", response_model=ReturnResponse)
async def get_return(
    return_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity)
):
    """Récupère les détails d'un retour spécifique"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        return_obj = db.query(Return).filter(
            Return.id == return_id,
            Return.tenant_id == tenant_id,
            Return.is_active == True
        ).first()
        
        if not return_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Retour non trouvé"
            )
        
        # Vérifier l'accès à la pharmacie
        if current_pharmacy and return_obj.pharmacy_id != current_pharmacy.id:
            if current_user.role.lower() not in ["super_admin", "superadmin", "admin"]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à ce retour"
                )
        
        items = db.query(ReturnItem).filter(ReturnItem.return_id == return_id).all()
        
        return ReturnResponse(
            message="",
            return_obj=return_obj,
            items=items
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération retour {return_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération retour: {str(e)}"
        )


@router.put("/{return_id}/approve", response_model=ReturnResponse)
async def approve_return(
    return_id: UUID,
    approval_data: ReturnApprovalRequest,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Approuve une demande de retour.
    Nécessite les droits d'administrateur ou gestionnaire.
    """
    try:
        if current_user.role.lower() not in ["super_admin", "superadmin", "admin", "gerant"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Seuls les administrateurs peuvent approuver des retours"
            )
        
        tenant_id = current_tenant.id if current_tenant else None
        
        return_obj = db.query(Return).filter(
            Return.id == return_id,
            Return.tenant_id == tenant_id,
            Return.is_active == True
        ).first()
        
        if not return_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Retour non trouvé"
            )
        
        if return_obj.status != ReturnStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Impossible d'approuver un retour avec le statut {return_obj.status.value}"
            )
        
        return_obj.approve(current_user.id, approval_data.notes)
        
        if approval_data.restocking_fee_percent is not None:
            return_obj.restocking_fee_percent = Decimal(str(approval_data.restocking_fee_percent))
            return_obj.calculate_restocking_fee()
        
        db.commit()
        db.refresh(return_obj)
        
        logger.info(f"Retour approuvé: {return_obj.return_number} par {current_user.email}")
        
        return ReturnResponse(
            message="Retour approuvé avec succès",
            return_obj=return_obj
        )
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur approbation retour: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur approbation: {str(e)}"
        )


@router.put("/{return_id}/reject", response_model=ReturnResponse)
async def reject_return(
    return_id: UUID,
    rejection_reason: str = Query(..., description="Raison du rejet"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """Rejette une demande de retour"""
    try:
        if current_user.role.lower() not in ["super_admin", "superadmin", "admin", "gerant"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Seuls les administrateurs peuvent rejeter des retours"
            )
        
        tenant_id = current_tenant.id if current_tenant else None
        
        return_obj = db.query(Return).filter(
            Return.id == return_id,
            Return.tenant_id == tenant_id,
            Return.is_active == True
        ).first()
        
        if not return_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Retour non trouvé"
            )
        
        if return_obj.status != ReturnStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Impossible de rejeter un retour avec le statut {return_obj.status.value}"
            )
        
        return_obj.reject(current_user.id, rejection_reason)
        db.commit()
        db.refresh(return_obj)
        
        logger.info(f"Retour rejeté: {return_obj.return_number} par {current_user.email}")
        
        return ReturnResponse(
            message="Retour rejeté",
            return_obj=return_obj
        )
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur rejet retour: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur rejet: {str(e)}"
        )


@router.post("/{return_id}/process", response_model=ReturnResponse)
async def process_return(
    return_id: UUID,
    process_data: ReturnProcessRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity)
):
    """
    Traite un retour approuvé:
    - Restaure le stock
    - Enregistre le remboursement
    - Met à jour le crédit client
    """
    try:
        if current_user.role.lower() not in ["super_admin", "superadmin", "admin", "gerant", "pharmacien"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission insuffisante pour traiter un retour"
            )
        
        tenant_id = current_tenant.id if current_tenant else None
        
        return_obj = db.query(Return).filter(
            Return.id == return_id,
            Return.tenant_id == tenant_id,
            Return.is_active == True
        ).first()
        
        if not return_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Retour non trouvé"
            )
        
        if return_obj.status not in [ReturnStatus.APPROVED, ReturnStatus.PENDING]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Impossible de traiter un retour avec le statut {return_obj.status.value}"
            )
        
        # Retour client lié à une vente : la vente retournée est SUPPRIMÉE de la base
        # (retour total) ou allégée des quantités retournées (retour partiel).
        # Le retour conserve l'instantané de la vente d'origine.
        restored_count = 0
        sale = None
        if return_obj.sale_id and return_obj.return_type == ReturnType.CUSTOMER:
            sale = db.query(Sale).filter(
                Sale.id == return_obj.sale_id,
                Sale.tenant_id == tenant_id
            ).first()

        if sale:
            try:
                sale_result = apply_sale_return(
                    db, return_obj, sale, current_user.id,
                    restore_stock=process_data.restore_stock
                )
            except SaleDeletionError as e:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
            restored_count = sale_result["restored_count"]
        elif process_data.restore_stock:
            # Restaurer le stock
            restored_count = await restore_stock_for_return(db, return_obj, tenant_id, current_user.id)
        
        # Traiter le remboursement
        if process_data.refund_amount and process_data.refund_amount > 0:
            return_obj.refund_amount = Decimal(str(process_data.refund_amount))
            return_obj.refund_method = process_data.refund_method
            return_obj.refund_date = datetime.utcnow()
            
            # Mettre à jour le crédit client
            if return_obj.customer_id:
                await update_customer_credit(db, return_obj.customer_id, return_obj.refund_amount, True)
        
        # Générer une note de crédit si demandée
        if process_data.generate_credit_note:
            return_obj.credit_note_number = f"CN-{return_obj.return_number}"
            return_obj.credit_note_issued = True
        
        # Finaliser le traitement
        return_obj.process(current_user.id, process_data.restore_stock)
        
        db.commit()
        db.refresh(return_obj)
        
        logger.info(f"Retour traité: {return_obj.return_number} - {restored_count} articles restaurés")
        
        return ReturnResponse(
            message=f"Retour traité avec succès. {restored_count} articles restaurés.",
            return_obj=return_obj
        )
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur traitement retour: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur traitement: {str(e)}"
        )


@router.post("/from-sale/{sale_id}", response_model=ReturnResponse)
async def create_return_from_sale(
    sale_id: UUID,
    items_to_return: List[Dict[str, Any]],
    reason: ReturnReason = ReturnReason.CUSTOMER_RETURN,
    restocking_fee_percent: Optional[float] = None,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity)
):
    """
    Crée un retour directement à partir d'une vente existante.
    Utile pour les annulations partielles ou totales.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Récupérer la vente
        sale = db.query(Sale).filter(
            Sale.id == sale_id,
            Sale.tenant_id == tenant_id,
            Sale.pharmacy_id == current_pharmacy.id
        ).first()
        
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vente non trouvée"
            )
        
        # Créer les items de retour à partir de la vente
        return_items = []
        total_refund = Decimal("0")
        
        for item_data in items_to_return:
            sale_item = db.query(SaleItem).filter(
                SaleItem.id == item_data.get("sale_item_id"),
                SaleItem.sale_id == sale_id
            ).first()
            
            if not sale_item:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Item de vente {item_data.get('sale_item_id')} non trouvé"
                )
            
            quantity = item_data.get("quantity", sale_item.quantity)
            
            return_items.append(
                ReturnItemCreate(
                    product_id=sale_item.product_id,
                    sale_item_id=sale_item.id,
                    quantity=quantity,
                    condition=item_data.get("condition", "opened"),
                    reason_description=item_data.get("reason_description")
                )
            )
            
            # Calculer le remboursement (prix payé - remise)
            item_refund = (sale_item.total or 0) * (quantity / sale_item.quantity)
            total_refund += item_refund
        
        # Créer le retour
        return_create = ReturnCreate(
            return_type=ReturnType.CUSTOMER,
            reason=reason,
            sale_id=sale_id,
            invoice_number=sale.invoice_number,
            customer_id=sale.customer_id,
            customer_name=sale.customer_name,
            customer_phone=sale.customer_phone,
            customer_email=getattr(sale, 'customer_email', None),
            items=return_items,
            restocking_fee_percent=Decimal(str(restocking_fee_percent)) if restocking_fee_percent else None,
            notes=f"Retour depuis la vente {sale.reference}"
        )
        
        # Appeler la création de retour
        return await create_return(
            return_data=return_create,
            db=db,
            current_tenant=current_tenant,
            current_user=current_user,
            current_pharmacy=current_pharmacy,
            background_tasks=BackgroundTasks()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur création retour depuis vente: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur création retour: {str(e)}"
        )


@router.post("/exchange", response_model=ReturnResponse)
async def exchange_product(
    exchange_data: ExchangeRequest,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity)
):
    """
    Échange un produit contre un autre.
    - Crée un retour pour le produit retourné
    - Crée une nouvelle vente pour le produit d'échange
    - Ajuste la différence de prix
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # 1. Créer le retour pour le produit retourné
        return_items = [
            ReturnItemCreate(
                product_id=exchange_data.returned_product_id,
                sale_item_id=exchange_data.sale_item_id,
                quantity=exchange_data.returned_quantity,
                condition=exchange_data.returned_condition,
                reason_description=exchange_data.return_reason
            )
        ]
        
        return_create = ReturnCreate(
            return_type=ReturnType.CUSTOMER,
            reason=ReturnReason.CUSTOMER_RETURN,
            sale_id=exchange_data.sale_id,
            customer_id=exchange_data.customer_id,
            items=return_items,
            notes=f"Échange: {exchange_data.returned_product_name} -> {exchange_data.new_product_name}"
        )
        
        # Créer le retour
        return_result = await create_return(
            return_data=return_create,
            db=db,
            current_tenant=current_tenant,
            current_user=current_user,
            current_pharmacy=current_pharmacy,
            background_tasks=BackgroundTasks()
        )
        
        # 2. Créer la vente pour le produit d'échange
        from app.schemas.sale import SaleCreate, SaleItemCreate as SaleItemCreateSchema
        
        sale_create = SaleCreate(
            customer_id=exchange_data.customer_id,
            items=[
                SaleItemCreateSchema(
                    product_id=exchange_data.new_product_id,
                    quantity=exchange_data.new_quantity,
                    discount_percent=exchange_data.exchange_discount or 0
                )
            ],
            payment_method=exchange_data.payment_method,
            notes=f"Échange depuis retour {return_result.return_obj.return_number}"
        )
        
        # Appeler l'API de vente pour créer la vente d'échange
        from app.api.v1.sales import create_sale
        
        sale_result = await create_sale(
            sale_data=sale_create,
            db=db,
            current_tenant=current_tenant,
            current_user=current_user,
            current_pharmacy=current_pharmacy,
            background_tasks=BackgroundTasks()
        )
        
        # 3. Traiter le retour pour restaurer le stock
        await process_return(
            return_id=return_result.return_obj.id,
            process_data=ReturnProcessRequest(restore_stock=True),
            background_tasks=BackgroundTasks(),
            db=db,
            current_tenant=current_tenant,
            current_user=current_user,
            current_pharmacy=current_pharmacy
        )
        
        return ReturnResponse(
            message="Échange effectué avec succès",
            return_obj=return_result.return_obj,
            exchange_sale_id=sale_result.sale.id if hasattr(sale_result, 'sale') else None
        )
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur échange produit: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur échange: {str(e)}"
        )


@router.post("/bulk", response_model=ReturnListResponse)
async def bulk_create_returns(
    bulk_data: BulkReturnRequest,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    Crée plusieurs retours en masse.
    Utile pour les retours fournisseurs ou les rappels produits.
    """
    try:
        if current_user.role.lower() not in ["super_admin", "superadmin", "admin", "gerant"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Seuls les administrateurs peuvent créer des retours en masse"
            )
        
        created_returns = []
        errors = []
        
        for return_data in bulk_data.returns:
            try:
                result = await create_return(
                    return_data=return_data,
                    db=db,
                    current_tenant=current_tenant,
                    current_user=current_user,
                    current_pharmacy=current_pharmacy,
                    background_tasks=background_tasks
                )
                created_returns.append(result.return_obj)
            except Exception as e:
                errors.append({
                    "data": return_data.dict() if hasattr(return_data, 'dict') else return_data,
                    "error": str(e)
                })
        
        return ReturnListResponse(
            total=len(created_returns),
            page=1,
            page_size=len(created_returns),
            data=[{"return": r, "items": []} for r in created_returns],
            bulk_errors=errors if errors else None
        )
        
    except Exception as e:
        logger.error(f"Erreur création retours en masse: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur création en masse: {str(e)}"
        )


@router.get("/stats/overview", response_model=ReturnStatsResponse)
async def get_return_stats(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    period: Optional[str] = Query("this_month", description="Period: today, this_week, this_month, this_year")
):
    """Statistiques globales des retours - Version robuste"""
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        if not current_pharmacy:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucune pharmacie active selectionnee"
            )
        
        # Determiner la periode
        today = date.today()
        if period == "today":
            start_date = today
            end_date = today
        elif period == "this_week":
            start_date = today - timedelta(days=today.weekday())
            end_date = today
        elif period == "this_month":
            start_date = today.replace(day=1)
            end_date = today
        elif period == "this_year":
            start_date = today.replace(month=1, day=1)
            end_date = today
        else:
            start_date = today.replace(day=1)
            end_date = today
        
        # Requete de base
        base_query = db.query(Return).filter(
            Return.tenant_id == tenant_id,
            Return.pharmacy_id == current_pharmacy.id,
            Return.is_active == True,
            Return.created_at >= start_date,
            Return.created_at <= end_date + timedelta(days=1)
        )
        
        # Compter par statut (en utilisant des strings pour eviter les erreurs d'enum)
        all_returns = base_query.all()
        
        total_returns = len(all_returns)
        pending_count = sum(1 for r in all_returns if getattr(r, 'status', None) == 'pending')
        approved_count = sum(1 for r in all_returns if getattr(r, 'status', None) == 'approved')
        rejected_count = sum(1 for r in all_returns if getattr(r, 'status', None) == 'rejected')
        processed_count = sum(1 for r in all_returns if getattr(r, 'status', None) == 'processed')
        
        # Compter par type
        customer_returns = sum(1 for r in all_returns if getattr(r, 'return_type', None) == 'customer')
        supplier_returns = sum(1 for r in all_returns if getattr(r, 'return_type', None) == 'supplier')
        internal_returns = sum(1 for r in all_returns if getattr(r, 'return_type', None) == 'internal')
        
        # Calculer les montants
        total_refund_amount = sum(float(getattr(r, 'refund_amount', 0) or 0) for r in all_returns if getattr(r, 'status', None) == 'processed')
        total_restocking_fees = sum(float(getattr(r, 'restocking_fee', 0) or 0) for r in all_returns)
        
        # Top produits retournes
        try:
            top_products_query = db.query(
                ReturnItem.product_name,
                func.sum(ReturnItem.quantity).label("total_quantity"),
                func.sum(ReturnItem.total).label("total_value")
            ).join(
                Return, Return.id == ReturnItem.return_id
            ).filter(
                Return.tenant_id == tenant_id,
                Return.pharmacy_id == current_pharmacy.id,
                Return.created_at >= start_date,
                Return.created_at <= end_date + timedelta(days=1),
                Return.is_active == True,
                ReturnItem.product_name.isnot(None)
            ).group_by(
                ReturnItem.product_name
            ).order_by(
                desc("total_quantity")
            ).limit(5).all()
            
            top_products = [
                {
                    "product_name": p.product_name,
                    "quantity": int(p.total_quantity),
                    "value": float(p.total_value) if p.total_value else 0.0
                }
                for p in top_products_query
            ]
        except Exception as e:
            logger.warning(f"Erreur top produits: {str(e)}")
            top_products = []
        
        return ReturnStatsResponse(
            period=period,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            total_returns=total_returns,
            pending_count=pending_count,
            approved_count=approved_count,
            rejected_count=rejected_count,
            processed_count=processed_count,
            total_refund_amount=total_refund_amount,
            total_restocking_fees=total_restocking_fees,
            customer_returns=customer_returns,
            supplier_returns=supplier_returns,
            internal_returns=internal_returns,
            top_returned_products=top_products
        )
        
    except Exception as e:
        logger.error(f"Erreur statistiques retours: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur statistiques: {str(e)}"
        )

@router.delete("/{return_id}", status_code=status.HTTP_200_OK)
async def cancel_return(
    return_id: UUID,
    reason: Optional[str] = Query(None, description="Raison de l'annulation"),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Annule une demande de retour (si non encore traitée).
    """
    try:
        if current_user.role.lower() not in ["super_admin", "superadmin", "admin", "gerant"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Seuls les administrateurs peuvent annuler des retours"
            )
        
        tenant_id = current_tenant.id if current_tenant else None
        
        return_obj = db.query(Return).filter(
            Return.id == return_id,
            Return.tenant_id == tenant_id,
            Return.is_active == True
        ).first()
        
        if not return_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Retour non trouvé"
            )
        
        if return_obj.status in [ReturnStatus.PROCESSED, ReturnStatus.CANCELLED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Impossible d'annuler un retour déjà {return_obj.status.value}"
            )
        
        return_obj.cancel(current_user.id, reason)
        db.commit()
        
        logger.info(f"Retour annulé: {return_obj.return_number} par {current_user.email}")
        
        return {
            "message": "Retour annulé avec succès",
            "return_id": str(return_id),
            "return_number": return_obj.return_number
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur annulation retour: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur annulation: {str(e)}"
        )

# À ajouter dans returns.py après les routes existantes

@router.post("/batch", response_model=Dict[str, Any])
async def batch_create_returns(
    batch_data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    Endpoint batch pour synchroniser plusieurs retours.
    Utilisé par le sync_manager mobile.
    """
    try:
        returns_data = batch_data.get("returns", [])
        batch_id = batch_data.get("batch_id", str(uuid.uuid4()))
        
        if not current_pharmacy:
            return {
                "success": False,
                "error": "Aucune pharmacie active",
                "synced_ids": []
            }
        
        tenant_id = current_tenant.id if current_tenant else None
        
        synced_ids = []
        errors = []
        
        for return_data in returns_data:
            try:
                # Vérifier si le retour existe déjà
                existing = db.query(Return).filter(
                    Return.return_number == return_data.get("return_number"),
                    Return.tenant_id == tenant_id
                ).first()
                
                if existing:
                    synced_ids.append(return_data.get("local_id"))
                    continue
                
                # La vente retournée est supprimée : ne pas référencer une vente absente
                batch_sale_id = return_data.get("sale_id")
                if batch_sale_id and not db.query(Sale.id).filter(Sale.id == batch_sale_id).first():
                    batch_sale_id = None

                # Créer le retour
                return_obj = Return(
                    tenant_id=tenant_id,
                    pharmacy_id=current_pharmacy.id,
                    branch_id=return_data.get("branch_id"),
                    return_number=return_data.get("return_number", f"RET-{datetime.now().strftime('%Y%m%d')}-{len(synced_ids)+1:04d}"),
                    reference=return_data.get("reference"),
                    return_type=return_data.get("return_type", "customer"),
                    status=ReturnStatus.PENDING,
                    reason=return_data.get("reason", "other"),
                    sale_id=batch_sale_id,
                    invoice_number=return_data.get("invoice_number"),
                    customer_name=return_data.get("customer_name"),
                    customer_phone=return_data.get("customer_phone"),
                    return_date=datetime.fromisoformat(return_data.get("return_date")) if return_data.get("return_date") else datetime.utcnow(),
                    requested_date=datetime.utcnow(),
                    notes=return_data.get("notes"),
                    created_by=current_user.id,
                    total_amount=Decimal(str(return_data.get("total_price", 0))),
                    subtotal=Decimal(str(return_data.get("subtotal", return_data.get("total_price", 0)))),
                )
                
                db.add(return_obj)
                db.flush()
                
                # Créer les items
                quantity = return_data.get("quantity", 1)
                unit_price = Decimal(str(return_data.get("unit_price", 0)))
                
                return_item = ReturnItem(
                    tenant_id=tenant_id,
                    return_id=return_obj.id,
                    product_id=return_data.get("product_id"),
                    product_name=return_data.get("product_name", "Produit"),
                    quantity=quantity,
                    unit_price=unit_price,
                    subtotal=unit_price * quantity,
                    total=unit_price * quantity,
                    reason=return_data.get("reason", "other")
                )
                
                db.add(return_item)
                
                synced_ids.append(return_data.get("local_id"))
                
            except Exception as e:
                errors.append({
                    "local_id": return_data.get("local_id"),
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
        db.rollback()
        logger.error(f"Erreur batch returns: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "synced_ids": []
        }


# Endpoint de test
@router.get("/test", include_in_schema=False)
async def test_returns(
    current_user: User = Depends(get_current_active_user)
):
    """Endpoint de test pour le module retours"""
    return {
        "message": "Module Retours produits opérationnel",
        "version": "1.0.0",
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role
        },
        "features": [
            "Création de retours produits",
            "Approbation et rejet",
            "Restauration automatique du stock",
            "Remboursement client",
            "Échange de produits",
            "Recherche par facture/montant/client/date",
            "Filtres rapides (aujourd'hui/hier/cette semaine/ce mois)",
            "Statistiques détaillées"
        ]
    }
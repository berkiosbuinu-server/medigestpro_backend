# app/api/v1/orders.py

from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.api import deps
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.order import (
    OrderCreate,
    OrderUpdate,
    OrderResponse,
    OrderListResponse,
    OrderStatusUpdate,
    OrderPaymentUpdate,
    OrderStatus,
    PaymentStatus
)
from app.services.order_service import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])


# ==================== ROUTES SPÉCIFIQUES ====================
# Ces routes doivent être définies AVANT les routes avec paramètres dynamiques
# pour éviter les conflits de routage (ex: /stats/overview vs /{order_id})

@router.get("/stats/overview", response_model=dict)
async def get_order_stats(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user)
) -> dict:
    """
    Récupère les statistiques des commandes.
    """
    service = OrderService(db)
    return service.get_order_stats(current_tenant.id)


@router.get("/by-number/{order_number}", response_model=OrderResponse)
async def get_order_by_number(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    order_number: str = Path(..., description="Numéro de commande")
) -> OrderResponse:
    """
    Récupère une commande par son numéro.
    """
    service = OrderService(db)
    order = service.get_order_by_number(current_tenant.id, order_number)
    
    if not order:
        raise HTTPException(status_code=404, detail="Commande non trouvée")
    
    return OrderResponse.from_attributes(order)


# ==================== ROUTES CLIENTS ====================

@router.get("/customer/{customer_id}/summary", response_model=dict)
async def get_customer_order_summary(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    customer_id: str = Path(..., description="ID du client")
) -> dict:
    """
    Récupère un résumé des commandes d'un client.
    """
    service = OrderService(db)
    summary = service.get_customer_order_summary(current_tenant.id, customer_id)
    
    if not summary:
        raise HTTPException(status_code=404, detail="Client non trouvé")
    
    return summary


@router.get("/customer/{customer_id}", response_model=OrderListResponse)
async def get_customer_orders(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    customer_id: str = Path(..., description="ID du client"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000)
) -> OrderListResponse:
    """
    Récupère toutes les commandes d'un client spécifique.
    """
    service = OrderService(db)
    orders, total = service.get_customer_orders(
        tenant_id=current_tenant.id,
        customer_id=customer_id,
        skip=skip,
        limit=limit
    )
    
    return OrderListResponse(
        items=[OrderResponse.from_attributes(order) for order in orders],
        total=total,
        page=skip // limit + 1 if limit > 0 else 1,
        size=limit,
        pages=(total + limit - 1) // limit if limit > 0 else 1
    )


# ==================== ROUTES PRINCIPALES ====================

@router.post("/", response_model=OrderResponse, status_code=201)
async def create_order(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    order_data: OrderCreate
) -> OrderResponse:
    """
    Crée une nouvelle commande.
    """
    service = OrderService(db)
    order = service.create_order(current_tenant.id, order_data)
    return OrderResponse.from_attributes(order)


@router.get("/", response_model=OrderListResponse)
async def get_orders(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    status: Optional[OrderStatus] = None,
    payment_status: Optional[PaymentStatus] = None,
    customer_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> OrderListResponse:
    """
    Récupère la liste des commandes avec filtres.
    """
    service = OrderService(db)
    orders, total = service.get_orders(
        tenant_id=current_tenant.id,
        skip=skip,
        limit=limit,
        status=status,
        payment_status=payment_status,
        customer_id=customer_id,
        start_date=start_date,
        end_date=end_date
    )
    
    return OrderListResponse(
        items=[OrderResponse.from_attributes(order) for order in orders],
        total=total,
        page=skip // limit + 1 if limit > 0 else 1,
        size=limit,
        pages=(total + limit - 1) // limit if limit > 0 else 1
    )


# ==================== ROUTES AVEC PARAMÈTRES DYNAMIQUES ====================
# Ces routes doivent être définies APRÈS toutes les routes spécifiques

@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    order_id: str = Path(..., description="ID de la commande")
) -> OrderResponse:
    """
    Récupère une commande par son ID.
    """
    # Vérification pour éviter les conflits avec d'autres routes
    if order_id in ["stats", "customer", "by-number"]:
        raise HTTPException(
            status_code=400, 
            detail=f"L'ID de commande '{order_id}' n'est pas valide"
        )
    
    service = OrderService(db)
    order = service.get_order(current_tenant.id, order_id)
    
    if not order:
        raise HTTPException(status_code=404, detail="Commande non trouvée")
    
    return OrderResponse.from_attributes(order)


@router.put("/{order_id}", response_model=OrderResponse)
async def update_order(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    order_id: str = Path(..., description="ID de la commande"),
    order_data: OrderUpdate
) -> OrderResponse:
    """
    Met à jour une commande.
    """
    service = OrderService(db)
    order = service.update_order(current_tenant.id, order_id, order_data)
    
    if not order:
        raise HTTPException(status_code=404, detail="Commande non trouvée")
    
    return OrderResponse.from_attributes(order)


@router.patch("/{order_id}/status", response_model=OrderResponse)
async def update_order_status(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    order_id: str = Path(..., description="ID de la commande"),
    status_update: OrderStatusUpdate
) -> OrderResponse:
    """
    Met à jour le statut d'une commande.
    """
    service = OrderService(db)
    order = service.update_order_status(
        current_tenant.id,
        order_id,
        status_update.status,
        status_update.notes
    )
    
    if not order:
        raise HTTPException(status_code=404, detail="Commande non trouvée")
    
    return OrderResponse.from_attributes(order)


@router.patch("/{order_id}/payment", response_model=OrderResponse)
async def update_payment_status(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    order_id: str = Path(..., description="ID de la commande"),
    payment_update: OrderPaymentUpdate
) -> OrderResponse:
    """
    Met à jour le statut de paiement d'une commande.
    """
    service = OrderService(db)
    order = service.update_payment_status(
        current_tenant.id,
        order_id,
        payment_update.payment_status,
        payment_update.payment_id,
        payment_update.notes
    )
    
    if not order:
        raise HTTPException(status_code=404, detail="Commande non trouvée")
    
    return OrderResponse.from_attributes(order)


@router.delete("/{order_id}", status_code=204)
async def delete_order(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    order_id: str = Path(..., description="ID de la commande"),
    hard_delete: bool = Query(False, description="Suppression définitive")
):
    """
    Supprime une commande.
    """
    service = OrderService(db)
    deleted = service.delete_order(current_tenant.id, order_id, not hard_delete)
    
    if not deleted:
        raise HTTPException(status_code=404, detail="Commande non trouvée")
    
    return None
# app/api/routes/inventory.py
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission
from app.db.session import SessionLocal, get_db
from app.models.inventory import InventoryItem, InventorySchedule, PhysicalInventory
from app.models.product import Product
from app.models.pharmacy import Pharmacy
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.inventory import (
    InventoryCreate,
    InventoryInDB,
    InventoryItemCreate,
    InventoryItemInDB,
    InventoryReport,
    ScheduleCreate,
)
from app.services.subscription_service import check_user_subscription

router = APIRouter(prefix="/inventory", tags=["Inventory"])
logger = logging.getLogger(__name__)


# =============================================================================
# HELPERS
# =============================================================================

def _user_display_name(user: User) -> str:
    return getattr(user, "full_name", None) or getattr(user, "email", None) or str(user.id)


def _inventory_number() -> str:
    return f"INV-{datetime.utcnow().strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"


def _product_columns() -> set[str]:
    inspector = inspect(Product)
    return {col.key for col in inspector.columns}


def _ensure_inventory_exists(
    db: Session,
    inventory_id: UUID,
    tenant_id: UUID,
) -> PhysicalInventory:
    inventory = db.query(PhysicalInventory).filter(
        PhysicalInventory.id == inventory_id,
        PhysicalInventory.tenant_id == tenant_id,
    ).first()

    if not inventory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventaire non trouvé",
        )
    return inventory


def _ensure_product_exists(
    db: Session,
    product_id: UUID,
    tenant_id: UUID,
) -> Product:
    product = db.query(Product).filter(
        Product.id == product_id,
        Product.tenant_id == tenant_id,
    ).first()

    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Produit non trouvé",
        )
    return product


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _read_only_payload(db: Session, user: User) -> Dict[str, Any]:
    sub_status = check_user_subscription(db, str(user.id))
    is_active = bool(sub_status.get("is_active", False))
    has_subscription = bool(sub_status.get("has_subscription", False))
    return {
        "subscription_active": is_active,
        "has_subscription": has_subscription,
        "access_mode": "full" if is_active else "read_only",
        "is_read_only": not is_active,
    }


def _ensure_inventory_editable(inventory: PhysicalInventory) -> None:
    if inventory.status not in {"draft", "in_progress", "counting"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inventaire non modifiable",
        )


def _build_inventory_item_schema(
    item: InventoryItem,
    product_name: Optional[str] = None,
    product_code: Optional[str] = None,
) -> InventoryItemInDB:
    return InventoryItemInDB(
        id=item.id,
        tenant_id=item.tenant_id,
        inventory_id=item.inventory_id,
        product_id=item.product_id,
        expected_quantity=item.expected_quantity,
        counted_quantity=item.counted_quantity,
        variance=item.variance,
        variance_percentage=item.variance_percentage,
        batch_number=item.batch_number,
        expiry_date=item.expiry_date,
        location=item.location,
        notes=item.notes,
        status=item.status,
        counted_at=item.counted_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
        product_name=product_name,
        product_code=product_code,
    )


# =============================================================================
# INVENTAIRES - TOUTES LES ROUTES SONT ASYNCHRONES
# =============================================================================

@router.post("/", response_model=InventoryInDB)
@require_permission("inventory_manage")
async def create_inventory(
    inventory_data: InventoryCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> PhysicalInventory:
    """
    Crée un nouvel inventaire physique.
    """
    try:
        inventory_type = (
            inventory_data.inventory_type.value
            if hasattr(inventory_data.inventory_type, "value")
            else str(inventory_data.inventory_type)
        )

        inventory = PhysicalInventory(
            tenant_id=current_tenant.id,
            inventory_number=_inventory_number(),
            inventory_type=inventory_type,
            description=inventory_data.description,
            planned_date=inventory_data.planned_date,
            tags=getattr(inventory_data, "tags", None),
            created_by=current_user.id,
            status="draft",
        )
        db.add(inventory)
        db.flush()

        if inventory_type == "partial" and getattr(inventory_data, "product_ids", None):
            products = db.query(Product).filter(
                Product.tenant_id == current_tenant.id,
                Product.id.in_(inventory_data.product_ids),
            ).all()
        else:
            products = db.query(Product).filter(
                Product.tenant_id == current_tenant.id,
                Product.is_active.is_(True),
            ).all()

        items = [
            InventoryItem(
                tenant_id=current_tenant.id,
                inventory_id=inventory.id,
                product_id=product.id,
                expected_quantity=getattr(product, "quantity", 0) or 0,
                expected_value=(
                    (getattr(product, "quantity", 0) or 0)
                    * (getattr(product, "purchase_price", 0) or 0)
                ),
                status="pending",
            )
            for product in products
        ]

        if items:
            db.bulk_save_objects(items)

        db.commit()
        db.refresh(inventory)

        logger.info(
            "Inventaire créé: %s par %s",
            inventory.inventory_number,
            _user_display_name(current_user),
        )
        return inventory

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Erreur création inventaire: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la création de l'inventaire",
        )


@router.get("/", response_model=List[InventoryInDB])
@require_permission("inventory_view")
async def list_inventories(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    status_filter: Optional[str] = Query(None, alias="status"),
    inventory_type: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> List[PhysicalInventory]:
    """
    Liste les inventaires avec filtres.
    """
    query = db.query(PhysicalInventory).filter(
        PhysicalInventory.tenant_id == current_tenant.id
    )

    if status_filter:
        query = query.filter(PhysicalInventory.status == status_filter)

    if inventory_type:
        query = query.filter(PhysicalInventory.inventory_type == inventory_type)

    if start_date:
        query = query.filter(PhysicalInventory.created_at >= start_date)

    if end_date:
        query = query.filter(PhysicalInventory.created_at <= end_date)

    inventories = query.order_by(
        PhysicalInventory.created_at.desc()
    ).offset(skip).limit(limit).all()

    return inventories


@router.get("/stats/summary", response_model=Dict[str, Any])
@require_permission("inventory_view")
async def get_inventory_stats(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Statistiques globales des inventaires terminés.
    """
    query = db.query(PhysicalInventory).filter(
        PhysicalInventory.tenant_id == current_tenant.id,
        PhysicalInventory.status == "completed",
    )

    if start_date:
        query = query.filter(PhysicalInventory.end_date >= start_date)

    if end_date:
        query = query.filter(PhysicalInventory.end_date <= end_date)

    inventories = query.all()

    if not inventories:
        return {
            "total_inventories": 0,
            "total_items": 0,
            "average_variance": 0,
            "total_variance_value": 0,
            "inventories_by_type": {},
            "recent_inventories": [],
        }

    total_variance = sum(
        _safe_float(inv.variance_percentage) for inv in inventories
    )

    stats = {
        "total_inventories": len(inventories),
        "total_items": sum(inv.total_items or 0 for inv in inventories),
        "total_variance_value": sum(_safe_float(inv.variance_value) for inv in inventories),
        "average_variance": (total_variance / len(inventories)) if inventories else 0,
        "inventories_by_type": {},
        "recent_inventories": [],
    }

    for inv in inventories:
        inv_type = inv.inventory_type or "unknown"
        stats["inventories_by_type"][inv_type] = stats["inventories_by_type"].get(inv_type, 0) + 1

    recent_inventories = sorted(
        inventories,
        key=lambda x: x.end_date or x.created_at,
        reverse=True,
    )[:5]

    stats["recent_inventories"] = [
        {
            "id": str(inv.id),
            "number": inv.inventory_number,
            "type": inv.inventory_type,
            "end_date": inv.end_date.isoformat() if inv.end_date else None,
            "variance_percentage": _safe_float(inv.variance_percentage),
            "total_items": inv.total_items or 0,
        }
        for inv in recent_inventories
    ]

    return stats


@router.get("/alerts", response_model=Dict[str, Any])
async def get_inventory_alerts(
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Récupère les alertes d'inventaire.
    Accessible en lecture même si l'abonnement est inactif.
    """
    if current_tenant is None or current_user.role == "super_admin":
        logger.info(f"Super admin {current_user.email} sans tenant - retour des alertes vides")
        return {
            "success": True,
            "subscription_active": True,
            "has_subscription": True,
            "access_mode": "full",
            "is_read_only": False,
            "low_stock_count": 0,
            "expiring_soon_count": 0,
            "expired_count": 0,
            "alerts": {
                "low_stock": [],
                "expiring_soon": [],
                "expired": [],
            },
            "restrictions": None,
        }
    
    columns = _product_columns()
    access = _read_only_payload(db, current_user)
    today = date.today()
    expiry_threshold = today + timedelta(days=30)

    try:
        base_query = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
        )

        if "is_active" in columns:
            base_query = base_query.filter(Product.is_active.is_(True))

        low_stock_query = base_query
        if "min_stock" in columns:
            low_stock_query = low_stock_query.filter(Product.quantity <= Product.min_stock)
        elif "alert_threshold" in columns:
            low_stock_query = low_stock_query.filter(Product.quantity <= Product.alert_threshold)
        else:
            low_stock_query = low_stock_query.filter(Product.quantity <= 10)

        low_stock = low_stock_query.order_by(Product.quantity.asc()).limit(limit).all()

        expiring_soon: List[Product] = []
        expired: List[Product] = []

        if "expiry_date" in columns:
            expiring_soon = db.query(Product).filter(
                Product.tenant_id == current_tenant.id,
                Product.expiry_date.isnot(None),
                Product.expiry_date >= today,
                Product.expiry_date <= expiry_threshold,
            ).order_by(Product.expiry_date.asc()).limit(limit).all()

            expired = db.query(Product).filter(
                Product.tenant_id == current_tenant.id,
                Product.expiry_date.isnot(None),
                Product.expiry_date < today,
            ).order_by(Product.expiry_date.asc()).limit(limit).all()

        return {
            "success": True,
            **access,
            "low_stock_count": len(low_stock),
            "expiring_soon_count": len(expiring_soon),
            "expired_count": len(expired),
            "alerts": {
                "low_stock": [
                    {
                        "id": str(p.id),
                        "name": p.name,
                        "code": getattr(p, "code", None),
                        "barcode": getattr(p, "barcode", None),
                        "qty": _safe_float(getattr(p, "quantity", 0)),
                        "min": _safe_float(
                            getattr(p, "min_stock", None)
                            if hasattr(p, "min_stock")
                            else getattr(p, "alert_threshold", 10)
                        ),
                    }
                    for p in low_stock
                ],
                "expiring_soon": [
                    {
                        "id": str(p.id),
                        "name": p.name,
                        "code": getattr(p, "code", None),
                        "expiry": p.expiry_date.isoformat() if p.expiry_date else None,
                    }
                    for p in expiring_soon
                ],
                "expired": [
                    {
                        "id": str(p.id),
                        "name": p.name,
                        "code": getattr(p, "code", None),
                        "expiry": p.expiry_date.isoformat() if p.expiry_date else None,
                    }
                    for p in expired
                ],
            },
            "restrictions": {
                "can_view": True,
                "can_create": False,
                "can_update": False,
                "can_delete": False,
                "can_export": True,
                "max_items_visible": 100,
            } if access["is_read_only"] else None,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Erreur récupération alertes inventaire: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la récupération des alertes d'inventaire",
        )


@router.get("/{inventory_id}", response_model=InventoryReport)
@require_permission("inventory_view")
async def get_inventory(
    inventory_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> InventoryReport:
    """
    Récupère un inventaire avec ses items.
    """
    inventory = _ensure_inventory_exists(db, inventory_id, current_tenant.id)

    items_query = db.query(
        InventoryItem,
        Product.name.label("product_name"),
        Product.code.label("product_code"),
    ).join(
        Product,
        InventoryItem.product_id == Product.id,
    ).filter(
        InventoryItem.inventory_id == inventory_id,
        InventoryItem.tenant_id == current_tenant.id,
    ).all()

    inventory_items = [
        _build_inventory_item_schema(item, product_name, product_code)
        for item, product_name, product_code in items_query
    ]

    total_items = inventory.total_items or 0
    items_counted = inventory.items_counted or 0

    summary = {
        "total_items": total_items,
        "items_counted": items_counted,
        "items_missing": inventory.items_missing or 0,
        "items_excess": inventory.items_excess or 0,
        "completion_rate": (items_counted / total_items * 100) if total_items > 0 else 0,
        "system_value": _safe_float(inventory.system_value),
        "counted_value": _safe_float(inventory.counted_value),
        "variance_value": _safe_float(inventory.variance_value),
        "variance_percentage": _safe_float(inventory.variance_percentage),
    }

    recommendations: List[str] = []
    if summary["variance_percentage"] > 5:
        recommendations.append("Écart significatif détecté. Vérifier les procédures de stockage.")
    if summary["items_missing"] > 0:
        recommendations.append(f"{summary['items_missing']} items manquants. Investigation requise.")
    if items_counted < total_items:
        recommendations.append(f"Inventaire incomplet : {total_items - items_counted} items restants.")

    return InventoryReport(
        inventory=inventory,
        items=inventory_items,
        summary=summary,
        recommendations=recommendations,
    )


@router.post("/{inventory_id}/items", response_model=Dict[str, Any])
@require_permission("inventory_manage")
async def add_inventory_item(
    inventory_id: UUID,
    item_data: InventoryItemCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Ajoute ou met à jour un item d'inventaire.
    """
    inventory = _ensure_inventory_exists(db, inventory_id, current_tenant.id)
    _ensure_inventory_editable(inventory)

    product = _ensure_product_exists(db, item_data.product_id, current_tenant.id)

    try:
        existing_item = db.query(InventoryItem).filter(
            InventoryItem.inventory_id == inventory_id,
            InventoryItem.product_id == item_data.product_id,
            InventoryItem.tenant_id == current_tenant.id,
        ).first()

        if existing_item:
            existing_item.counted_quantity = item_data.counted_quantity
            existing_item.counted_at = datetime.utcnow()
            existing_item.batch_number = item_data.batch_number
            existing_item.expiry_date = item_data.expiry_date
            existing_item.location = item_data.location
            existing_item.notes = item_data.notes
            existing_item.status = "counted"
            existing_item.calculate_variance()
            saved_item = existing_item
        else:
            saved_item = InventoryItem(
                tenant_id=current_tenant.id,
                inventory_id=inventory_id,
                product_id=item_data.product_id,
                expected_quantity=getattr(product, "quantity", 0) or 0,
                counted_quantity=item_data.counted_quantity,
                batch_number=item_data.batch_number,
                expiry_date=item_data.expiry_date,
                location=item_data.location,
                notes=item_data.notes,
                counted_at=datetime.utcnow(),
                status="counted",
            )
            saved_item.calculate_variance()
            db.add(saved_item)

        if hasattr(inventory, "calculate_variance"):
            inventory.calculate_variance()

        db.commit()
        logger.info("Item ajouté/mis à jour dans l'inventaire %s", inventory.inventory_number)

        return {
            "success": True,
            "message": "Item enregistré avec succès",
            "inventory_id": str(inventory.id),
            "item_id": str(saved_item.id),
        }

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Erreur ajout item inventaire: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'ajout de l'item",
        )


@router.post("/{inventory_id}/start", response_model=Dict[str, Any])
@require_permission("inventory_manage")
async def start_inventory(
    inventory_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Démarre un inventaire.
    """
    inventory = _ensure_inventory_exists(db, inventory_id, current_tenant.id)

    if inventory.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'inventaire a déjà été démarré",
        )

    try:
        inventory.status = "in_progress"
        inventory.start_date = datetime.utcnow()
        db.commit()

        logger.info("Inventaire démarré: %s", inventory.inventory_number)
        return {"success": True, "message": "Inventaire démarré avec succès"}

    except Exception as exc:
        db.rollback()
        logger.error("Erreur démarrage inventaire: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors du démarrage de l'inventaire",
        )


@router.post("/{inventory_id}/complete", response_model=Dict[str, Any])
@require_permission("inventory_manage")
async def complete_inventory(
    inventory_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Termine un inventaire et ajuste les stocks.
    """
    inventory = _ensure_inventory_exists(db, inventory_id, current_tenant.id)

    if inventory.status not in {"in_progress", "counting"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'inventaire n'est pas en cours",
        )

    try:
        inventory.status = "completed"
        inventory.end_date = datetime.utcnow()
        inventory.validated_by = current_user.id

        if hasattr(inventory, "calculate_variance"):
            inventory.calculate_variance()

        from app.models.stock_movement import StockMovement

        for item in getattr(inventory, "items", []):
            if not item.product:
                continue

            variance = _safe_float(item.variance)
            counted_quantity = _safe_float(item.counted_quantity)
            expected_quantity = _safe_float(item.expected_quantity)

            if variance == 0:
                continue

            movement = StockMovement(
                tenant_id=current_tenant.id,
                product_id=item.product.id,
                quantity_before=expected_quantity,
                quantity_after=counted_quantity,
                quantity_change=variance,
                movement_type="inventory_adjustment",
                reason=f"Ajustement d'inventaire {inventory.inventory_number}",
                reference_number=inventory.inventory_number,
                created_by=current_user.id,
            )

            item.product.quantity = counted_quantity
            db.add(movement)

        db.commit()

        background_tasks.add_task(
            generate_inventory_report,
            inventory_id=inventory.id,
            tenant_id=current_tenant.id,
        )

        logger.info("Inventaire terminé: %s", inventory.inventory_number)
        return {
            "success": True,
            "message": "Inventaire terminé avec succès",
            "variance_value": _safe_float(inventory.variance_value),
            "variance_percentage": _safe_float(inventory.variance_percentage),
        }

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Erreur finalisation inventaire: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la finalisation de l'inventaire",
        )


@router.get("/{inventory_id}/export", response_model=Dict[str, Any])
@require_permission("inventory_view")
async def export_inventory(
    inventory_id: UUID,
    export_format: str = Query("excel", pattern="^(excel|pdf|csv)$"),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Exporte un inventaire.
    """
    inventory = _ensure_inventory_exists(db, inventory_id, current_tenant.id)

    if background_tasks:
        from app.services.export import ExportService

        export_service = ExportService(current_tenant)
        background_tasks.add_task(
            export_service.export_inventory,
            inventory_id=inventory_id,
            export_format=export_format,
            user_id=current_user.id,
        )

        return {
            "success": True,
            "message": "Export démarré en arrière-plan",
            "format": export_format,
            "inventory_number": inventory.inventory_number,
        }

    return {
        "success": False,
        "message": "Export synchrone non implémenté",
        "format": export_format,
        "inventory_number": inventory.inventory_number,
    }


# =============================================================================
# NOUVELLE ROUTE POUR DÉMARRER UNE SESSION D'INVENTAIRE (POUR LE FRONTEND)
# =============================================================================

@router.post("/start", response_model=Dict[str, Any])
@require_permission("inventory_manage")
async def start_inventory_session(
    start_data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Démarre une nouvelle session d'inventaire pour le frontend.
    Body: {"pharmacyId": "uuid", "date": "2024-01-01T00:00:00Z"}
    """
    try:
        pharmacy_id = UUID(start_data.get("pharmacyId"))
        planned_date_str = start_data.get("date", datetime.utcnow().isoformat())
        
        if isinstance(planned_date_str, str):
            planned_date = datetime.fromisoformat(planned_date_str.replace('Z', '+00:00')).date()
        else:
            planned_date = planned_date_str or datetime.utcnow().date()
        
        # Vérifier que la pharmacie existe
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == pharmacy_id,
            Pharmacy.tenant_id == current_tenant.id
        ).first()
        
        if not pharmacy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pharmacie non trouvée"
            )
        
        # Créer l'inventaire
        inventory = PhysicalInventory(
            tenant_id=current_tenant.id,
            inventory_number=_inventory_number(),
            inventory_type="full",
            description=f"Inventaire complet - {pharmacy.name}",
            planned_date=planned_date,
            created_by=current_user.id,
            status="draft",
        )
        db.add(inventory)
        db.flush()
        
        # Ajouter tous les produits actifs de la pharmacie comme items
        products = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.pharmacy_id == pharmacy_id,
            Product.is_active.is_(True),
        ).all()
        
        items = []
        for product in products:
            item = InventoryItem(
                tenant_id=current_tenant.id,
                inventory_id=inventory.id,
                product_id=product.id,
                expected_quantity=getattr(product, "quantity", 0) or 0,
                expected_value=(
                    (getattr(product, "quantity", 0) or 0)
                    * (getattr(product, "purchase_price", 0) or 0)
                ),
                status="pending",
            )
            items.append(item)
        
        if items:
            db.bulk_save_objects(items)
        
        # Démarrer l'inventaire immédiatement
        inventory.status = "in_progress"
        inventory.start_date = datetime.utcnow()
        
        db.commit()
        db.refresh(inventory)
        
        # Rafraîchir les items pour avoir leurs IDs
        db_items = db.query(InventoryItem).filter(
            InventoryItem.inventory_id == inventory.id
        ).all()
        
        # Transformer la réponse pour le frontend
        return {
            "id": str(inventory.id),
            "pharmacyId": str(pharmacy_id),
            "pharmacyName": pharmacy.name,
            "date": inventory.created_at.isoformat(),
            "status": inventory.status,
            "items": [
                {
                    "id": str(item.id),
                    "productId": str(item.product_id),
                    "productName": product.name,
                    "productCode": product.code or "",
                    "category": getattr(product, "category", "Général"),
                    "theoreticalStock": item.expected_quantity,
                    "actualStock": item.counted_quantity or 0,
                    "difference": item.variance or 0,
                    "differenceValue": (item.variance or 0) * (product.selling_price or 0),
                    "unitPrice": float(product.selling_price or 0),
                    "batchNumber": item.batch_number or "",
                    "expiryDate": item.expiry_date.isoformat() if item.expiry_date else (product.expiry_date.isoformat() if product.expiry_date else ""),
                    "location": item.location or "",
                    "lastCount": item.counted_at.isoformat() if item.counted_at else "",
                    "countedBy": str(current_user.id),
                    "notes": item.notes or "",
                    "status": item.status
                }
                for item, product in zip(db_items, products)
            ],
            "startedBy": str(current_user.id),
            "summary": {
                "totalItems": len(db_items),
                "countedItems": len([i for i in db_items if i.status != "pending"]),
                "verifiedItems": len([i for i in db_items if i.status == "verified"]),
                "positiveDifferences": len([i for i in db_items if (i.variance or 0) > 0]),
                "positiveDifferenceValue": sum(
                    ((i.variance or 0) * (p.selling_price or 0)) 
                    for i, p in zip(db_items, products) 
                    if (i.variance or 0) > 0
                ),
                "negativeDifferences": len([i for i in db_items if (i.variance or 0) < 0]),
                "negativeDifferenceValue": sum(
                    ((i.variance or 0) * (p.selling_price or 0)) 
                    for i, p in zip(db_items, products) 
                    if (i.variance or 0) < 0
                ),
                "totalDifferenceValue": sum(
                    ((i.variance or 0) * (p.selling_price or 0)) 
                    for i, p in zip(db_items, products)
                )
            }
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Erreur création session inventaire: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la création de la session d'inventaire",
        )


# =============================================================================
# PLANNINGS
# =============================================================================

@router.post("/schedules", response_model=Dict[str, Any])
@require_permission("inventory_manage")
async def create_inventory_schedule(
    schedule_data: ScheduleCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Crée un planning d'inventaire récurrent.
    """
    try:
        today = date.today()
        next_schedule = today
        schedule_type = (
            schedule_data.schedule_type.value
            if hasattr(schedule_data.schedule_type, "value")
            else str(schedule_data.schedule_type)
        )

        if schedule_type == "daily":
            next_schedule = today + timedelta(days=schedule_data.frequency)

        elif schedule_type == "weekly":
            if schedule_data.day_of_week is not None:
                days_ahead = schedule_data.day_of_week - today.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                next_schedule = today + timedelta(days=days_ahead)

        elif schedule_type == "monthly":
            if schedule_data.day_of_month is not None:
                year = today.year
                month = today.month
                if today.day >= schedule_data.day_of_month:
                    month += 1
                    if month > 12:
                        month = 1
                        year += 1
                next_schedule = date(year, month, min(schedule_data.day_of_month, 28))

        elif schedule_type == "yearly":
            if schedule_data.month_of_year is not None:
                year = today.year
                if today.month > schedule_data.month_of_year or (
                    today.month == schedule_data.month_of_year and today.day >= 1
                ):
                    year += 1
                next_schedule = date(year, schedule_data.month_of_year, 1)

        schedule = InventorySchedule(
            tenant_id=current_tenant.id,
            schedule_type=schedule_type,
            frequency=schedule_data.frequency,
            day_of_week=schedule_data.day_of_week,
            day_of_month=schedule_data.day_of_month,
            month_of_year=schedule_data.month_of_year,
            cycle_count=schedule_data.cycle_count or 0,
            description=schedule_data.description,
            next_schedule=next_schedule,
        )

        db.add(schedule)
        db.commit()

        logger.info("Planning inventaire créé par %s", _user_display_name(current_user))
        return {
            "success": True,
            "message": "Planning créé avec succès",
            "next_schedule": next_schedule.isoformat(),
        }

    except Exception as exc:
        db.rollback()
        logger.error("Erreur création planning inventaire: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la création du planning",
        )


# =============================================================================
# RAPPORTS
# =============================================================================

async def generate_inventory_report(
    inventory_id: UUID,
    tenant_id: UUID,
) -> Optional[Dict[str, Any]]:
    """
    Génère un rapport JSON d'inventaire.
    """
    db: Optional[Session] = None

    try:
        db = SessionLocal()

        inventory = db.query(PhysicalInventory).filter(
            PhysicalInventory.id == inventory_id,
            PhysicalInventory.tenant_id == tenant_id,
        ).first()

        if not inventory:
            logger.error("Inventaire %s non trouvé pour génération du rapport", inventory_id)
            return None

        items_with_products = db.query(
            InventoryItem,
            Product,
        ).join(
            Product,
            InventoryItem.product_id == Product.id,
        ).filter(
            InventoryItem.inventory_id == inventory_id,
            InventoryItem.tenant_id == tenant_id,
        ).all()

        total_items = inventory.total_items or 0
        items_counted = inventory.items_counted or 0

        report_data: Dict[str, Any] = {
            "inventory_id": str(inventory.id),
            "inventory_number": inventory.inventory_number,
            "inventory_type": inventory.inventory_type,
            "status": inventory.status,
            "created_at": inventory.created_at.isoformat() if inventory.created_at else None,
            "start_date": inventory.start_date.isoformat() if inventory.start_date else None,
            "end_date": inventory.end_date.isoformat() if inventory.end_date else None,
            "total_items": total_items,
            "items_counted": items_counted,
            "items_missing": inventory.items_missing or 0,
            "items_excess": inventory.items_excess or 0,
            "completion_rate": (items_counted / total_items * 100) if total_items > 0 else 0,
            "system_value": _safe_float(inventory.system_value),
            "counted_value": _safe_float(inventory.counted_value),
            "variance_value": _safe_float(inventory.variance_value),
            "variance_percentage": _safe_float(inventory.variance_percentage),
            "items": [],
        }

        for item, product in items_with_products:
            report_data["items"].append(
                {
                    "product_id": str(product.id),
                    "product_code": getattr(product, "code", None),
                    "product_name": getattr(product, "name", None),
                    "expected_quantity": _safe_float(item.expected_quantity),
                    "counted_quantity": _safe_float(item.counted_quantity),
                    "variance": _safe_float(item.variance),
                    "variance_percentage": _safe_float(item.variance_percentage),
                    "batch_number": item.batch_number,
                    "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
                    "location": item.location,
                    "notes": item.notes,
                    "status": item.status,
                    "counted_at": item.counted_at.isoformat() if item.counted_at else None,
                }
            )

        reports_dir = Path("reports/inventory")
        reports_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_number = str(inventory.inventory_number).replace("/", "-").replace("\\", "-")
        filename = f"inventory_report_{safe_number}_{timestamp}.json"
        filepath = reports_dir / filename

        with filepath.open("w", encoding="utf-8") as file:
            json.dump(report_data, file, ensure_ascii=False, indent=2)

        inventory.report_path = str(filepath)
        db.commit()

        logger.info("Rapport inventaire généré: %s", filepath)
        return report_data

    except Exception as exc:
        logger.error("Erreur génération rapport inventaire %s: %s", inventory_id, exc, exc_info=True)
        return None

    finally:
        if db:
            db.close()


@router.get("/{inventory_id}/report")
@require_permission("inventory_view")
async def download_inventory_report(
    inventory_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    """
    Télécharge le rapport d'inventaire JSON.
    """
    inventory = _ensure_inventory_exists(db, inventory_id, current_tenant.id)

    if not inventory.report_path:
        report_data = await generate_inventory_report(inventory_id, current_tenant.id)
        if not report_data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Impossible de générer le rapport",
            )

        return JSONResponse(
            content=report_data,
            headers={
                "Content-Disposition": f'attachment; filename="inventory_report_{inventory.inventory_number}.json"'
            },
        )

    if os.path.exists(inventory.report_path):
        filename = f"inventory_report_{inventory.inventory_number}.json"
        return FileResponse(
            path=inventory.report_path,
            filename=filename,
            media_type="application/json",
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Rapport non disponible",
    )
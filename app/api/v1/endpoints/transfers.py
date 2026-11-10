# app/api/v1/endpoints/transfers.py

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Path, status
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session, joinedload

from app.api import deps
from app.models.tenant import Tenant
from app.models.user import User
from app.models.transfert import (
    ProductTransfer,
    TransferItem,
    TransferPriority,
    TransferStatus,
    TransferType,
)
from app.schemas.transfer import (
    TransferApprove,
    TransferCancel,
    TransferCreate,
    TransferInDB,
    TransferListResponse,
    TransferReceive,
    TransferShip,
    TransferStatistics,
    TransferUpdate,
)
from app.services.transfer_service import TransferService
from app.api.v1.endpoints.stock import _check_permission
from app.models.product import Product
from app.models.branch import Branch
from app.models.stock_movement import StockMovement
from app.db.session import get_db
from app.api.deps import get_current_tenant, get_current_active_user

# Configuration du logger
logger = logging.getLogger(__name__)

# Routeur avec préfixe explicite pour éviter les conflits
router = APIRouter(prefix="/transfers", tags=["Transfers"])


# ============================================================================
# FONCTIONS UTILITAIRES (helpers)
# ============================================================================

def _get_current_user_pharmacy_id(current_user: User) -> Optional[UUID]:
    """
    Retourne l'ID de la pharmacie principale de l'utilisateur.
    """
    try:
        # Vérifier si l'utilisateur a un attribut pharmacy_id
        if hasattr(current_user, 'pharmacy_id') and current_user.pharmacy_id:
            logger.debug(f"Utilisation pharmacy_id direct: {current_user.pharmacy_id}")
            return current_user.pharmacy_id
        
        # Essayer la méthode get_primary_pharmacy
        if hasattr(current_user, 'get_primary_pharmacy'):
            primary_pharmacy = current_user.get_primary_pharmacy()
            if primary_pharmacy and hasattr(primary_pharmacy, 'id'):
                logger.debug(f"Utilisation get_primary_pharmacy: {primary_pharmacy.id}")
                return primary_pharmacy.id
        
        # Pour les admins, retourner None pour permettre l'accès à toutes les pharmacies
        if hasattr(current_user, 'role') and current_user.role in ['admin', 'super_admin']:
            logger.info(f"Admin {current_user.email} - accès toutes pharmacies")
            return None
        
        logger.error(f"Aucune pharmacie trouvée pour l'utilisateur {current_user.email}")
        return None
    except Exception as e:
        logger.error(f"Erreur lors de la récupération de la pharmacie: {e}")
        return None


def _is_super_admin(user: User) -> bool:
    """
    Vérifie si l'utilisateur est super admin.
    """
    is_admin = bool(getattr(user, "is_super_admin", False)) or getattr(user, "role", "") in ['admin', 'super_admin']
    if is_admin:
        logger.debug(f"Utilisateur {user.email} est admin")
    return is_admin


def _validate_pharmacy_access(
    current_user: User,
    tenant_id: UUID,
    requested_pharmacy_id: Optional[UUID],
) -> Optional[UUID]:
    """
    Détermine la pharmacie à utiliser et vérifie les droits d'accès.
    """
    user_pharmacy_id = _get_current_user_pharmacy_id(current_user)
    
    # Si l'utilisateur n'a pas de pharmacie assignée (admin) et qu'aucune pharmacie n'est demandée
    if user_pharmacy_id is None and requested_pharmacy_id is None:
        if _is_super_admin(current_user):
            logger.info(f"Admin {current_user.email} - pas de pharmacie spécifiée")
            return None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie associée à l'utilisateur et aucune pharmacie spécifiée",
        )
    
    # Utiliser la pharmacie demandée ou celle de l'utilisateur
    query_pharmacy_id = requested_pharmacy_id or user_pharmacy_id
    
    # Vérifier les droits d'accès
    if requested_pharmacy_id and user_pharmacy_id and requested_pharmacy_id != user_pharmacy_id:
        if not _is_super_admin(current_user):
            logger.warning(f"Accès refusé pour {current_user.email} à la pharmacie {requested_pharmacy_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous n'avez pas accès aux transferts de cette pharmacie",
            )
        logger.info(f"Admin {current_user.email} accède à la pharmacie {requested_pharmacy_id}")
    
    logger.debug(f"Pharmacie validée pour {current_user.email}: {query_pharmacy_id}")
    return query_pharmacy_id


# ============================================================================
# ROUTES SPÉCIFIQUES (doivent être définies AVANT les routes dynamiques)
# ============================================================================

@router.get("/pending", response_model=List[TransferInDB])
def get_pending_transfers(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    pharmacy_id: Optional[UUID] = Query(None, description="ID de la pharmacie"),
):
    """
    Récupère les transferts en attente pour une pharmacie.
    """
    logger.info(f"📋 GET /transfers/pending - Utilisateur: {current_user.email}")
    
    query_pharmacy_id = _validate_pharmacy_access(
        current_user, current_tenant.id, pharmacy_id
    )
    
    if query_pharmacy_id is None:
        logger.warning("Aucune pharmacie spécifiée pour les transferts en attente")
        return []

    transfers = (
        db.query(ProductTransfer)
        .filter(
            ProductTransfer.tenant_id == current_tenant.id,
            ProductTransfer.destination_pharmacy_id == query_pharmacy_id,
            ProductTransfer.status == TransferStatus.PENDING,
        )
        .options(
            joinedload(ProductTransfer.source_pharmacy),
            joinedload(ProductTransfer.destination_pharmacy),
            joinedload(ProductTransfer.requested_by),
            joinedload(ProductTransfer.items),
        )
        .order_by(desc(ProductTransfer.created_at))
        .all()
    )
    
    logger.info(f"📋 {len(transfers)} transferts en attente trouvés")
    return transfers


@router.get("/statistics/summary", response_model=TransferStatistics)
def get_transfer_statistics(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    pharmacy_id: Optional[UUID] = Query(None, description="ID de la pharmacie"),
):
    """
    Récupère les statistiques des transferts.
    """
    logger.info(f"📊 GET /transfers/statistics/summary - Utilisateur: {current_user.email}")
    
    query_pharmacy_id = _validate_pharmacy_access(
        current_user, current_tenant.id, pharmacy_id
    )
    
    # Si pas de pharmacie spécifiée (admin), retourner des stats vides ou globales
    if query_pharmacy_id is None:
        logger.info("Admin - retour des statistiques globales")
        return TransferStatistics(
            pending_incoming=0,
            pending_outgoing=0,
            in_transit_incoming=0,
            in_transit_outgoing=0,
            completed_this_month=0,
            total_value_transferred_sum=0.0,
        )

    incoming = db.query(ProductTransfer).filter(
        ProductTransfer.tenant_id == current_tenant.id,
        ProductTransfer.destination_pharmacy_id == query_pharmacy_id,
    )

    outgoing = db.query(ProductTransfer).filter(
        ProductTransfer.tenant_id == current_tenant.id,
        ProductTransfer.source_pharmacy_id == query_pharmacy_id,
    )

    completed_values = db.query(ProductTransfer.total_value).filter(
        ProductTransfer.tenant_id == current_tenant.id,
        or_(
            ProductTransfer.source_pharmacy_id == query_pharmacy_id,
            ProductTransfer.destination_pharmacy_id == query_pharmacy_id,
        ),
        ProductTransfer.status == TransferStatus.COMPLETED,
    ).all()

    total_value_transferred_sum = 0.0
    for row in completed_values:
        value = row[0] if isinstance(row, tuple) else row
        if value is not None:
            total_value_transferred_sum += float(value)

    stats = TransferStatistics(
        pending_incoming=incoming.filter(
            ProductTransfer.status == TransferStatus.PENDING
        ).count(),
        pending_outgoing=outgoing.filter(
            ProductTransfer.status == TransferStatus.PENDING
        ).count(),
        in_transit_incoming=incoming.filter(
            ProductTransfer.status == TransferStatus.IN_TRANSIT
        ).count(),
        in_transit_outgoing=outgoing.filter(
            ProductTransfer.status == TransferStatus.IN_TRANSIT
        ).count(),
        completed_this_month=db.query(ProductTransfer).filter(
            ProductTransfer.tenant_id == current_tenant.id,
            or_(
                ProductTransfer.source_pharmacy_id == query_pharmacy_id,
                ProductTransfer.destination_pharmacy_id == query_pharmacy_id,
            ),
            ProductTransfer.status == TransferStatus.COMPLETED,
            ProductTransfer.completed_date >= datetime.now().replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ),
        ).count(),
        total_value_transferred_sum=total_value_transferred_sum,
    )
    
    logger.info(f"📊 Statistiques calculées: {stats.dict()}")
    return stats


# ============================================================================
# ROUTES PRINCIPALES
# ============================================================================

@router.get("/", response_model=TransferListResponse)
def get_transfers(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    pharmacy_id: Optional[UUID] = Query(None, description="ID de la pharmacie"),
    status_filter: Optional[TransferStatus] = Query(
        None, alias="status", description="Statut du transfert"
    ),
    transfer_type: Optional[TransferType] = Query(
        None, description="Type de transfert"
    ),
    priority: Optional[TransferPriority] = Query(
        None, description="Priorité"
    ),
    direction: Optional[str] = Query(
        None, description="Direction: incoming, outgoing, all"
    ),
    start_date: Optional[datetime] = Query(
        None, description="Date de début"
    ),
    end_date: Optional[datetime] = Query(
        None, description="Date de fin"
    ),
    search: Optional[str] = Query(
        None, description="Recherche par numéro ou produit"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """
    Récupère la liste des transferts.
    """
    logger.info(f"📋 GET /transfers/ - Utilisateur: {current_user.email}, direction: {direction}")
    
    query_pharmacy_id = _validate_pharmacy_access(
        current_user, current_tenant.id, pharmacy_id
    )
    
    query = db.query(ProductTransfer).filter(
        ProductTransfer.tenant_id == current_tenant.id
    )

    # Filtre par direction - si pas de pharmacie spécifiée (admin), retourner tous
    if query_pharmacy_id is not None:
        if direction == "incoming":
            query = query.filter(
                ProductTransfer.destination_pharmacy_id == query_pharmacy_id
            )
        elif direction == "outgoing":
            query = query.filter(
                ProductTransfer.source_pharmacy_id == query_pharmacy_id
            )
        else:
            query = query.filter(
                or_(
                    ProductTransfer.source_pharmacy_id == query_pharmacy_id,
                    ProductTransfer.destination_pharmacy_id == query_pharmacy_id,
                )
            )
    elif not _is_super_admin(current_user):
        # Si pas de pharmacie et pas admin, retourner vide
        logger.warning(f"Utilisateur {current_user.email} sans pharmacie et non-admin")
        return TransferListResponse(
            transfers=[],
            total=0,
            skip=skip,
            limit=limit,
        )

    # Filtres optionnels
    if status_filter is not None:
        query = query.filter(ProductTransfer.status == status_filter)

    if transfer_type is not None:
        query = query.filter(ProductTransfer.transfer_type == transfer_type)

    if priority is not None:
        query = query.filter(ProductTransfer.priority == priority)

    if start_date is not None:
        query = query.filter(ProductTransfer.requested_date >= start_date)

    if end_date is not None:
        query = query.filter(ProductTransfer.requested_date <= end_date)

    if search:
        query = query.filter(
            or_(
                ProductTransfer.transfer_number.ilike(f"%{search}%"),
                ProductTransfer.items.any(
                    TransferItem.product_name.ilike(f"%{search}%")
                ),
            )
        )

    total = query.count()
    logger.debug(f"📊 Total des transferts avant pagination: {total}")

    transfers = (
        query.options(
            joinedload(ProductTransfer.source_pharmacy),
            joinedload(ProductTransfer.destination_pharmacy),
            joinedload(ProductTransfer.requested_by),
            joinedload(ProductTransfer.approved_by),
            joinedload(ProductTransfer.prepared_by),
            joinedload(ProductTransfer.shipped_by),
            joinedload(ProductTransfer.received_by),
            joinedload(ProductTransfer.cancelled_by),
            joinedload(ProductTransfer.items).joinedload(TransferItem.product),
        )
        .order_by(desc(ProductTransfer.created_at))
        .offset(skip)
        .limit(limit)
        .all()
    )
    
    logger.info(f"📋 {len(transfers)} transferts retournés")
    return TransferListResponse(
        transfers=transfers,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post("/", response_model=TransferInDB, status_code=status.HTTP_201_CREATED)
def create_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_data: TransferCreate,
):
    """
    Crée un nouveau transfert.
    """
    logger.info(f"➕ POST /transfers/ - Utilisateur: {current_user.email}")
    
    service = TransferService(db, current_user, current_tenant)

    try:
        result = service.create_transfer(transfer_data)
        logger.info(f"✅ Transfert créé avec succès: {result.id}")
        return result
    except PermissionError as e:
        logger.error(f"❌ Permission refusée pour la création de transfert: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        logger.error(f"❌ Erreur de validation lors de la création: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


# ============================================================================
# ROUTES AVEC PARAMÈTRES DYNAMIQUES
# ============================================================================

@router.get("/{transfer_id}", response_model=TransferInDB)
def get_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
):
    """
    Récupère un transfert par son ID.
    """
    logger.info(f"🔍 GET /transfers/{transfer_id} - Utilisateur: {current_user.email}")
    
    transfer = (
        db.query(ProductTransfer)
        .filter(
            ProductTransfer.id == transfer_id,
            ProductTransfer.tenant_id == current_tenant.id,
        )
        .options(
            joinedload(ProductTransfer.source_pharmacy),
            joinedload(ProductTransfer.destination_pharmacy),
            joinedload(ProductTransfer.requested_by),
            joinedload(ProductTransfer.approved_by),
            joinedload(ProductTransfer.prepared_by),
            joinedload(ProductTransfer.shipped_by),
            joinedload(ProductTransfer.received_by),
            joinedload(ProductTransfer.cancelled_by),
            joinedload(ProductTransfer.items).joinedload(TransferItem.product),
        )
        .first()
    )

    if not transfer:
        logger.warning(f"❌ Transfert {transfer_id} non trouvé")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transfert non trouvé",
        )

    # Vérifier l'accès à la pharmacie
    user_pharmacy_id = _get_current_user_pharmacy_id(current_user)
    
    if user_pharmacy_id is not None and not _is_super_admin(current_user):
        if (
            transfer.source_pharmacy_id != user_pharmacy_id
            and transfer.destination_pharmacy_id != user_pharmacy_id
        ):
            logger.warning(f"❌ Accès refusé pour {current_user.email} au transfert {transfer_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous n'avez pas accès à ce transfert",
            )
    
    logger.info(f"✅ Transfert {transfer_id} trouvé")
    return transfer


@router.patch("/{transfer_id}", response_model=TransferInDB)
def update_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
    transfer_data: TransferUpdate,
):
    """
    Met à jour un transfert.
    """
    logger.info(f"✏️ PATCH /transfers/{transfer_id} - Utilisateur: {current_user.email}")
    
    service = TransferService(db, current_user, current_tenant)

    try:
        result = service.update_transfer(transfer_id, transfer_data)
        logger.info(f"✅ Transfert {transfer_id} mis à jour")
        return result
    except PermissionError as e:
        logger.error(f"❌ Permission refusée pour la mise à jour: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        logger.error(f"❌ Erreur de validation lors de la mise à jour: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/{transfer_id}/approve", response_model=TransferInDB)
def approve_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
    approve_data: TransferApprove,
):
    """
    Approuve un transfert.
    """
    logger.info(f"✅ POST /transfers/{transfer_id}/approve - Utilisateur: {current_user.email}")
    
    service = TransferService(db, current_user, current_tenant)

    try:
        result = service.approve_transfer(transfer_id, approve_data)
        logger.info(f"✅ Transfert {transfer_id} approuvé")
        return result
    except PermissionError as e:
        logger.error(f"❌ Permission refusée pour l'approbation: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        logger.error(f"❌ Erreur de validation lors de l'approbation: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/{transfer_id}/prepare", response_model=TransferInDB)
def prepare_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
):
    """
    Prépare un transfert.
    """
    logger.info(f"📦 POST /transfers/{transfer_id}/prepare - Utilisateur: {current_user.email}")
    
    service = TransferService(db, current_user, current_tenant)

    try:
        result = service.prepare_transfer(transfer_id)
        logger.info(f"✅ Transfert {transfer_id} préparé")
        return result
    except PermissionError as e:
        logger.error(f"❌ Permission refusée pour la préparation: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        logger.error(f"❌ Erreur de validation lors de la préparation: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/{transfer_id}/ship", response_model=TransferInDB)
def ship_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
    ship_data: TransferShip,
):
    """
    Expédie un transfert.
    """
    logger.info(f"🚚 POST /transfers/{transfer_id}/ship - Utilisateur: {current_user.email}")
    
    service = TransferService(db, current_user, current_tenant)

    try:
        result = service.ship_transfer(transfer_id, ship_data)
        logger.info(f"✅ Transfert {transfer_id} expédié")
        return result
    except PermissionError as e:
        logger.error(f"❌ Permission refusée pour l'expédition: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        logger.error(f"❌ Erreur de validation lors de l'expédition: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/{transfer_id}/receive", response_model=TransferInDB)
def receive_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
    receive_data: TransferReceive,
):
    """
    Enregistre la réception d'un transfert.
    """
    logger.info(f"📥 POST /transfers/{transfer_id}/receive - Utilisateur: {current_user.email}")
    
    service = TransferService(db, current_user, current_tenant)

    try:
        result = service.receive_transfer(transfer_id, receive_data)
        logger.info(f"✅ Transfert {transfer_id} reçu")
        return result
    except PermissionError as e:
        logger.error(f"❌ Permission refusée pour la réception: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        logger.error(f"❌ Erreur de validation lors de la réception: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/{transfer_id}/cancel", response_model=TransferInDB)
def cancel_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
    cancel_data: TransferCancel,
):
    """
    Annule un transfert.
    """
    logger.info(f"❌ POST /transfers/{transfer_id}/cancel - Utilisateur: {current_user.email}")
    
    service = TransferService(db, current_user, current_tenant)

    try:
        result = service.cancel_transfer(transfer_id, cancel_data)
        logger.info(f"✅ Transfert {transfer_id} annulé")
        return result
    except PermissionError as e:
        logger.error(f"❌ Permission refusée pour l'annulation: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        logger.error(f"❌ Erreur de validation lors de l'annulation: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

@router.post("/transfer-between-branches", summary="Transférer du stock entre succursales")
async def transfer_stock_between_branches(
    product_id: UUID,
    quantity: int,
    from_branch_id: UUID,
    to_branch_id: UUID,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Transfère du stock d'une succursale à une autre.
    L'admin peut gérer toutes les branches.
    """
    try:
        _check_permission(current_user, ["super_admin", "superadmin", "admin", "gerant"])
        
        tenant_id = current_tenant.id if current_tenant else None
        
        # Vérifier les branches
        from_branch = db.query(Branch).filter(
            Branch.id == from_branch_id,
            Branch.tenant_id == tenant_id,
            Branch.is_active == True
        ).first()
        
        to_branch = db.query(Branch).filter(
            Branch.id == to_branch_id,
            Branch.tenant_id == tenant_id,
            Branch.is_active == True
        ).first()
        
        if not from_branch or not to_branch:
            raise HTTPException(status_code=404, detail="Succursale source ou destination non trouvée")
        
        # Récupérer le produit dans la branche source
        source_product = db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == tenant_id,
            Product.branch_id == from_branch_id,
            Product.is_active == True
        ).first()
        
        if not source_product:
            raise HTTPException(status_code=404, detail="Produit non trouvé dans la succursale source")
        
        if source_product.quantity < quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Quantité insuffisante. Disponible: {source_product.quantity}"
            )
        
        # Récupérer ou créer le produit dans la branche destination
        target_product = db.query(Product).filter(
            Product.name == source_product.name,
            Product.code == source_product.code,
            Product.tenant_id == tenant_id,
            Product.branch_id == to_branch_id,
            Product.is_active == True
        ).first()
        
        # Décrémenter la quantité source
        old_source_qty = source_product.quantity
        source_product.quantity -= quantity
        source_product.available_quantity = max(0, source_product.quantity - source_product.reserved_quantity)
        source_product.refresh_statuses()
        
        # Mouvement source (sortie)
        movement_out = StockMovement(
            tenant_id=tenant_id,
            product_id=source_product.id,
            pharmacy_id=source_product.pharmacy_id,
            branch_id=from_branch_id,
            quantity_before=old_source_qty,
            quantity_after=source_product.quantity,
            quantity_change=-quantity,
            movement_type="branch_transfer_out",
            reason=f"Transfert vers {to_branch.name}" + (f" - {reason}" if reason else ""),
            created_by=current_user.id
        )
        db.add(movement_out)
        
        if target_product:
            # Incrémenter la quantité destination
            old_target_qty = target_product.quantity
            target_product.quantity += quantity
            target_product.available_quantity = max(0, target_product.quantity - target_product.reserved_quantity)
            target_product.refresh_statuses()
            
            movement_in = StockMovement(
                tenant_id=tenant_id,
                product_id=target_product.id,
                pharmacy_id=target_product.pharmacy_id,
                branch_id=to_branch_id,
                quantity_before=old_target_qty,
                quantity_after=target_product.quantity,
                quantity_change=quantity,
                movement_type="branch_transfer_in",
                reason=f"Transfert depuis {from_branch.name}" + (f" - {reason}" if reason else ""),
                created_by=current_user.id
            )
            db.add(movement_in)
        else:
            # Créer un nouveau produit dans la destination
            new_product = Product(
                tenant_id=tenant_id,
                pharmacy_id=from_branch.parent_pharmacy_id,
                branch_id=to_branch_id,
                name=source_product.name,
                code=source_product.code,
                barcode=source_product.barcode,
                purchase_price=source_product.purchase_price,
                selling_price=source_product.selling_price,
                unit=source_product.unit,
                category=source_product.category,
                quantity=quantity,
                available_quantity=quantity,
                reserved_quantity=0,
                expiry_date=source_product.expiry_date,
                batch_number=source_product.batch_number,
                is_active=True
            )
            new_product.refresh_statuses()
            db.add(new_product)
            db.flush()
            
            movement_in = StockMovement(
                tenant_id=tenant_id,
                product_id=new_product.id,
                pharmacy_id=new_product.pharmacy_id,
                branch_id=to_branch_id,
                quantity_before=0,
                quantity_after=quantity,
                quantity_change=quantity,
                movement_type="branch_transfer_in",
                reason=f"Transfert depuis {from_branch.name}" + (f" - {reason}" if reason else ""),
                created_by=current_user.id
            )
            db.add(movement_in)
        
        db.commit()
        
        logger.info(
            f"Transfert entre branches: {quantity} x {source_product.name} de {from_branch.name} vers {to_branch.name} par {current_user.email}"
        )
        
        return {
            "message": "Transfert entre succursales effectué avec succès",
            "product_name": source_product.name,
            "quantity": quantity,
            "from_branch": from_branch.name,
            "to_branch": to_branch.name,
            "source_remaining_stock": source_product.quantity
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur transfert entre branches")
        raise HTTPException(status_code=400, detail=f"Erreur transfert entre branches: {exc}")
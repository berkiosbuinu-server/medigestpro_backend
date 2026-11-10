# app/services/transfer_service.py

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID
import uuid

from sqlalchemy.orm import Session, joinedload

from app.models.pharmacy import Pharmacy
from app.models.product import Product
from app.models.transfert import (
    ProductTransfer,
    TransferItem,
    TransferStatus,
)
from app.models.user import User
from app.schemas.transfer import (
    TransferApprove,
    TransferCancel,
    TransferCreate,
    TransferReceive,
    TransferShip,
    TransferUpdate,
)


class TransferService:
    def __init__(self, db: Session, current_user: User):
        self.db = db
        self.current_user = current_user

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _is_super_admin(self) -> bool:
        return bool(getattr(self.current_user, "is_super_admin", False))

    def _get_user_primary_pharmacy(self) -> Pharmacy:
        """
        Récupère la pharmacie principale de l'utilisateur.
        Compatible avec l'architecture SaaS multi-pharmacie.
        """
        primary_pharmacy = self.current_user.get_primary_pharmacy()
        if not primary_pharmacy:
            raise PermissionError("Aucune pharmacie associée à l'utilisateur")
        return primary_pharmacy

    def _get_user_pharmacy_id(self) -> UUID:
        return self._get_user_primary_pharmacy().id

    def _can_access_pharmacy(self, pharmacy_id: UUID) -> bool:
        """
        Vérifie si l'utilisateur courant a accès à la pharmacie.
        """
        if self._is_super_admin():
            return True

        associations = getattr(self.current_user, "pharmacy_associations", []) or []
        return any(assoc.pharmacy_id == pharmacy_id for assoc in associations)

    def _ensure_can_access_pharmacy(
        self,
        pharmacy_id: UUID,
        action_message: str = "accéder à cette pharmacie",
    ) -> None:
        if not self._can_access_pharmacy(pharmacy_id):
            raise PermissionError(f"Vous n'avez pas le droit de {action_message}")

    def _get_pharmacy_or_raise(self, pharmacy_id: UUID, label: str) -> Pharmacy:
        pharmacy = (
            self.db.query(Pharmacy)
            .filter(
                Pharmacy.id == pharmacy_id,
                Pharmacy.tenant_id == self.current_user.tenant_id,
            )
            .first()
        )
        if not pharmacy:
            raise ValueError(f"Pharmacie {label} non trouvée")
        return pharmacy

    def _get_product_or_raise(self, product_id: UUID) -> Product:
        product = (
            self.db.query(Product)
            .filter(
                Product.id == product_id,
                Product.tenant_id == self.current_user.tenant_id,
            )
            .first()
        )
        if not product:
            raise ValueError(f"Produit {product_id} non trouvé")
        return product

    def _get_transfer_or_raise(self, transfer_id: UUID) -> ProductTransfer:
        transfer = (
            self.db.query(ProductTransfer)
            .options(joinedload(ProductTransfer.items))
            .filter(
                ProductTransfer.id == transfer_id,
                ProductTransfer.tenant_id == self.current_user.tenant_id,
            )
            .first()
        )
        if not transfer:
            raise ValueError("Transfert non trouvé")
        return transfer

    def _generate_transfer_number(self) -> str:
        """
        Génère un numéro de transfert unique.
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"TRF-{timestamp}-{uuid.uuid4().hex[:6].upper()}"

    def _to_decimal(self, value: Optional[object]) -> Decimal:
        if value is None:
            return Decimal("0")
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    def _append_note(self, current_notes: Optional[str], extra_note: Optional[str]) -> Optional[str]:
        if not extra_note:
            return current_notes
        if not current_notes:
            return extra_note
        return f"{current_notes}\n{extra_note}"

    # =========================================================================
    # CRUD / ACTIONS
    # =========================================================================

    def create_transfer(self, transfer_data: TransferCreate) -> ProductTransfer:
        """
        Crée un nouveau transfert.
        """
        source_pharmacy = self._get_pharmacy_or_raise(
            transfer_data.source_pharmacy_id,
            "source",
        )
        dest_pharmacy = self._get_pharmacy_or_raise(
            transfer_data.destination_pharmacy_id,
            "destination",
        )

        if source_pharmacy.id == dest_pharmacy.id:
            raise ValueError("La pharmacie source et la pharmacie destination doivent être différentes")

        self._ensure_can_access_pharmacy(
            source_pharmacy.id,
            "créer un transfert depuis cette pharmacie",
        )

        transfer = ProductTransfer(
            tenant_id=self.current_user.tenant_id,
            source_pharmacy_id=transfer_data.source_pharmacy_id,
            destination_pharmacy_id=transfer_data.destination_pharmacy_id,
            transfer_number=self._generate_transfer_number(),
            transfer_type=transfer_data.transfer_type,
            priority=transfer_data.priority,
            reason=transfer_data.reason,
            notes=transfer_data.notes,
            expected_delivery_date=transfer_data.expected_delivery_date,
            shipping_cost=self._to_decimal(transfer_data.shipping_cost),
            requested_by_id=self.current_user.id,
            is_urgent=str(transfer_data.priority).lower() == "urgent",
        )

        self.db.add(transfer)
        self.db.flush()

        total_value = Decimal("0")
        total_quantity_requested = 0
        total_items = 0

        for item_data in transfer_data.items:
            product = self._get_product_or_raise(item_data.product_id)

            if getattr(product, "pharmacy_id", None) and product.pharmacy_id != source_pharmacy.id:
                raise ValueError(
                    f"Le produit {product.name} n'appartient pas à la pharmacie source"
                )

            requested_qty = int(item_data.requested_quantity)
            if requested_qty <= 0:
                raise ValueError(f"Quantité demandée invalide pour {product.name}")

            current_stock = getattr(product, "current_stock", 0) or 0
            if current_stock < requested_qty:
                raise ValueError(
                    f"Stock insuffisant pour {product.name}: disponible {current_stock}, demandé {requested_qty}"
                )

            unit_price = self._to_decimal(item_data.unit_price)
            item_total = unit_price * Decimal(str(requested_qty))
            total_value += item_total
            total_quantity_requested += requested_qty
            total_items += 1

            transfer_item = TransferItem(
                transfer_id=transfer.id,
                product_id=item_data.product_id,
                product_code=getattr(product, "code", None),
                product_name=product.name,
                requested_quantity=requested_qty,
                unit_price=unit_price,
                total_price=item_total,
                batch_number=item_data.batch_number,
                expiry_date=item_data.expiry_date,
                notes=item_data.notes,
            )
            self.db.add(transfer_item)

        transfer.total_value = total_value
        transfer.total_quantity_requested = total_quantity_requested
        transfer.total_items = total_items
        transfer.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(transfer)

        return transfer

    def update_transfer(self, transfer_id: UUID, update_data: TransferUpdate) -> ProductTransfer:
        """
        Met à jour un transfert.
        """
        transfer = self._get_transfer_or_raise(transfer_id)

        if transfer.status != TransferStatus.PENDING:
            raise ValueError("Impossible de modifier un transfert qui n'est pas en attente")

        self._ensure_can_access_pharmacy(
            transfer.source_pharmacy_id,
            "modifier ce transfert",
        )

        update_dict = update_data.dict(exclude_unset=True)

        forbidden_fields = {
            "id",
            "tenant_id",
            "transfer_number",
            "requested_by_id",
            "approved_by_id",
            "prepared_by_id",
            "shipped_by_id",
            "received_by_id",
            "cancelled_by_id",
            "status",
            "total_value",
            "total_items",
            "total_quantity_requested",
            "total_quantity_transferred",
            "total_quantity_received",
            "created_at",
            "updated_at",
        }

        for field, value in update_dict.items():
            if field in forbidden_fields:
                continue
            setattr(transfer, field, value)

        transfer.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(transfer)

        return transfer

    def approve_transfer(self, transfer_id: UUID, approve_data: TransferApprove) -> ProductTransfer:
        """
        Approuve un transfert.
        """
        transfer = self._get_transfer_or_raise(transfer_id)

        self._ensure_can_access_pharmacy(
            transfer.destination_pharmacy_id,
            "approuver ce transfert",
        )

        try:
            transfer.approve(self.current_user.id, approve_data.notes)
        except ValueError as e:
            raise ValueError(str(e)) from e

        for item in transfer.items:
            item.approved_quantity = item.requested_quantity
            item.updated_at = datetime.utcnow()

        transfer.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(transfer)

        return transfer

    def prepare_transfer(self, transfer_id: UUID) -> ProductTransfer:
        """
        Prépare un transfert.
        """
        transfer = self._get_transfer_or_raise(transfer_id)

        self._ensure_can_access_pharmacy(
            transfer.source_pharmacy_id,
            "préparer ce transfert",
        )

        try:
            transfer.prepare(self.current_user.id)
        except ValueError as e:
            raise ValueError(str(e)) from e

        transfer.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(transfer)

        return transfer

    def ship_transfer(self, transfer_id: UUID, ship_data: TransferShip) -> ProductTransfer:
        """
        Expédie un transfert.
        """
        transfer = self._get_transfer_or_raise(transfer_id)

        self._ensure_can_access_pharmacy(
            transfer.source_pharmacy_id,
            "expédier ce transfert",
        )

        try:
            transfer.ship(self.current_user.id, ship_data.tracking_number)
        except ValueError as e:
            raise ValueError(str(e)) from e

        total_quantity_transferred = 0

        for item in transfer.items:
            transferred_qty = item.approved_quantity or item.requested_quantity or 0
            item.transferred_quantity = transferred_qty
            item.updated_at = datetime.utcnow()
            total_quantity_transferred += int(transferred_qty or 0)

        transfer.total_quantity_transferred = total_quantity_transferred
        transfer.notes = self._append_note(transfer.notes, getattr(ship_data, "notes", None))
        transfer.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(transfer)

        return transfer

    def receive_transfer(self, transfer_id: UUID, receive_data: TransferReceive) -> ProductTransfer:
        """
        Enregistre la réception d'un transfert.
        """
        transfer = self._get_transfer_or_raise(transfer_id)

        self._ensure_can_access_pharmacy(
            transfer.destination_pharmacy_id,
            "réceptionner ce transfert",
        )

        if not transfer.can_receive():
            raise ValueError(
                f"Impossible de réceptionner un transfert avec le statut {transfer.status.value}"
            )

        item_map = {item.id: item for item in transfer.items}

        for item_update in receive_data.items:
            item = item_map.get(item_update.id)
            if not item:
                raise ValueError(f"Item de transfert introuvable: {item_update.id}")

            try:
                quantity = item_update.received_quantity or item.transferred_quantity
                item.receive(quantity, item_update.notes)
                item.updated_at = datetime.utcnow()
            except ValueError as e:
                raise ValueError(f"Erreur pour {item.product_name}: {str(e)}") from e

        transfer.total_quantity_received = sum(
            int(item.received_quantity or 0) for item in transfer.items
        )

        if hasattr(transfer, "update_statistics"):
            transfer.update_statistics()

        transfer.notes = self._append_note(transfer.notes, receive_data.notes)
        transfer.received_by_id = self.current_user.id
        transfer.actual_delivery_date = datetime.utcnow()
        transfer.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(transfer)

        return transfer

    def cancel_transfer(self, transfer_id: UUID, cancel_data: TransferCancel) -> ProductTransfer:
        """
        Annule un transfert.
        """
        transfer = self._get_transfer_or_raise(transfer_id)

        can_cancel = (
            self._is_super_admin()
            or self._can_access_pharmacy(transfer.source_pharmacy_id)
            or self._can_access_pharmacy(transfer.destination_pharmacy_id)
        )
        if not can_cancel:
            raise PermissionError("Vous n'avez pas le droit d'annuler ce transfert")

        try:
            transfer.cancel(self.current_user.id, cancel_data.reason)
        except ValueError as e:
            raise ValueError(str(e)) from e

        transfer.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(transfer)

        return transfer
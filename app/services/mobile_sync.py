# app/services/mobile_sync.py

from uuid import UUID
from typing import Dict, List, Any
from datetime import datetime

from sqlalchemy.orm import Session

# Exemple d'imports de modèles (à adapter selon ton projet)
from app.models.inventory import Inventory, InventoryItem


class MobileInventory:
    """
    Service de synchronisation mobile pour la gestion
    des inventaires en mode hors ligne.
    """

    def __init__(self, db: Session):
        self.db = db

    def prepare_offline_inventory(self, user_id: UUID) -> Dict[str, Any]:
        """
        Prépare les données d'inventaire à envoyer
        vers un appareil mobile pour travail hors ligne.
        """

        inventories = (
            self.db.query(Inventory)
            .filter(Inventory.user_id == user_id)
            .all()
        )

        data: List[Dict[str, Any]] = []

        for inv in inventories:
            items = (
                self.db.query(InventoryItem)
                .filter(InventoryItem.inventory_id == inv.id)
                .all()
            )

            data.append(
                {
                    "inventory_id": str(inv.id),
                    "name": inv.name,
                    "created_at": inv.created_at.isoformat(),
                    "items": [
                        {
                            "item_id": str(item.id),
                            "product_id": str(item.product_id),
                            "expected_quantity": item.expected_quantity,
                        }
                        for item in items
                    ],
                }
            )

        return {
            "user_id": str(user_id),
            "generated_at": datetime.utcnow().isoformat(),
            "inventories": data,
        }

    def sync_offline_counting(self, inventory_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Synchronise les comptages réalisés hors ligne
        depuis l'application mobile.
        """

        updated_items = 0

        inventories = inventory_data.get("inventories", [])

        for inv in inventories:
            for item in inv.get("items", []):
                db_item = (
                    self.db.query(InventoryItem)
                    .filter(InventoryItem.id == item["item_id"])
                    .first()
                )

                if db_item:
                    db_item.counted_quantity = item.get("counted_quantity", 0)
                    db_item.updated_at = datetime.utcnow()
                    updated_items += 1

        self.db.commit()

        return {
            "status": "success",
            "updated_items": updated_items,
            "synced_at": datetime.utcnow().isoformat(),
        }

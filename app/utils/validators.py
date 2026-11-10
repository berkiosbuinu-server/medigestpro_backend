# app/utils/validators.py

from typing import List, Dict, Any
from fastapi import HTTPException, status
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)


def validate_stock_availability(
    items: List[Any],
    products_by_id: Dict[Any, Any],
    pharmacy_name: str | None = None
) -> None:
    """
    Valide la disponibilité du stock pour une liste d'articles de vente.

    :param items: liste des items de vente (SaleItemCreate ou équivalent)
    :param products_by_id: dict {product_id: Product}
    :param pharmacy_name: nom de la pharmacie (optionnel, pour messages)
    :raises HTTPException: si stock insuffisant
    """

    unavailable_items = []

    for item in items:
        product = products_by_id.get(item.product_id)

        if not product:
            unavailable_items.append({
                "product_id": str(item.product_id),
                "error": "Produit introuvable"
            })
            continue

        available_stock = getattr(product, "quantity", 0)

        if available_stock < item.quantity:
            unavailable_items.append({
                "product": getattr(product, "name", "Unknown"),
                "requested": item.quantity,
                "available": available_stock,
                "pharmacy": pharmacy_name
            })

    if unavailable_items:
        logger.warning(
            f"Stock insuffisant détecté ({len(unavailable_items)} produits)"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Stock insuffisant pour certains articles",
                "unavailable_items": unavailable_items,
                "pharmacy": pharmacy_name
            }
        )

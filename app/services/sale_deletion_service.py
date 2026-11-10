# app/services/sale_deletion_service.py
"""
Suppression d'une vente retournée / annulée.

Règle métier : une vente retournée (ou annulée) est une vente SUPPRIMÉE.
Elle disparaît de la base (ventes, lignes, paiements, dettes, mouvements de
stock, transactions financières) et donc de tous les rapports (ventes, profit,
capital, dashboard...). Elle ne subsiste que dans le retour (table `returns`),
qui conserve un instantané complet de la vente d'origine dans
`meta_data["original_sale"]`.

Retour partiel : la vente n'est pas supprimée, mais les quantités retournées
sont retirées de ses lignes et ses totaux sont recalculés. Si toutes les
lignes sont retirées, la vente est supprimée.
"""
import logging
import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.branch import Branch
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.debt_payment import DebtPayment
from app.models.finance import FinancialTransaction
from app.models.product import Product
from app.models.return_product import (
    Return, ReturnItem, ReturnReason, ReturnStatus, ReturnType,
)
from app.models.sale import Sale, SaleItem
from app.models.stock_movement import StockMovement
from app.models.supplier_credit import SaleCreditAllocation

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


class SaleDeletionError(Exception):
    """Erreur métier pendant la suppression d'une vente."""


# ======================================================================
# Utilitaires
# ======================================================================

def _dec(value: Any) -> Decimal:
    if value is None:
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _json_safe(value: Any) -> Any:
    """Convertit récursivement en types sérialisables JSON."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "value") and not isinstance(value, (str, int, float, bool)):
        return value.value  # Enum
    return value


def generate_return_number(db: Session, tenant_id: UUID, pharmacy_id: UUID) -> str:
    """Numéro de retour unique : RET-YYYYMMDD-NNNN."""
    today = datetime.now()
    date_str = today.strftime("%Y%m%d")

    count = db.query(Return).filter(
        Return.tenant_id == tenant_id,
        Return.pharmacy_id == pharmacy_id,
        func.date(Return.created_at) == today.date(),
    ).count()

    sequence = count + 1
    number = f"RET-{date_str}-{sequence:04d}"
    # return_number est UNIQUE : on avance tant qu'il existe (retours supprimés, etc.)
    while db.query(Return.id).filter(Return.return_number == number).first():
        sequence += 1
        number = f"RET-{date_str}-{sequence:04d}"
    return number


def build_sale_snapshot(sale: Sale) -> Dict[str, Any]:
    """Instantané complet de la vente, conservé dans le retour."""
    return _json_safe({
        "id": sale.id,
        "reference": sale.reference,
        "invoice_number": sale.invoice_number,
        "status": sale.status,
        "created_at": sale.created_at,
        "pharmacy_id": sale.pharmacy_id,
        "branch_id": sale.branch_id,
        "customer_id": sale.customer_id,
        "customer_name": sale.customer_name,
        "customer_phone": sale.customer_phone,
        "seller_id": sale.created_by,
        "seller_name": sale.seller_name,
        "payment_method": sale.payment_method,
        "is_credit": sale.is_credit,
        "subtotal": sale.subtotal,
        "total_discount": sale.total_discount,
        "total_tva": sale.total_tva,
        "total_amount": sale.total_amount,
        "global_discount": sale.global_discount,
        "notes": sale.notes,
        "items": [
            {
                "id": it.id,
                "product_id": it.product_id,
                "product_code": it.product_code,
                "product_name": it.product_name,
                "quantity": it.quantity,
                "unit_price": it.unit_price,
                "discount_percent": it.discount_percent,
                "discount_amount": it.discount_amount,
                "tva_rate": it.tva_rate,
                "tva_amount": it.tva_amount,
                "subtotal": it.subtotal,
                "total": it.total,
                "batch_number": it.batch_number,
            }
            for it in sale.items
        ],
    })


def resolve_branch_id(
    db: Session, sale: Optional[Sale], user: Any, pharmacy_id: UUID
) -> UUID:
    """Branche à rattacher au retour (colonne NOT NULL)."""
    if sale is not None and sale.branch_id:
        return sale.branch_id
    active = getattr(user, "active_branch_id", None)
    if active:
        return active
    branch = db.query(Branch).filter(
        Branch.parent_pharmacy_id == pharmacy_id,
        Branch.is_active == True,  # noqa: E712
    ).first()
    if branch:
        return branch.id
    raise SaleDeletionError("Aucune branche disponible pour enregistrer le retour")


def _debts_remaining(db: Session, sale: Sale) -> Decimal:
    """Reste dû (dette client) lié à cette vente."""
    debts = db.query(Debt).filter(Debt.sale_id == sale.id).all()
    if debts:
        return sum(
            (_dec(d.remaining_amount) for d in debts if d.status not in ("paid", "cancelled")),
            ZERO,
        )
    if sale.is_credit:
        return max(ZERO, _dec(sale.total_amount) - _dec(sale.amount_paid))
    return ZERO


# ======================================================================
# Suppression physique d'une vente
# ======================================================================

def purge_sale(
    db: Session,
    sale: Sale,
    *,
    adjust_customer: bool = True,
    delete_stock_movements: bool = True,
) -> Dict[str, Any]:
    """
    Supprime physiquement la vente et tout ce qui en dépend.

    - Les retours qui référencent la vente sont conservés (sale_id -> NULL).
    - delete_stock_movements=False : les mouvements de stock sont conservés
      mais détachés de la vente (utile quand le stock n'est pas géré ici).
    Ne fait PAS de commit.
    """
    sale_id = sale.id

    # Allocations de crédit fournisseur : le remboursement fournisseur ne peut
    # pas être recalculé automatiquement -> on refuse plutôt que de corrompre.
    if db.query(SaleCreditAllocation.id).filter(SaleCreditAllocation.sale_id == sale_id).first():
        raise SaleDeletionError(
            "Cette vente a déclenché un remboursement de crédit fournisseur ; "
            "elle ne peut pas être supprimée automatiquement."
        )

    # --- Client : annuler l'effet de la vente sur ses statistiques / dettes
    if adjust_customer and sale.customer_id:
        customer = db.query(Customer).filter(Customer.id == sale.customer_id).first()
        if customer:
            total = _dec(sale.total_amount)
            customer.total_achats = max(ZERO, _dec(customer.total_achats) - total)
            customer.nombre_achats = max(0, (customer.nombre_achats or 0) - 1)
            remaining = _debts_remaining(db, sale)
            if remaining > 0:
                customer.dette_actuelle = max(ZERO, _dec(customer.dette_actuelle) - remaining)

    # --- Retours : on garde le retour, on coupe le lien
    db.query(Return).filter(Return.sale_id == sale_id).update(
        {Return.sale_id: None}, synchronize_session=False
    )
    for ret in list(sale.returns):
        ret.sale_id = None

    # --- Mouvements de stock
    if delete_stock_movements:
        db.query(StockMovement).filter(StockMovement.sale_id == sale_id).delete(
            synchronize_session=False
        )
    else:
        db.query(StockMovement).filter(StockMovement.sale_id == sale_id).update(
            {StockMovement.sale_id: None, StockMovement.sale_item_id: None},
            synchronize_session=False,
        )

    # --- Finance
    db.query(FinancialTransaction).filter(
        FinancialTransaction.sale_id == sale_id
    ).delete(synchronize_session=False)

    # --- Dettes et paiements de dettes
    debt_ids = [d.id for d in db.query(Debt.id).filter(Debt.sale_id == sale_id).all()]
    if debt_ids:
        db.query(DebtPayment).filter(DebtPayment.debt_id.in_(debt_ids)).delete(
            synchronize_session=False
        )
    db.query(DebtPayment).filter(DebtPayment.sale_id == sale_id).delete(
        synchronize_session=False
    )
    if debt_ids:
        db.query(Debt).filter(Debt.id.in_(debt_ids)).delete(synchronize_session=False)

    # --- La vente (items, paiements, remboursements : cascade ORM)
    db.expire(sale)
    db.delete(sale)
    db.flush()

    logger.info("Vente %s supprimée (retour/annulation)", sale_id)
    return {"sale_id": str(sale_id), "deleted": True}


# ======================================================================
# Création du retour correspondant à une vente
# ======================================================================

def create_return_for_sale(
    db: Session,
    sale: Sale,
    user: Any,
    *,
    reason: ReturnReason = ReturnReason.CUSTOMER_RETURN,
    reason_text: Optional[str] = None,
    source: str = "sale_cancellation",
) -> Return:
    """Crée un retour client TRAITÉ couvrant toute la vente (annulation)."""
    tenant_id = sale.tenant_id
    now = datetime.utcnow()

    refund_amount = _dec(sale.total_amount)
    if sale.is_credit:
        refund_amount = max(ZERO, refund_amount - _debts_remaining(db, sale))

    ret = Return(
        tenant_id=tenant_id,
        pharmacy_id=sale.pharmacy_id,
        branch_id=resolve_branch_id(db, sale, user, sale.pharmacy_id),
        return_number=generate_return_number(db, tenant_id, sale.pharmacy_id),
        return_type=ReturnType.CUSTOMER,
        status=ReturnStatus.PROCESSED,
        reason=reason,
        sale_id=sale.id,
        invoice_number=sale.invoice_number,
        customer_id=sale.customer_id,
        customer_name=sale.customer_name,
        customer_phone=sale.customer_phone,
        return_date=now,
        requested_date=now,
        approved_date=now,
        processed_date=now,
        subtotal=_dec(sale.subtotal),
        tax_amount=_dec(sale.total_tva),
        total_amount=_dec(sale.total_amount),
        refund_amount=refund_amount,
        refund_method=sale.payment_method,
        refund_date=now,
        notes=reason_text or f"Annulation de la vente {sale.reference}",
        created_by=user.id,
        approved_by=user.id,
        meta_data={
            "source": source,
            "sale_deleted": True,
            "original_sale": build_sale_snapshot(sale),
        },
    )
    db.add(ret)
    db.flush()

    for it in sale.items:
        db.add(ReturnItem(
            tenant_id=tenant_id,
            return_id=ret.id,
            product_id=it.product_id,
            product_code=it.product_code,
            product_name=it.product_name,
            batch_number=it.batch_number,
            expiry_date=it.expiry_date,
            quantity=it.quantity,
            unit_price=_dec(it.unit_price),
            original_unit_price=_dec(it.unit_price),
            discount_percent=_dec(it.discount_percent),
            discount_amount=_dec(it.discount_amount),
            tva_rate=_dec(it.tva_rate),
            tva_amount=_dec(it.tva_amount),
            subtotal=_dec(it.subtotal),
            total=_dec(it.total),
            reason=reason,
            reason_description=reason_text,
            sale_item_id=it.id,
            meta_data={},
        ))
    db.flush()
    db.refresh(ret)
    return ret


# ======================================================================
# Application d'un retour sur la vente (stock + suppression / ajustement)
# ======================================================================

def _match_sale_item(
    sale_items: List[SaleItem], remaining: Dict[UUID, int], ret_item: ReturnItem
) -> Optional[SaleItem]:
    """Ligne de vente correspondant à une ligne de retour."""
    if ret_item.sale_item_id:
        for si in sale_items:
            if si.id == ret_item.sale_item_id:
                return si
    for si in sale_items:
        if si.product_id == ret_item.product_id and remaining.get(si.id, 0) > 0:
            return si
    return None


def apply_sale_return(
    db: Session,
    return_obj: Return,
    sale: Sale,
    user_id: UUID,
    *,
    restore_stock: bool = True,
) -> Dict[str, Any]:
    """
    Applique un retour client sur sa vente :
      - réintègre le stock (si restore_stock) ;
      - retour total   -> la vente est supprimée ;
      - retour partiel -> les quantités retournées sont retirées de la vente.
    Le retour conserve l'instantané de la vente d'origine. Ne fait PAS de commit.
    """
    tenant_id = sale.tenant_id
    sale_items = list(sale.items)
    remaining: Dict[UUID, int] = {si.id: si.quantity for si in sale_items}
    returned: Dict[UUID, int] = {}  # sale_item_id -> quantité retournée
    matched_by_ri: Dict[UUID, int] = {}  # return_item_id -> quantité rattachée à la vente

    # 1) Associer chaque ligne de retour à une ligne de vente
    for ri in return_obj.items:
        todo = ri.quantity
        while todo > 0:
            si = _match_sale_item(sale_items, remaining, ri)
            if si is None or remaining.get(si.id, 0) <= 0:
                break
            q = min(todo, remaining[si.id])
            remaining[si.id] -= q
            returned[si.id] = returned.get(si.id, 0) + q
            todo -= q
            if ri.sale_item_id and ri.sale_item_id == si.id:
                # ligne explicitement ciblée : ne pas dériver sur une autre
                break
        matched_by_ri[ri.id] = ri.quantity - todo
        if todo > 0:
            logger.warning(
                "Retour %s: %s unité(s) de %s non rattachée(s) à la vente %s",
                return_obj.return_number, todo, ri.product_name, sale.reference,
            )

    if not returned:
        raise SaleDeletionError(
            "Aucun article du retour ne correspond à la vente d'origine"
        )

    # 2) Stock
    restored_count = 0
    for ri in return_obj.items:
        product = db.query(Product).filter(
            Product.id == ri.product_id, Product.tenant_id == tenant_id
        ).first()
        qty = matched_by_ri.get(ri.id, 0)
        if not product or qty <= 0:
            continue
        if restore_stock:
            product.quantity += qty
            product.available_quantity = max(
                0, product.quantity - (product.reserved_quantity or 0)
            )
            product.last_adjustment_date = datetime.utcnow()
            product.refresh_statuses()
            ri.quantity_restored = qty
            restored_count += qty
        else:
            # Marchandise non réintégrée (cassée, périmée...) : on trace la perte
            db.add(StockMovement(
                tenant_id=tenant_id,
                product_id=product.id,
                pharmacy_id=return_obj.pharmacy_id,
                branch_id=return_obj.branch_id,
                quantity_before=product.quantity + qty,
                quantity_after=product.quantity,
                quantity_change=-qty,
                movement_type="return_writeoff",
                reason=f"Retour sans réintégration - {return_obj.return_number}",
                reference=return_obj.return_number,
                created_by=user_id,
            ))
        product.total_sold = max(0, (product.total_sold or 0) - qty)

    return_obj.stock_restored = bool(restore_stock)
    return_obj.stock_restored_date = datetime.utcnow() if restore_stock else None

    # 3) Instantané + marquage dans le retour (avant toute suppression)
    meta = dict(return_obj.meta_data or {})
    meta.setdefault("original_sale", build_sale_snapshot(sale))
    meta["original_sale_id"] = str(sale.id)

    fully_returned = all(remaining[si.id] == 0 for si in sale_items)

    if fully_returned:
        meta["sale_deleted"] = True
        return_obj.meta_data = meta
        return_obj.sale_id = None
        purge_sale(db, sale, adjust_customer=True, delete_stock_movements=True)
        return {"sale_deleted": True, "restored_count": restored_count}

    # 4) Retour partiel : on retire les quantités retournées de la vente
    gd = _dec(sale.global_discount)
    d_subtotal = d_discount = d_tva = d_total = ZERO

    for si in sale_items:
        q = returned.get(si.id, 0)
        if q <= 0:
            continue
        ratio = Decimal(q) / Decimal(si.quantity)
        i_sub = _dec(si.subtotal) * ratio
        i_disc = _dec(si.discount_amount) * ratio
        i_tva = _dec(si.tva_amount) * ratio
        i_tot = _dec(si.total) * ratio

        d_subtotal += i_sub
        d_discount += i_disc
        d_tva += i_tva
        d_total += i_tot

        movements = db.query(StockMovement).filter(
            StockMovement.sale_id == sale.id,
            StockMovement.sale_item_id == si.id,
        ).all()

        if q >= si.quantity:
            for m in movements:
                db.delete(m)
            db.flush()
            db.delete(si)
        else:
            new_qty = si.quantity - q
            si.quantity = new_qty
            si.subtotal = _dec(si.subtotal) - i_sub
            si.discount_amount = _dec(si.discount_amount) - i_disc
            si.tva_amount = _dec(si.tva_amount) - i_tva
            si.total = _dec(si.total) - i_tot
            for m in movements:
                m.quantity_change = -new_qty
                m.quantity_after = m.quantity_before - new_qty

    net_total = d_total * (Decimal("1") - gd / Decimal("100"))
    sale.subtotal = max(ZERO, _dec(sale.subtotal) - d_subtotal)
    sale.total_discount = max(ZERO, _dec(sale.total_discount) - d_discount - (d_total - net_total))
    sale.total_tva = max(ZERO, _dec(sale.total_tva) - d_tva)
    sale.total_amount = max(ZERO, _dec(sale.total_amount) - net_total)

    # Client / dette
    if sale.customer_id:
        customer = db.query(Customer).filter(Customer.id == sale.customer_id).first()
        if customer:
            customer.total_achats = max(ZERO, _dec(customer.total_achats) - net_total)
            if sale.is_credit:
                debts = db.query(Debt).filter(Debt.sale_id == sale.id).all()
                left = net_total
                for debt in debts:
                    cut = min(left, _dec(debt.remaining_amount))
                    debt.remaining_amount = _dec(debt.remaining_amount) - cut
                    debt.initial_amount = max(ZERO, _dec(debt.initial_amount) - net_total)
                    if debt.remaining_amount <= 0:
                        debt.status = "paid"
                    left -= cut
                customer.dette_actuelle = max(ZERO, _dec(customer.dette_actuelle) - (net_total - left))

    meta["sale_deleted"] = False
    meta["partial_return"] = True
    return_obj.meta_data = meta
    db.flush()
    return {"sale_deleted": False, "restored_count": restored_count}


def cancel_sale_with_return(
    db: Session,
    sale: Sale,
    user: Any,
    *,
    reason_text: Optional[str] = None,
    reason: ReturnReason = ReturnReason.CUSTOMER_RETURN,
    restore_stock: bool = True,
) -> Dict[str, Any]:
    """
    Annulation complète d'une vente :
    crée le retour (seule trace conservée), réintègre le stock, supprime la vente.
    Ne fait PAS de commit.
    """
    ret = create_return_for_sale(
        db, sale, user, reason=reason, reason_text=reason_text,
        source="sale_cancellation",
    )
    result = apply_sale_return(db, ret, sale, user.id, restore_stock=restore_stock)
    return {
        "return_id": str(ret.id),
        "return_number": ret.return_number,
        **result,
    }

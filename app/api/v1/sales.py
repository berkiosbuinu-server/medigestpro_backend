# app/api/v1/endpoints/sales.py
"""
API de gestion des ventes avec intégration complète du stock
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session, selectinload, joinedload
from sqlalchemy import func, desc, and_, or_, distinct, asc
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, date, timedelta
import logging
from decimal import Decimal
from fastapi import status
from http import HTTPStatus  
from app.db.session import get_db
from app.models.sale import Sale, SaleItem
from app.models.product import Product, ProductStock
from app.models.customer import Customer
from app.models.user import User
from app.models.user_branch import UserBranch
from app.models.pharmacy import Pharmacy
from app.models.branch import Branch
from app.models.user_pharmacy import UserPharmacy
from app.models.tenant import Tenant
from app.models.stock_movement import StockMovement
from app.schemas.sale import (
    SaleCreate, SaleResponse, SaleInDB, SaleUpdate, SaleFilter,
    DailyStatsResponse, SaleListResponse, QuickSaleRequest, 
    SaleRefundRequest, CreditSaleCreate, ReceiptData, SaleItemCreate, 
    SaleDetailResponse, SalesStatsResponse, PharmacyStats, SaleExportRequest, 
    SaleExportResponse, UserPharmacyAccess, SaleValidationRequest, 
    SaleItemResponse, SaleImpactResponse, PeriodStatsResponse
)
from app.schemas.customer import CustomerCreate, CustomerInDB
from app.api.deps import (
    get_current_tenant, 
    get_current_user, 
    get_current_active_user, 
    require_role, 
    require_permission,
    get_current_pharmacy_entity,
    get_current_branch_entity,
    can_user_access_pharmacy
)
from app.services.inventory import InventoryService
from app.services.reporting import ReportService
from app.services.receipt import ReceiptService
from app.core.config import settings

router = APIRouter(prefix="/sales", tags=["Ventes"])
logger = logging.getLogger(__name__)


# =======================
# Tâches en arrière-plan
# =======================

async def generate_sale_receipt(sale_id: UUID, tenant_id: UUID, pharmacy_id: UUID, db: Session):
    """Génère un reçu PDF pour la vente"""
    try:
        # Vérifier que MEDIA_ROOT est configuré
        if not hasattr(settings, 'MEDIA_ROOT') or not settings.MEDIA_ROOT:
            logger.warning(f"⚠️ MEDIA_ROOT non configuré, génération de reçu ignorée pour la vente {sale_id}")
            return
        
        # Vérifier si la génération des reçus est activée
        if not getattr(settings, 'GENERATE_RECEIPTS', True):
            logger.info(f"📄 Génération de reçus désactivée pour la vente {sale_id}")
            return
        
        receipt_service = ReceiptService(db)
        sale = db.query(Sale).filter(
            Sale.id == sale_id,
            Sale.tenant_id == tenant_id,
            Sale.pharmacy_id == pharmacy_id
        ).first()
        
        if sale:
            receipt_path = await receipt_service.generate_sale_receipt(sale)
            if receipt_path:
                sale.receipt_path = receipt_path
                db.commit()
                logger.info(f"✅ Reçu généré pour la vente {sale.reference}: {receipt_path}")
            else:
                logger.warning(f"⚠️ Échec génération reçu pour {sale.reference}")
                
    except Exception as e:
        logger.error(f"❌ Erreur génération reçu pour vente {sale_id}: {str(e)}")


async def update_sales_statistics(tenant_id: UUID, pharmacy_id: UUID, sale_date: date, db: Session):
    """Met à jour les statistiques de ventes"""
    try:
        report_service = ReportService(db)
        # Note: La fonction update_daily_sales_stats a été modifiée pour accepter pharmacy_id
        await report_service.update_daily_sales_stats(tenant_id, pharmacy_id, sale_date)
        logger.info(f"Statistiques mises à jour pour {sale_date} - pharmacy {pharmacy_id}")
    except Exception as e:
        logger.error(f"Erreur mise à jour statistiques: {str(e)}")


# =======================
# Helpers
# =======================

def get_user_accessible_pharmacies(db: Session, user_id: UUID, tenant_id: Optional[UUID] = None) -> List[UUID]:
    """Récupère la liste des pharmacies accessibles par l'utilisateur"""
    if not user_id:
        return []
    
    query = db.query(UserPharmacy.pharmacy_id).filter(UserPharmacy.user_id == user_id)
    
    if tenant_id:
        query = query.join(Pharmacy).filter(Pharmacy.tenant_id == tenant_id)
    
    return [p.pharmacy_id for p in query.all()]


def check_stock_availability_with_prices(
    db: Session,
    tenant_id: UUID,
    pharmacy_id: UUID,
    items: List[SaleItemCreate],
    inventory_service: InventoryService
) -> tuple[List[Dict], List[Dict]]:
    """
    Vérifie la disponibilité des stocks et récupère les prix du stock.
    Retourne (items_disponibles, items_indisponibles)
    """
    available_items = []
    unavailable_items = []
    
    for item in items:
        product = db.query(Product).filter(
            Product.id == item.product_id,
            Product.tenant_id == tenant_id,
            Product.pharmacy_id == pharmacy_id,
            Product.is_active == True
        ).first()
        
        if not product:
            unavailable_items.append({
                "product_id": str(item.product_id),
                "product_name": "Unknown",
                "requested": float(item.quantity),
                "available": 0,
                "reason": "Produit non trouvé"
            })
            continue
        
        # Vérifier le stock disponible
        available_quantity = product.available_quantity
        
        if available_quantity < item.quantity:
            unavailable_items.append({
                "product_id": str(item.product_id),
                "product_name": product.name,
                "requested": float(item.quantity),
                "available": float(available_quantity),
                "unit": product.unit,
                "reason": "Stock insuffisant"
            })
        else:
            available_items.append({
                "product": product,
                "item": item,
                "available_stock": available_quantity,
                "selling_price": product.selling_price,
                "tva_rate": product.tva_rate if product.has_tva else Decimal('0')
            })
    
    return available_items, unavailable_items


# =======================
# Routes principales
# =======================

@router.post("/", response_model=SaleResponse, status_code=status.HTTP_201_CREATED)
async def create_sale(
    sale_data: SaleCreate,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    force_invoice_number: Optional[str] = Query(None, description="Force un numéro de facture spécifique (réservé admin)"),
    branch_id: Optional[UUID] = Query(None, description="ID de la branche (optionnel)")
):
    """
    Crée une nouvelle vente avec gestion complète par pharmacie
    et mise à jour automatique du stock avec traçabilité.
    
    GESTION DES NUMÉROS DE FACTURE:
    - Si invoice_number est fourni dans sale_data et qu'il est unique, il est utilisé
    - Sinon, le serveur génère automatiquement un numéro unique
    - En cas de conflit (numéro déjà existant), un nouveau numéro est généré
    
    IMPORTANT:
    - Le prix de vente est TOUJOURS celui défini dans le stock (product.selling_price)
    - Le taux de TVA est TOUJOURS celui défini dans le stock (product.tva_rate)
    - Les champs unit_price et tva_rate dans SaleItemCreate sont ignorés
    - Seule la remise (discount_percent) peut être appliquée par l'utilisateur
    """
    
    # Vérifier les permissions
    user_role = (current_user.role).lower() if current_user.role else ""
    allowed_roles = ["admin", "super_admin", "superadmin", "vendeur", "gerant", "caissier"]
    
    logger.info(f"🔍 Rôle utilisateur: {user_role}")
    
    if user_role not in allowed_roles:
        logger.error(f"❌ Rôle insuffisant: {user_role}")
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail=f"Rôle insuffisant pour créer une vente. Rôles autorisés: {allowed_roles}, rôle actuel: {user_role}"
        )
    
    try:
        # Déterminer la pharmacie
        pharmacy = current_pharmacy
        if not pharmacy:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucune pharmacie active sélectionnée"
            )
        
        tenant_id = current_tenant.id if current_tenant else None

        # ========================
        # DÉTERMINER LA BRANCHE
        # ========================
        final_branch_id = None
        
        # Priorité 1: branch_id passé en paramètre
        if branch_id:
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.is_active == True
            ).first()
            if branch:
                final_branch_id = branch.id
                logger.info(f"📍 Branche sélectionnée: {branch.name} ({branch.id})")
            else:
                logger.warning(f"⚠️ Branche {branch_id} non trouvée")
        
        # Priorité 2: branche active de l'utilisateur
        if not final_branch_id and current_user.active_branch_id:
            branch = db.query(Branch).filter(
                Branch.id == current_user.active_branch_id,
                Branch.is_active == True
            ).first()
            if branch:
                final_branch_id = branch.id
                logger.info(f"📍 Branche active utilisateur: {branch.name} ({branch.id})")
        
        # Priorité 3: première branche de l'utilisateur
        if not final_branch_id:
            user_branch = db.query(UserBranch).filter(
                UserBranch.user_id == current_user.id,
                UserBranch.is_active == True
            ).first()
            if user_branch and user_branch.branch:
                final_branch_id = user_branch.branch.id
                logger.info(f"📍 Première branche de l'utilisateur: {user_branch.branch.name} ({final_branch_id})")
        
        # Priorité 4: branche par défaut de la pharmacie
        if not final_branch_id:
            first_branch = db.query(Branch).filter(
                Branch.parent_pharmacy_id == pharmacy.id,
                Branch.is_active == True
            ).first()
            if first_branch:
                final_branch_id = first_branch.id
                logger.info(f"📍 Branche par défaut de la pharmacie: {first_branch.name} ({final_branch_id})")
        
        # Si toujours pas de branche, on peut laisser NULL
        if not final_branch_id:
            logger.warning(f"⚠️ Aucune branche trouvée pour la pharmacie {pharmacy.id}, vente sans branche assignée")

        # Validation des données
        if not sale_data.items or len(sale_data.items) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La vente doit contenir au moins un article"
            )

        # VALIDATION: Vérifier qu'aucun prix n'est spécifié dans la requête
        for idx, item in enumerate(sale_data.items):
            if hasattr(item, 'unit_price') and item.unit_price is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Article {idx + 1}: Le prix de vente ne peut pas être spécifié. "
                           f"Utilisez le prix défini dans le stock."
                )
            if hasattr(item, 'tva_rate') and item.tva_rate is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Article {idx + 1}: Le taux de TVA ne peut pas être spécifié. "
                           f"Utilisez le taux défini dans le stock."
                )

        # Service d'inventaire
        inventory_service = InventoryService(db, tenant_id)

        # Vérification des stocks et récupération des produits avec leurs prix
        available_items, unavailable_items = check_stock_availability_with_prices(
            db=db,
            tenant_id=tenant_id,
            pharmacy_id=pharmacy.id,
            items=sale_data.items,
            inventory_service=inventory_service
        )

        if unavailable_items:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Stock insuffisant pour certains articles",
                    "unavailable_items": unavailable_items,
                    "pharmacy": pharmacy.name
                }
            )

        # Vérification client
        client = None
        if sale_data.customer_id:
            client = db.query(Customer).filter(
                Customer.id == sale_data.customer_id,
                Customer.tenant_id == tenant_id,
                Customer.is_active == True
            ).first()
            
            if not client:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Client non trouvé ou inactif"
                )
            
            # Vérification crédit client
            if sale_data.is_credit:
                if not getattr(client, 'eligible_credit', False):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Client non éligible au crédit"
                    )
                
                credit_limit = getattr(client, 'credit_limit', Decimal('0'))
                current_debt = getattr(client, 'dette_actuelle', Decimal('0'))
                credit_available = credit_limit - current_debt
                
                # Calcul temporaire du total pour validation
                temp_total = Decimal('0')
                for available in available_items:
                    product = available["product"]
                    item = available["item"]
                    unit_price = product.selling_price
                    tva_rate = product.tva_rate if product.has_tva else Decimal('0')
                    
                    item_subtotal = unit_price * Decimal(str(item.quantity))
                    item_discount = item_subtotal * (Decimal(str(item.discount_percent)) / Decimal('100')) if item.discount_percent else Decimal('0')
                    item_after_discount = item_subtotal - item_discount
                    item_tva = item_after_discount * (tva_rate / Decimal('100'))
                    item_total = item_after_discount + item_tva
                    temp_total += item_total
                
                # Appliquer la remise globale
                if sale_data.global_discount:
                    global_discount_amount = temp_total * (Decimal(str(sale_data.global_discount)) / Decimal('100'))
                    temp_total -= global_discount_amount
                
                if temp_total > credit_available:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Crédit insuffisant. Disponible: {float(credit_available):.2f}, Requis: {float(temp_total):.2f}"
                    )

        # ========================
        # GESTION DU NUMÉRO DE FACTURE
        # ========================
        
        final_invoice_number = None
        
        # Vérifier si un numéro de facture a été fourni
        if sale_data.invoice_number:
            # Vérifier si ce numéro existe déjà
            existing_sale = db.query(Sale).filter(
                Sale.invoice_number == sale_data.invoice_number,
                Sale.tenant_id == tenant_id
            ).first()
            
            if existing_sale:
                # Conflit détecté - générer un nouveau numéro
                logger.warning(f"⚠️ Conflit numéro facture {sale_data.invoice_number} - Génération automatique")
                final_invoice_number = await generate_unique_invoice_number(
                    db, tenant_id, pharmacy.id, final_branch_id 
                )
            else:
                # Numéro valide et unique
                final_invoice_number = sale_data.invoice_number
                logger.info(f"📋 Utilisation du numéro facture client: {final_invoice_number}")
        else:
            # Aucun numéro fourni - génération automatique
            final_invoice_number = await generate_unique_invoice_number(
                db, tenant_id, pharmacy.id, final_branch_id 
            )
            logger.info(f"📋 Numéro facture généré automatiquement: {final_invoice_number}")

        # Génération référence vente avec gestion robuste des conflits
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        pharmacy_code = getattr(pharmacy, 'pharmacy_code', 'PHARM')

        # Déterminer la référence finale
        if not sale_data.reference or sale_data.reference.startswith("OFFLINE-"):
            # Aucune référence fournie ou référence offline - générer une nouvelle référence unique
            final_reference = await generate_unique_reference(db, tenant_id, pharmacy_code)
            logger.info(f"📋 Référence générée automatiquement: {final_reference}")
        else:
            # Une référence a été fournie, vérifier si elle existe déjà
            existing = db.query(Sale).filter(
                Sale.reference == sale_data.reference,
                Sale.tenant_id == tenant_id
            ).first()
            
            if existing:
                # Conflit - générer une nouvelle référence
                logger.warning(f"⚠️ Conflit référence {sale_data.reference} - Génération automatique")
                final_reference = await generate_unique_reference(db, tenant_id, pharmacy_code)
                logger.info(f"📋 Nouvelle référence générée: {final_reference}")
            else:
                # Référence valide et unique
                final_reference = sale_data.reference
                logger.info(f"📋 Utilisation de la référence fournie: {final_reference}")
        # Calcul des totaux en utilisant les prix du stock
        subtotal = Decimal('0')
        total_discount = Decimal('0')
        total_tva = Decimal('0')
        total_amount = Decimal('0')
        
        # Stocker les données calculées pour chaque item
        items_calculated = []
        
        for available in available_items:
            product = available["product"]
            item = available["item"]
            
            # Utiliser le prix de vente du stock
            unit_price = product.selling_price
            tva_rate = product.tva_rate if product.has_tva else Decimal('0')
            discount_percent = Decimal(str(item.discount_percent)) if item.discount_percent else Decimal('0')
            
            # Calculs
            item_subtotal = unit_price * Decimal(str(item.quantity))
            item_discount = item_subtotal * (discount_percent / Decimal('100'))
            item_after_discount = item_subtotal - item_discount
            item_tva = item_after_discount * (tva_rate / Decimal('100'))
            item_total = item_after_discount + item_tva
            
            subtotal += item_subtotal
            total_discount += item_discount
            total_tva += item_tva
            total_amount += item_total
            
            # Stocker pour utilisation ultérieure
            items_calculated.append({
                "product": product,
                "item": item,
                "unit_price": unit_price,
                "tva_rate": tva_rate,
                "discount_percent": discount_percent,
                "item_subtotal": item_subtotal,
                "item_discount": item_discount,
                "item_tva": item_tva,
                "item_total": item_total
            })
        
        # Appliquer la remise globale
        global_discount_percent = Decimal(str(sale_data.global_discount)) if sale_data.global_discount else Decimal('0')
        if global_discount_percent > 0:
            global_discount_amount = total_amount * (global_discount_percent / Decimal('100'))
            total_discount += global_discount_amount
            total_amount -= global_discount_amount

        # Créer la vente avec le numéro de facture final ET la branche
        sale = Sale(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy.id,
            branch_id=final_branch_id,  # ← AJOUT DE LA BRANCHE !
            reference=final_reference,  
            customer_id=sale_data.customer_id,
            customer_name=client.nom_complet if client else sale_data.customer_name,
            customer_phone=client.telephone if client else sale_data.customer_phone,
            created_by=current_user.id,
            seller_name=getattr(current_user, 'nom_complet', current_user.email),
            payment_method=sale_data.payment_method.value,
            reference_payment=sale_data.reference_payment,
            payment_date=datetime.utcnow() if sale_data.payment_method.value != "credit" else None,
            is_credit=sale_data.is_credit,
            credit_due_date=sale_data.credit_due_date,
            guarantee_deposit=Decimal(str(sale_data.guarantee_deposit)) if sale_data.guarantee_deposit else Decimal('0'),
            guarantor_name=sale_data.guarantor_name,
            guarantor_phone=sale_data.guarantor_phone,
            global_discount=global_discount_percent,
            notes=sale_data.notes,
            subtotal=subtotal,
            total_discount=total_discount,
            total_tva=total_tva,
            total_amount=total_amount,
            status="pending" if sale_data.is_credit else "completed",
            invoice_number=final_invoice_number,
            cancelled_at=None,
            cancelled_by=None,
            cancel_reason=None
        )
        
        db.add(sale)
        db.flush()

        # Création des items et mise à jour des stocks
        sale_items = []
        
        for calc in items_calculated:
            product = calc["product"]
            item = calc["item"]
            
            # Créer l'item de vente avec les prix du stock
            sale_item = SaleItem(
                sale_id=sale.id,
                tenant_id=tenant_id,
                pharmacy_id=pharmacy.id,
                product_id=item.product_id,
                product_code=product.code,
                product_name=product.name,
                quantity=item.quantity,
                unit_price=calc["unit_price"],
                discount_percent=calc["discount_percent"],
                discount_amount=calc["item_discount"],
                tva_rate=calc["tva_rate"],
                tva_amount=calc["item_tva"],
                subtotal=calc["item_subtotal"],
                total=calc["item_total"],
                batch_number=item.batch_number,
                expiry_date=item.expiry_date
            )
            db.add(sale_item)
            sale_items.append(sale_item)
            
            # Mettre à jour le stock
            old_quantity = product.quantity
            new_quantity = old_quantity - item.quantity
            
            if new_quantity < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Stock insuffisant pour {product.name}"
                )
            
            product.quantity = new_quantity
            product.available_quantity = max(0, new_quantity - (product.reserved_quantity or 0))
            product.total_sold = (product.total_sold or 0) + item.quantity
            product.last_sale_date = datetime.utcnow()
            product.refresh_statuses()
            
            # Créer un mouvement de stock avec traçabilité du prix utilisé
            movement = StockMovement(
                tenant_id=tenant_id,
                branch_id=final_branch_id,  # ← Utiliser la même branche
                product_id=product.id,
                pharmacy_id=pharmacy.id,
                quantity_before=old_quantity,
                quantity_after=new_quantity,
                quantity_change=-item.quantity,
                movement_type="sale",
                reason="vente",
                reference=sale.reference,
                batch_number=item.batch_number,
                purchase_price=product.purchase_price,
                selling_price=calc["unit_price"],
                sale_id=sale.id,
                sale_item_id=sale_item.id,
                created_by=current_user.id
            )
            db.add(movement)

        # Mise à jour client
        if client:
            client.total_achats = (getattr(client, 'total_achats', Decimal('0')) or Decimal('0')) + total_amount
            client.nombre_achats = (getattr(client, 'nombre_achats', 0) or 0) + 1
            client.dernier_achat = datetime.utcnow()
            client.dernier_montant = total_amount
            
            if sale_data.is_credit:
                client.dette_actuelle = (getattr(client, 'dette_actuelle', Decimal('0')) or Decimal('0')) + total_amount

        # Incrémenter le compteur de factures
        await increment_invoice_counter(db, tenant_id, pharmacy.id, final_invoice_number)

        # Validation automatique si configuré
        if getattr(settings, 'AUTO_VALIDATE_SALES', False) and current_user.role in ["admin", "super_admin", "superadmin", "gerant"]:
            sale.status = "completed"
            sale.validated_by = current_user.id
            sale.validated_at = datetime.utcnow()

        db.commit()
        db.refresh(sale)

        # Tâches en arrière-plan
        background_tasks.add_task(generate_sale_receipt, sale.id, tenant_id, pharmacy.id, db)
        background_tasks.add_task(update_sales_statistics, tenant_id, pharmacy.id, datetime.now().date(), db)

        logger.info(
            f"Vente créée: {sale.reference} - Facture: {final_invoice_number} - "
            f"Branche: {final_branch_id} - "
            f"{len(sale_items)} articles - Pharmacie: {pharmacy.name} par {current_user.email}"
        )

        # Construction de la réponse
        return SaleResponse(
            message="Vente créée avec succès",
            sale=SaleInDB.model_validate(sale),
            pharmacy={
                "id": str(pharmacy.id),
                "name": pharmacy.name,
                "code": getattr(pharmacy, 'pharmacy_code', None)
            },
            receipt_available=True,
            receipt_url=f"/api/v1/sales/{sale.id}/receipt",
            generated_invoice_number=final_invoice_number
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création vente: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=f"Erreur création vente: {str(e)}"
        )

# ========================
# FONCTIONS UTILITAIRES POUR LES NUMÉROS DE FACTURE
# ========================

async def generate_unique_invoice_number(
    db: Session,
    tenant_id: UUID,
    pharmacy_id: UUID,
    branch_id: UUID,  # ← Ajouter branch_id
    max_attempts: int = 5
) -> str:
    """
    Génère un numéro de facture unique pour une branche spécifique.
    Format: INV-{branch_code}-{YYYYMMDD}-{XXXX}
    """
    from app.models.invoice_counter import InvoiceCounter
    
    today = datetime.now().date()
    date_str = today.strftime("%Y%m%d")
    
    # Récupérer le code de la branche (3-4 lettres)
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    branch_code = branch.code if branch and branch.code else branch.name[:4].upper() if branch.name else "BR"
    
    for attempt in range(max_attempts):
        # Récupérer ou créer le compteur pour cette branche
        counter = db.query(InvoiceCounter).filter(
            InvoiceCounter.tenant_id == tenant_id,
            InvoiceCounter.pharmacy_id == pharmacy_id,
            InvoiceCounter.branch_id == branch_id,  # ← Filtrer par branche
            InvoiceCounter.date == today
        ).first()
        
        if not counter:
            counter = InvoiceCounter(
                tenant_id=tenant_id,
                pharmacy_id=pharmacy_id,
                branch_id=branch_id,  # ← Ajouter branch_id
                date=today,
                current_number=1
            )
            db.add(counter)
            db.flush()
        
        # Générer le numéro avec code branche
        sequence = counter.current_number
        invoice_number = f"INV-{branch_code}-{date_str}-{sequence:04d}"
        
        # Vérifier l'unicité (maintenant unique par branche)
        existing = db.query(Sale).filter(
            Sale.invoice_number == invoice_number,
            Sale.tenant_id == tenant_id,
            Sale.branch_id == branch_id  # ← Vérifier aussi par branche
        ).first()
        
        if not existing:
            counter.current_number += 1
            db.flush()
            return invoice_number
        
        # Conflit - incrémenter et réessayer
        counter.current_number += 1
        logger.warning(f"⚠️ Conflit sur {invoice_number}, tentative {attempt + 2}/{max_attempts}")
    
    # Fallback
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"INV-{branch_code}-{timestamp}"

async def increment_invoice_counter(
    db: Session,
    tenant_id: UUID,
    pharmacy_id: UUID,
    used_invoice_number: str
) -> None:
    """
    Incrémente le compteur après utilisation réussie d'un numéro.
    Utile pour la synchronisation avec les clients.
    """
    from app.models.invoice_counter import InvoiceCounter
    
    # Extraire la date du numéro de facture
    try:
        parts = used_invoice_number.split('-')
        if len(parts) >= 3 and parts[0] == 'INV':
            date_str = parts[1]
            sequence = int(parts[2])
            
            # S'assurer que le compteur est au moins à séquence + 1
            today = datetime.now().date()
            counter = db.query(InvoiceCounter).filter(
                InvoiceCounter.tenant_id == tenant_id,
                InvoiceCounter.pharmacy_id == pharmacy_id,
                InvoiceCounter.date == today
            ).first()
            
            if counter and counter.current_number <= sequence:
                counter.current_number = sequence + 1
                db.flush()
    except Exception as e:
        logger.error(f"Erreur incrémentation compteur: {e}")


# ========================
# ENDPOINTS POUR SYNCHRONISATION DES FACTURES
# ========================

@router.get("/next-invoice-number")
async def get_next_invoice_number(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
):
    """
    Récupère le prochain numéro de facture disponible.
    GARANTIT l'unicité en vérifiant l'existence réelle dans la base.
    """
    from sqlalchemy import text
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy_id = current_pharmacy.id if current_pharmacy else None
    
    if not pharmacy_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie sélectionnée"
        )
    
    today = datetime.now().date()
    date_str = today.strftime("%Y%m%d")
    
    max_attempts = 20
    attempt = 0
    
    while attempt < max_attempts:
        attempt += 1
        
        # Utiliser une transaction pour éviter les conditions de course
        try:
            # Verrouiller la ligne du compteur (SELECT FOR UPDATE)
            lock_query = text("""
                SELECT current_number FROM invoice_counter 
                WHERE tenant_id::text = :tenant_id 
                AND pharmacy_id::text = :pharmacy_id 
                AND date = :date
                FOR UPDATE
            """)
            
            result = db.execute(lock_query, {
                "tenant_id": str(tenant_id) if tenant_id else None,
                "pharmacy_id": str(pharmacy_id),
                "date": today
            }).first()
            
            if result:
                current_number = result[0]
            else:
                current_number = 1
                
                # Créer le compteur
                insert_counter = text("""
                    INSERT INTO invoice_counter (tenant_id, pharmacy_id, date, current_number)
                    VALUES (:tenant_id, :pharmacy_id, :date, 1)
                """)
                db.execute(insert_counter, {
                    "tenant_id": tenant_id,
                    "pharmacy_id": pharmacy_id,
                    "date": today
                })
            
            # Générer le numéro potentiel
            test_number = f"INV-{date_str}-{current_number:04d}"
            
            # Vérifier si ce numéro existe DÉJÀ
            check_exists = text("""
                SELECT id FROM sales 
                WHERE invoice_number = :invoice_number
                AND tenant_id::text = :tenant_id
                LIMIT 1
            """)
            
            existing = db.execute(check_exists, {
                "invoice_number": test_number,
                "tenant_id": str(tenant_id) if tenant_id else None
            }).first()
            
            if not existing:
                # ✅ Numéro libre ! Le réserver immédiatement
                # Incrémenter le compteur pour la prochaine fois
                update_counter = text("""
                    UPDATE invoice_counter 
                    SET current_number = current_number + 1
                    WHERE tenant_id::text = :tenant_id 
                    AND pharmacy_id::text = :pharmacy_id 
                    AND date = :date
                """)
                db.execute(update_counter, {
                    "tenant_id": str(tenant_id) if tenant_id else None,
                    "pharmacy_id": str(pharmacy_id),
                    "date": today
                })
                
                db.commit()
                
                return {
                    "invoice_number": test_number,
                    "sequence_number": current_number,
                    "date": date_str,
                    "pharmacy_id": str(pharmacy_id),
                    "tenant_id": str(tenant_id) if tenant_id else None,
                    "unique": True,
                    "attempt": attempt
                }
            else:
                # Ce numéro existe déjà - mettre à jour le compteur et réessayer
                logger.warning(f"⚠️ Conflit: {test_number} existe déjà (tentative {attempt}/{max_attempts})")
                
                # Mettre à jour le compteur pour passer au suivant
                update_counter = text("""
                    UPDATE invoice_counter 
                    SET current_number = current_number + 1
                    WHERE tenant_id::text = :tenant_id 
                    AND pharmacy_id::text = :pharmacy_id 
                    AND date = :date
                """)
                db.execute(update_counter, {
                    "tenant_id": str(tenant_id) if tenant_id else None,
                    "pharmacy_id": str(pharmacy_id),
                    "date": today
                })
                
                db.commit()
                continue  # Réessayer avec le nouveau numéro
                
        except Exception as e:
            logger.error(f"Erreur tentative {attempt}: {e}")
            db.rollback()
            
            if attempt >= max_attempts:
                break
            continue
    
    # Fallback ultime : timestamp + UUID
    import uuid
    fallback_number = f"INV-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:8]}"
    
    return {
        "invoice_number": fallback_number,
        "sequence_number": 0,
        "date": date_str,
        "pharmacy_id": str(pharmacy_id),
        "tenant_id": str(tenant_id) if tenant_id else None,
        "fallback": True,
        "warning": f"Génération fallback après {max_attempts} tentatives"
    }
    
@router.post("/sync-invoice-counter")
async def sync_invoice_counter(
    sync_data: dict,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """
    Synchronise le compteur de factures depuis un client.
    Utile pour la reprise après mode offline.
    """
    from app.models.invoice_counter import InvoiceCounter
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy_id = UUID(sync_data.get("pharmacy_id"))
    last_invoice_number = sync_data.get("last_invoice_number")
    max_sequence = sync_data.get("max_sequence", 0)
    
    today = datetime.now().date()
    
    counter = db.query(InvoiceCounter).filter(
        InvoiceCounter.tenant_id == tenant_id,
        InvoiceCounter.pharmacy_id == pharmacy_id,
        InvoiceCounter.date == today
    ).first()
    
    if not counter:
        counter = InvoiceCounter(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy_id,
            date=today,
            current_number=1
        )
        db.add(counter)
    
    # Prendre le maximum entre le compteur local et celui du client
    if max_sequence > counter.current_number:
        counter.current_number = max_sequence
        logger.info(f"Compteur mis à jour: {counter.current_number} (client: {max_sequence})")
    
    db.commit()
    
    return {
        "success": True,
        "new_counter": counter.current_number,
        "next_invoice": f"INV-{today.strftime('%Y%m%d')}-{counter.current_number:04d}"
    }


@router.post("/confirm-invoice-number", status_code=status.HTTP_200_OK)
async def confirm_invoice_number(
    request: dict,  # Accepter dict pour plus de flexibilité
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
):
    """
    Confirme l'utilisation d'un numéro de facture et incrémente le compteur.
    Accepte soit invoice_number soit pharmacy_id dans le body.
    """
    from app.models.invoice_counter import InvoiceCounter
    
    # Extraire les paramètres (supporte plusieurs formats)
    invoice_number = request.get("invoice_number") or request.get("invoiceNumber")
    pharmacy_id = request.get("pharmacy_id") or request.get("pharmacyId")
    
    # Si pharmacy_id non fourni, utiliser celui de la session
    if not pharmacy_id and current_pharmacy:
        pharmacy_id = current_pharmacy.id
    elif not pharmacy_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pharmacy_id requis"
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    today = datetime.now().date()
    
    # Chercher le compteur
    counter = db.query(InvoiceCounter).filter(
        InvoiceCounter.tenant_id == tenant_id,
        InvoiceCounter.pharmacy_id == pharmacy_id,
        InvoiceCounter.date == today
    ).first()
    
    if counter:
        # Si un numéro spécifique est fourni, s'assurer que le compteur est au moins à ce niveau
        if invoice_number:
            try:
                # Extraire le numéro séquentiel de INV-20260424-0042
                parts = invoice_number.split('-')
                if len(parts) >= 3:
                    sequence = int(parts[2])
                    if counter.current_number <= sequence:
                        counter.current_number = sequence + 1
                        db.commit()
            except Exception as e:
                logger.warning(f"Erreur extraction séquence: {e}")
        else:
            # Sinon, simplement incrémenter
            counter.current_number += 1
            db.commit()
    else:
        # Créer un nouveau compteur
        if invoice_number:
            sequence = 2  # Valeur par défaut
            try:
                parts = invoice_number.split('-')
                if len(parts) >= 3:
                    sequence = int(parts[2]) + 1
            except:
                pass
        else:
            sequence = 2
            
        counter = InvoiceCounter(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy_id,
            date=today,
            current_number=sequence
        )
        db.add(counter)
        db.commit()
    
    return {
        "success": True,
        "invoice_number": invoice_number,
        "new_sequence": counter.current_number,
        "pharmacy_id": str(pharmacy_id),
        "date": today.isoformat()
    }

@router.get("/last-invoice-number")
async def get_last_invoice_number(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
):
    """
    Récupère le dernier numéro de facture utilisé (séquence)
    """
    from sqlalchemy import text
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy_id = current_pharmacy.id if current_pharmacy else None
    
    if not pharmacy_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie sélectionnée"
        )
    
    today = datetime.now().date()
    date_str = today.strftime("%Y%m%d")
    
    # Récupérer la dernière séquence utilisée
    query = text("""
        SELECT MAX(CAST(SUBSTRING(invoice_number FROM 'INV-[0-9]+-([0-9]+)$') AS INTEGER)) as last_sequence
        FROM sales 
        WHERE invoice_number LIKE :pattern
        AND tenant_id::text = :tenant_id
        AND pharmacy_id::text = :pharmacy_id
    """)
    
    result = db.execute(query, {
        "pattern": f"INV-{date_str}-%",
        "tenant_id": str(tenant_id) if tenant_id else None,
        "pharmacy_id": str(pharmacy_id)
    }).first()
    
    last_sequence = result[0] if result and result[0] else 0
    
    return {
        "last_sequence": last_sequence,
        "date": date_str,
        "pharmacy_id": str(pharmacy_id),
        "next_sequence": last_sequence + 1,
        "next_invoice": f"INV-{date_str}-{last_sequence + 1:04d}"
    }

# ========================
# FONCTIONS UTILITAIRES POUR LES RÉFÉRENCES UNIQUES
# ========================

async def generate_unique_reference(
    db: Session,
    tenant_id: UUID,
    pharmacy_code: str,
    max_attempts: int = 5
) -> str:
    """Génère une référence de vente unique"""
    import uuid
    from datetime import datetime
    
    for attempt in range(max_attempts):
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        unique_suffix = str(uuid.uuid4().hex)[:8].upper()
        reference = f"VNT-{timestamp}-{pharmacy_code}-{unique_suffix}"
        
        existing = db.query(Sale).filter(
            Sale.reference == reference,
            Sale.tenant_id == tenant_id
        ).first()
        
        if not existing:
            return reference
    
    # Fallback ultime
    return f"VNT-{uuid.uuid4().hex.upper()}"
# =======================
# Endpoint: Impact des ventes sur le stock
# =======================

@router.get("/stock-impact", response_model=List[SaleImpactResponse])
async def get_sales_stock_impact(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    product_id: Optional[UUID] = Query(None, description="Filtrer par produit"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    limit: int = Query(100, ge=1, le=1000)
):
    """
    Récupère l'impact des ventes sur le stock
    Point de communication avec le module stock
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacies_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacies_query = pharmacies_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacies_query = pharmacies_query.filter(Pharmacy.id == pharmacy_id)
            pharmacy_ids = [p.id for p in pharmacies_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        if not pharmacy_ids:
            return []
        
        # Construction de la requête
        query = db.query(
            Product.id.label("product_id"),
            Product.code,
            Product.name,
            Product.unit,
            func.coalesce(func.sum(SaleItem.quantity), 0).label("total_sold"),
            func.coalesce(func.sum(SaleItem.total), 0).label("total_revenue"),
            func.count(distinct(Sale.id)).label("sale_count"),
            func.avg(SaleItem.unit_price).label("average_price")
        ).join(
            SaleItem, SaleItem.product_id == Product.id
        ).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids)
        )
        
        if tenant_id:
            query = query.filter(Product.tenant_id == tenant_id)
            query = query.filter(Sale.tenant_id == tenant_id)
        
        if product_id:
            query = query.filter(Product.id == product_id)
        
        if start_date:
            query = query.filter(func.date(Sale.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(Sale.created_at) <= end_date)
        
        results = query.group_by(
            Product.id, Product.code, Product.name, Product.unit
        ).order_by(
            desc("total_sold")
        ).limit(limit).all()
        
        return [
            SaleImpactResponse(
                product_id=row.product_id,
                product_code=row.code,
                product_name=row.name,
                unit=row.unit,
                total_sold=int(row.total_sold),
                total_revenue=float(row.total_revenue),
                sale_count=int(row.sale_count),
                average_price=float(row.average_price),
                stock_impact=-int(row.total_sold)
            )
            for row in results
        ]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération impact stock: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération impact stock: {str(e)}"
        )


# =======================
# Endpoint: Mouvements de stock liés à une vente
# =======================

@router.get("/stock-movements/{sale_id}")
async def get_sale_stock_movements(
    sale_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère tous les mouvements de stock liés à une vente spécifique
    Point de communication avec le module stock
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Vérifier que la vente existe et est accessible
        sale_query = db.query(Sale).filter(Sale.id == sale_id)
        if tenant_id:
            sale_query = sale_query.filter(Sale.tenant_id == tenant_id)
        
        sale = sale_query.first()
        
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vente non trouvée"
            )
        
        # Vérifier l'accès à la pharmacie
        if current_user.role not in ["super_admin", "superadmin", "admin"]:
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if sale.pharmacy_id not in accessible_pharmacies:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette vente"
                )
        
        # Récupérer les mouvements de stock liés à cette vente
        movements = db.query(StockMovement).filter(
            StockMovement.sale_id == sale_id,
            StockMovement.tenant_id == tenant_id
        ).all()
        
        return {
            "sale_id": str(sale.id),
            "sale_reference": sale.reference,
            "pharmacy_id": str(sale.pharmacy_id),
            "movements": [
                {
                    "id": str(m.id),
                    "product_id": str(m.product_id),
                    "product_name": getattr(m.product, "name", None),
                    "quantity_change": float(m.quantity_change) if m.quantity_change else 0,
                    "quantity_before": float(m.quantity_before) if m.quantity_before else 0,
                    "quantity_after": float(m.quantity_after) if m.quantity_after else 0,
                    "movement_type": m.movement_type,
                    "reason": m.reason,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "batch_number": m.batch_number
                }
                for m in movements
            ],
            "total_movements": len(movements)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération mouvements stock pour vente {sale_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération mouvements stock: {str(e)}"
        )


# =======================
# Endpoint: Ventes par produit
# =======================

@router.get("/by-product", response_model=None)
@router.get("/by-product/{product_id}", response_model=None)
async def get_sales_by_product(
    product_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    # Nouveaux paramètres (pour l'onglet Par Produit)
    branch_id: Optional[UUID] = Query(None, description="ID de la branche (nouveau mode)"),
    start_date: Optional[datetime] = Query(None, description="Date de début (ISO format)"),
    end_date: Optional[datetime] = Query(None, description="Date de fin (ISO format)"),
    # Anciens paramètres (pour compatibilité)
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie (ancien mode)"),
    start_date_only: Optional[date] = Query(None, description="Date de début (ancien mode)"),
    end_date_only: Optional[date] = Query(None, description="Date de fin (ancien mode)"),
    limit: int = Query(100, ge=1, le=1000, description="Limite de résultats (ancien mode)")
):
    """
    Récupère les ventes par produit.
    
    DEUX MODES D'UTILISATION:
    
    1. MODE NOUVEAU (groupé par produit pour tableau de bord):
       - Utiliser branch_id + start_date + end_date (datetime)
       - Retourne les données groupées par produit avec détails par vendeur
       - Exemple: GET /sales/by-product?branch_id=xxx&start_date=2024-01-01T00:00:00&end_date=2024-12-31T23:59:59
    
    2. MODE ANCIEN (ventes détaillées pour un produit spécifique):
       - Utiliser product_id dans l'URL + pharmacy_id + start_date_only/end_date_only (date)
       - Retourne la liste des ventes pour un produit spécifique
       - Exemple: GET /sales/by-product/123e4567-e89b-12d3-a456-426614174000
    """
    tenant_id = current_tenant.id if current_tenant else None
    
    # ========================
    # MODE NOUVEAU: Groupé par produit (pour l'onglet Par Produit)
    # ========================
    if branch_id and start_date and end_date:
        try:
            # Vérifier l'accès à la branche
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.is_active == True
            ).first()
            
            if not branch:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Branche non trouvée"
                )
            
            # Si un product_id est spécifié, on filtre sur ce produit
            # (permet d'avoir le détail groupé pour un seul produit)
            filter_product_id = product_id  # product_id peut venir de l'URL ou du query param
            
            # Construire la requête de base
            query = db.query(
                SaleItem.product_id,
                Product.code.label("product_code"),
                Product.name.label("product_name"),
                func.sum(SaleItem.quantity).label("total_quantity"),
                func.sum(SaleItem.total).label("total_revenue"),
                func.count(distinct(Sale.id)).label("sale_count"),
                func.avg(SaleItem.unit_price).label("average_price"),
                func.max(Sale.created_at).label("last_sale_date")
            ).join(
                Sale, Sale.id == SaleItem.sale_id
            ).join(
                Product, Product.id == SaleItem.product_id
            ).filter(
                Sale.branch_id == branch_id,
                Sale.status == "completed",
                Sale.created_at >= start_date,
                Sale.created_at <= end_date
            )
            
            if tenant_id:
                query = query.filter(Sale.tenant_id == tenant_id)
            
            if filter_product_id:
                query = query.filter(SaleItem.product_id == filter_product_id)
            
            # Grouper par produit
            results = query.group_by(
                SaleItem.product_id,
                Product.code,
                Product.name
            ).order_by(
                desc("total_quantity")
            ).all()
            
            # Si un produit spécifique est demandé et qu'aucun résultat n'est trouvé
            if filter_product_id and not results:
                # Vérifier que le produit existe
                product = db.query(Product).filter(Product.id == filter_product_id).first()
                if product:
                    return [{
                        "productId": str(product.id),
                        "productCode": product.code,
                        "productName": product.name,
                        "totalQuantity": 0,
                        "totalRevenue": 0,
                        "saleCount": 0,
                        "averagePrice": 0,
                        "lastSaleDate": None,
                        "sellers": []
                    }]
            
            # Pour chaque produit, récupérer les détails par vendeur
            sales_by_product = []
            
            for row in results:
                # Récupérer les ventes par vendeur pour ce produit
                seller_query = db.query(
                    User.id.label("user_id"),
                    User.nom_complet.label("user_name"),
                    func.sum(SaleItem.quantity).label("quantity"),
                    func.sum(SaleItem.total).label("revenue"),
                    func.count(distinct(Sale.id)).label("sale_count")
                ).join(
                    Sale, Sale.id == SaleItem.sale_id
                ).join(
                    User, User.id == Sale.created_by
                ).filter(
                    SaleItem.product_id == row.product_id,
                    Sale.branch_id == branch_id,
                    Sale.status == "completed",
                    Sale.created_at >= start_date,
                    Sale.created_at <= end_date
                ).group_by(
                    User.id, User.nom_complet
                ).all()
                
                # Récupérer les détails individuels des ventes
                sales_detail = db.query(
                    Sale.id,
                    Sale.reference,
                    Sale.created_at,
                    Sale.customer_name,
                    User.nom_complet.label("seller_name"),
                    SaleItem.quantity,
                    SaleItem.unit_price,
                    SaleItem.total.label("amount")
                ).join(
                    Sale, Sale.id == SaleItem.sale_id
                ).join(
                    User, User.id == Sale.created_by
                ).filter(
                    SaleItem.product_id == row.product_id,
                    Sale.branch_id == branch_id,
                    Sale.status == "completed",
                    Sale.created_at >= start_date,
                    Sale.created_at <= end_date
                ).order_by(
                    desc(Sale.created_at)
                ).all()
                
                sellers_data = []
                for seller in seller_query:
                    seller_sales = [
                        {
                            "id": str(sale.id),
                            "reference": sale.reference,
                            "created_at": sale.created_at.isoformat(),
                            "customer_name": sale.customer_name,
                            "seller_name": sale.seller_name,
                            "quantity": int(sale.quantity),
                            "unit_price": float(sale.unit_price),
                            "amount": float(sale.amount)
                        }
                        for sale in sales_detail
                        if sale.seller_name == seller.user_name
                    ]
                    
                    sellers_data.append({
                        "userId": str(seller.user_id),
                        "userName": seller.user_name,
                        "quantity": int(seller.quantity),
                        "revenue": float(seller.revenue),
                        "saleCount": seller.sale_count,
                        "sales": seller_sales
                    })
                
                sales_by_product.append({
                    "productId": str(row.product_id),
                    "productCode": row.product_code,
                    "productName": row.product_name,
                    "totalQuantity": int(row.total_quantity),
                    "totalRevenue": float(row.total_revenue),
                    "saleCount": row.sale_count,
                    "averagePrice": float(row.average_price or 0),
                    "lastSaleDate": row.last_sale_date.isoformat() if row.last_sale_date else None,
                    "sellers": sellers_data
                })
            
            return sales_by_product
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Erreur récupération ventes groupées par produit: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erreur récupération des données: {str(e)}"
            )
    
    # ========================
    # MODE ANCIEN: Ventes détaillées pour un produit spécifique (avec product_id dans l'URL)
    # ========================
    elif product_id:
        try:
            # Vérifier que le produit existe
            product_query = db.query(Product).filter(Product.id == product_id)
            if tenant_id:
                product_query = product_query.filter(Product.tenant_id == tenant_id)
            
            product = product_query.first()
            
            if not product:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Produit non trouvé"
                )
            
            # Déterminer les pharmacies accessibles
            if current_user.role in ["super_admin", "superadmin", "admin"]:
                if pharmacy_id:
                    pharmacies = [pharmacy_id]
                else:
                    pharmacies_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
                    if tenant_id:
                        pharmacies_query = pharmacies_query.filter(Pharmacy.tenant_id == tenant_id)
                    pharmacies = [p.id for p in pharmacies_query.all()]
            else:
                accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
                if pharmacy_id and pharmacy_id not in accessible_pharmacies:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Accès non autorisé à cette pharmacie"
                    )
                pharmacies = [pharmacy_id] if pharmacy_id else accessible_pharmacies
            
            if not pharmacies:
                return {
                    "product_id": str(product_id),
                    "product_name": product.name,
                    "product_code": product.code,
                    "sales": [],
                    "total_sales": 0,
                    "total_quantity": 0,
                    "total_revenue": 0
                }
            
            # Construire la requête
            query = db.query(
                Sale.id,
                Sale.reference,
                Sale.created_at,
                Sale.client_name,
                Sale.seller_name,
                SaleItem.quantity,
                SaleItem.unit_price,
                SaleItem.total,
                Sale.pharmacy_id
            ).join(
                SaleItem, SaleItem.sale_id == Sale.id
            ).filter(
                SaleItem.product_id == product_id,
                Sale.status == "completed",
                Sale.pharmacy_id.in_(pharmacies)
            )
            
            if tenant_id:
                query = query.filter(Sale.tenant_id == tenant_id)
                query = query.filter(SaleItem.tenant_id == tenant_id)
            
            # Utiliser start_date_only et end_date_only (anciens paramètres)
            if start_date_only:
                query = query.filter(func.date(Sale.created_at) >= start_date_only)
            if end_date_only:
                query = query.filter(func.date(Sale.created_at) <= end_date_only)
            
            results = query.order_by(desc(Sale.created_at)).limit(limit).all()
            
            total_quantity = sum(r.quantity for r in results)
            total_revenue = sum(float(r.total) for r in results)
            
            return {
                "product_id": str(product_id),
                "product_name": product.name,
                "product_code": product.code,
                "sales": [
                    {
                        "sale_id": str(r.id),
                        "reference": r.reference,
                        "date": r.created_at.isoformat() if r.created_at else None,
                        "client_name": r.client_name,
                        "seller_name": r.seller_name,
                        "quantity": int(r.quantity),
                        "unit_price": float(r.unit_price),
                        "total": float(r.total),
                        "pharmacy_id": str(r.pharmacy_id) if r.pharmacy_id else None
                    }
                    for r in results
                ],
                "total_sales": len(results),
                "total_quantity": int(total_quantity),
                "total_revenue": float(total_revenue)
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Erreur récupération ventes pour produit {product_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erreur récupération ventes: {str(e)}"
            )
    
    # ========================
    # AUCUN MODE DÉTECTÉ
    # ========================
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Paramètres invalides. Utilisez soit:\n"
                   "- branch_id + start_date + end_date (mode groupé)\n"
                   "- product_id dans l'URL (mode détaillé)"
        )

# =======================
# Endpoint: Statistiques quotidiennes
# =======================

@router.get("/stats/daily", response_model=DailyStatsResponse)
async def get_daily_stats_endpoint(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    target_date: Optional[date] = Query(None, description="Date (format YYYY-MM-DD)"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Récupère les statistiques de ventes pour une journée spécifique.
    Si la date n'est pas fournie, utilise la date du jour.
    """
    try:
        if target_date is None:
            target_date = datetime.now().date()
        
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            if pharmacy_id:
                pharmacies = [pharmacy_id]
            else:
                pharmacies_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
                if tenant_id:
                    pharmacies_query = pharmacies_query.filter(Pharmacy.tenant_id == tenant_id)
                pharmacies = [p.id for p in pharmacies_query.all()]
        else:
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in accessible_pharmacies:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            pharmacies = [pharmacy_id] if pharmacy_id else accessible_pharmacies
        
        if not pharmacies:
            return DailyStatsResponse(
                date=target_date.isoformat(),
                sales_count=0,
                total_amount=0,
                average_basket=0,
                items_sold=0,
                top_products=[],
                by_pharmacy=[]
            )
        
        # Date range pour la journée
        start_datetime = datetime.combine(target_date, datetime.min.time())
        end_datetime = datetime.combine(target_date, datetime.max.time())
        
        # Requête principale
        stats_query = db.query(
            func.count(distinct(Sale.id)).label("sales_count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("total_amount"),
            func.coalesce(func.sum(SaleItem.quantity), 0).label("items_sold"),
            func.avg(Sale.total_amount).label("average_basket")
        ).join(
            SaleItem, SaleItem.sale_id == Sale.id
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacies),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        
        if tenant_id:
            stats_query = stats_query.filter(Sale.tenant_id == tenant_id)
        
        stats_result = stats_query.first()
        
        # Top produits
        top_products_query = db.query(
            Product.id.label("product_id"),
            Product.name.label("product_name"),
            func.sum(SaleItem.quantity).label("total_quantity"),
            func.sum(SaleItem.total).label("total_amount")
        ).join(
            SaleItem, SaleItem.product_id == Product.id
        ).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacies),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        
        if tenant_id:
            top_products_query = top_products_query.filter(Sale.tenant_id == tenant_id)
        
        top_products = top_products_query.group_by(
            Product.id, Product.name
        ).order_by(
            desc("total_quantity")
        ).limit(5).all()
        
        # Par pharmacie
        by_pharmacy_query = db.query(
            Pharmacy.id.label("pharmacy_id"),
            Pharmacy.name.label("pharmacy_name"),
            func.count(distinct(Sale.id)).label("sales_count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("total_amount")
        ).join(
            Sale, Sale.pharmacy_id == Pharmacy.id
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacies),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        
        if tenant_id:
            by_pharmacy_query = by_pharmacy_query.filter(Sale.tenant_id == tenant_id)
        
        by_pharmacy_results = by_pharmacy_query.group_by(
            Pharmacy.id, Pharmacy.name
        ).all()
        
        total_sales = float(stats_result.total_amount or 0)
        by_pharmacy = []
        for pharm in by_pharmacy_results:
            percentage = (float(pharm.total_amount) / total_sales * 100) if total_sales > 0 else 0
            by_pharmacy.append({
                "pharmacy_id": str(pharm.pharmacy_id),
                "pharmacy_name": pharm.pharmacy_name,
                "sales_count": pharm.sales_count,
                "total_amount": float(pharm.total_amount),
                "percentage": percentage
            })
        
        return DailyStatsResponse(
            date=target_date.isoformat(),
            sales_count=stats_result.sales_count or 0,
            total_amount=float(stats_result.total_amount or 0),
            average_basket=float(stats_result.average_basket or 0),
            items_sold=int(stats_result.items_sold or 0),
            top_products=[
                {
                    "product": p.product_name,
                    "quantity": int(p.total_quantity),
                    "amount": float(p.total_amount)
                }
                for p in top_products
            ],
            by_pharmacy=by_pharmacy
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération stats quotidiennes: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération stats: {str(e)}"
        )


# =======================
# Endpoint: Statistiques globales (overview)
# =======================

@router.get("/stats/overview", response_model=SalesStatsResponse)
async def get_sales_stats(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Récupère les statistiques globales des ventes.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        today = datetime.now().date()
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            if pharmacy_id:
                pharmacies = [pharmacy_id]
            else:
                pharmacies_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
                if tenant_id:
                    pharmacies_query = pharmacies_query.filter(Pharmacy.tenant_id == tenant_id)
                pharmacies = [p.id for p in pharmacies_query.all()]
        else:
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in accessible_pharmacies:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            pharmacies = [pharmacy_id] if pharmacy_id else accessible_pharmacies
        
        if not pharmacies:
            empty_period = {"total": 0, "count": 0, "average": 0}
            empty_daily = DailyStatsResponse(
                date=today.isoformat(),
                sales_count=0,
                total_amount=0,
                average_basket=0,
                items_sold=0,
                top_products=[],
                by_pharmacy=[]
            )
            return SalesStatsResponse(
                today=empty_daily,
                week=empty_period,
                month=empty_period,
                year=empty_period
            )
        
        def get_stats_for_period(start: datetime, end: datetime) -> Dict[str, Any]:
            query = db.query(
                func.count(distinct(Sale.id)).label("count"),
                func.coalesce(func.sum(Sale.total_amount), 0).label("total")
            ).filter(
                Sale.status == "completed",
                Sale.pharmacy_id.in_(pharmacies),
                Sale.created_at >= start,
                Sale.created_at <= end
            )
            if tenant_id:
                query = query.filter(Sale.tenant_id == tenant_id)
            result = query.first()
            total = float(result.total or 0)
            count = result.count or 0
            return {
                "total": total,
                "count": count,
                "average": total / count if count > 0 else 0
            }
        
        # Statistiques du jour
        today_stats = await get_daily_stats_endpoint(
            db=db,
            current_tenant=current_tenant,
            current_user=current_user,
            target_date=today,
            pharmacy_id=pharmacy_id
        )
        
        # Statistiques de la semaine
        week_start = datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time())
        week_end = datetime.combine(today, datetime.max.time())
        week_stats = get_stats_for_period(week_start, week_end)
        
        # Statistiques du mois
        month_start = datetime.combine(today.replace(day=1), datetime.min.time())
        month_end = datetime.combine(today, datetime.max.time())
        month_stats = get_stats_for_period(month_start, month_end)
        
        # Statistiques de l'année
        year_start = datetime.combine(today.replace(month=1, day=1), datetime.min.time())
        year_end = datetime.combine(today, datetime.max.time())
        year_stats = get_stats_for_period(year_start, year_end)
        
        return SalesStatsResponse(
            today=today_stats,
            week=week_stats,
            month=month_stats,
            year=year_stats
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération stats globales: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération stats: {str(e)}"
        )

# =======================
# Endpoint: Liste des ventes (avec filtres et pagination)
# =======================

@router.get("/", response_model=SaleListResponse)
async def get_sales(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    # Pagination
    skip: int = Query(0, ge=0, description="Nombre d'éléments à sauter"),
    limit: int = Query(100, ge=1, le=1000, description="Nombre d'éléments par page"),
    # Filtres
    start_date: Optional[datetime] = Query(None, description="Date de début"),
    end_date: Optional[datetime] = Query(None, description="Date de fin"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par branche"),  # ← AJOUTER CETTE LIGNE
    user_id: Optional[UUID] = Query(None, description="Filtrer par utilisateur (caissier)"),
    payment_method: Optional[str] = Query(None, description="Filtrer par méthode de paiement"),
    status: Optional[str] = Query(None, description="Filtrer par statut (completed, pending, cancelled)"),
    customer_id: Optional[UUID] = Query(None, description="Filtrer par client"),
    search: Optional[str] = Query(None, description="Recherche par référence, client, vendeur"),
    # Tri
    sort_by: str = Query("created_at", description="Champ de tri (created_at, total_amount, reference)"),
    sort_order: str = Query("desc", description="Ordre de tri (asc, desc)"),
):
    """
    Récupère la liste des ventes avec filtres et pagination.
    Accessible aux administrateurs et vendeurs (limité à leurs pharmacies).
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Convertir branch_id en pharmacy_id si nécessaire
        actual_pharmacy_id = pharmacy_id
        if branch_id and not pharmacy_id:
            # Récupérer la pharmacie parente de la branche
            branch = db.query(Branch).filter(Branch.id == branch_id).first()
            if branch:
                actual_pharmacy_id = branch.parent_pharmacy_id
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin", "gerant"]:
            if actual_pharmacy_id:
                # Vérifier que la pharmacie existe
                pharmacy_check = db.query(Pharmacy).filter(Pharmacy.id == actual_pharmacy_id).first()
                if not pharmacy_check:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Pharmacie non trouvée"
                    )
                pharmacy_ids = [actual_pharmacy_id]
            else:
                pharmacies_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
                if tenant_id:
                    pharmacies_query = pharmacies_query.filter(Pharmacy.tenant_id == tenant_id)
                pharmacy_ids = [p.id for p in pharmacies_query.all()]
        else:
            # Vendeur, caissier - uniquement ses pharmacies
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if actual_pharmacy_id:
                if actual_pharmacy_id not in accessible_pharmacies:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Accès non autorisé à cette pharmacie"
                    )
                pharmacy_ids = [actual_pharmacy_id]
            else:
                pharmacy_ids = accessible_pharmacies
        
        if not pharmacy_ids:
            return SaleListResponse(
                items=[],
                total=0,
                page=skip // limit + 1 if limit > 0 else 1,
                size=0,
                has_more=False,
                page_size=limit
            )
        
        # Construction de la requête de base
        query = db.query(Sale).filter(
            Sale.pharmacy_id.in_(pharmacy_ids),
            Sale.status != "deleted"  # Exclure les ventes supprimées
        )
        
        if tenant_id:
            query = query.filter(Sale.tenant_id == tenant_id)
        
        # Filtres
        if start_date:
            query = query.filter(Sale.created_at >= start_date)
        if end_date:
            query = query.filter(Sale.created_at <= end_date)
        
        if user_id:
            query = query.filter(Sale.created_by == user_id)
        
        if payment_method:
            query = query.filter(Sale.payment_method == payment_method)
        
        if status:
            query = query.filter(Sale.status == status)
        
        if customer_id:
            query = query.filter(Sale.customer_id == customer_id)
        
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Sale.reference.ilike(search_term),
                    Sale.client_name.ilike(search_term),
                    Sale.seller_name.ilike(search_term),
                    Sale.invoice_number.ilike(search_term)
                )
            )
        
        # Compter le total avant pagination
        total = query.count()
        
        # Appliquer le tri
        sort_column = getattr(Sale, sort_by, Sale.created_at)
        if sort_order.lower() == "desc":
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(asc(sort_column))
        
        # Pagination
        sales = query.offset(skip).limit(limit).all()
        
        # Construire la réponse
        sales_data = []
        for sale in sales:
            # Récupérer les items de la vente
            items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
            
            # Récupérer le nom de la pharmacie
            pharmacy = db.query(Pharmacy).filter(Pharmacy.id == sale.pharmacy_id).first()
            
            sales_data.append(SaleInDB(
                id=sale.id,
                tenant_id=sale.tenant_id,
                pharmacy_id=sale.pharmacy_id,
                pharmacy_name=pharmacy.name if pharmacy else None,
                reference=sale.reference,
                customer_id=sale.customer_id,
                customer_name=sale.customer_name,
                customer_phone=sale.customer_phone,
                created_by=sale.created_by,
                seller_name=sale.seller_name,
                created_at=sale.created_at,
                updated_at=sale.updated_at,
                payment_method=sale.payment_method,
                reference_payment=sale.reference_payment,
                payment_date=sale.payment_date,
                is_credit=sale.is_credit,
                credit_due_date=sale.credit_due_date,
                guarantee_deposit=sale.guarantee_deposit,
                guarantor_name=sale.guarantor_name,
                guarantor_phone=sale.guarantor_phone,
                global_discount=sale.global_discount,
                notes=sale.notes,
                subtotal=float(sale.subtotal) if sale.subtotal else 0,
                total_discount=float(sale.total_discount) if sale.total_discount else 0,
                total_tva=float(sale.total_tva) if sale.total_tva else 0,
                total_amount=float(sale.total_amount) if sale.total_amount else 0,
                status=sale.status,
                validated_by=sale.validated_by,
                validated_at=sale.validated_at,
                cancelled_at=sale.cancelled_at,
                cancelled_by=sale.cancelled_by,
                cancel_reason=sale.cancel_reason,
                invoice_number=sale.invoice_number,
                receipt_path=sale.receipt_path,
                invoice_path=getattr(sale, 'invoice_path', None),
                items=[
                    SaleItemResponse(
                        id=item.id,
                        sale_id=item.sale_id,
                        tenant_id=item.tenant_id,
                        pharmacy_id=item.pharmacy_id,
                        created_at=item.created_at,
                        product_id=item.product_id,
                        product_name=item.product_name,
                        product_code=item.product_code,
                        quantity=float(item.quantity),
                        unit_price=float(item.unit_price),
                        discount_percent=float(item.discount_percent) if item.discount_percent else 0,
                        discount_amount=float(item.discount_amount) if item.discount_amount else 0,
                        tva_rate=float(item.tva_rate) if item.tva_rate else 0,
                        tva_amount=float(item.tva_amount) if item.tva_amount else 0,
                        subtotal=float(item.subtotal) if item.subtotal else 0,
                        total=float(item.total) if item.total else 0,
                        batch_number=item.batch_number,
                        expiry_date=item.expiry_date
                    )
                    for item in items
                ]
            ))
        
        return SaleListResponse(
            items=sales_data,
            total=total,
            page=skip // limit + 1 if limit > 0 else 1,
            size=len(sales_data),
            has_more=(skip + limit) < total,
            page_size=limit
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération liste des ventes: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération des ventes: {str(e)}"
        )

@router.get("/{sale_id}", response_model=SaleDetailResponse)
async def get_sale_by_id(
    sale_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """
    Récupère les détails d'une vente spécifique par son ID.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Récupérer la vente
        query = db.query(Sale).filter(Sale.id == sale_id)
        if tenant_id:
            query = query.filter(Sale.tenant_id == tenant_id)
        
        sale = query.first()
        
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vente non trouvée"
            )
        
        # Vérifier l'accès à la pharmacie
        if current_user.role not in ["super_admin", "superadmin", "admin", "gerant"]:
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if sale.pharmacy_id not in accessible_pharmacies:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette vente"
                )
        
        # Récupérer les items
        items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
        
        # Récupérer la pharmacie
        pharmacy = db.query(Pharmacy).filter(Pharmacy.id == sale.pharmacy_id).first()
        
        # Récupérer la branche si présente
        branch = None
        if sale.branch_id:
            branch = db.query(Branch).filter(Branch.id == sale.branch_id).first()
        
        return SaleDetailResponse(
            id=sale.id,
            tenant_id=sale.tenant_id,
            pharmacy_id=sale.pharmacy_id,
            pharmacy_name=pharmacy.name if pharmacy else None,
            branch_id=sale.branch_id,
            branch_name=branch.name if branch else None,
            reference=sale.reference,
            customer_id=sale.customer_id,
            customer_name=sale.customer_name,
            customer_phone=sale.customer_phone,
            created_by=sale.created_by,
            seller_name=sale.seller_name,
            created_at=sale.created_at,
            updated_at=sale.updated_at,
            payment_method=sale.payment_method,
            reference_payment=sale.reference_payment,
            payment_date=sale.payment_date,
            is_credit=sale.is_credit,
            credit_due_date=sale.credit_due_date,
            guarantee_deposit=float(sale.guarantee_deposit) if sale.guarantee_deposit else 0,
            guarantor_name=sale.guarantor_name,
            guarantor_phone=sale.guarantor_phone,
            global_discount=float(sale.global_discount) if sale.global_discount else 0,
            notes=sale.notes,
            subtotal=float(sale.subtotal) if sale.subtotal else 0,
            total_discount=float(sale.total_discount) if sale.total_discount else 0,
            total_tva=float(sale.total_tva) if sale.total_tva else 0,
            total_amount=float(sale.total_amount) if sale.total_amount else 0,
            status=sale.status,
            validated_by=sale.validated_by,
            validated_at=sale.validated_at,
            cancelled_at=sale.cancelled_at,
            cancelled_by=sale.cancelled_by,
            cancel_reason=sale.cancel_reason,
            invoice_number=sale.invoice_number,
            receipt_path=sale.receipt_path,
            invoice_path=getattr(sale, 'invoice_path', None),
            items=[
                SaleItemResponse(
                    id=item.id,
                    sale_id=item.sale_id,
                    tenant_id=item.tenant_id,
                    pharmacy_id=item.pharmacy_id,
                    created_at=item.created_at,
                    product_id=item.product_id,
                    product_name=item.product_name,
                    product_code=item.product_code,
                    quantity=float(item.quantity),
                    unit_price=float(item.unit_price),
                    discount_percent=float(item.discount_percent) if item.discount_percent else 0,
                    discount_amount=float(item.discount_amount) if item.discount_amount else 0,
                    tva_rate=float(item.tva_rate) if item.tva_rate else 0,
                    tva_amount=float(item.tva_amount) if item.tva_amount else 0,
                    subtotal=float(item.subtotal) if item.subtotal else 0,
                    total=float(item.total) if item.total else 0,
                    batch_number=item.batch_number,
                    expiry_date=item.expiry_date
                )
                for item in items
            ]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération vente {sale_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération de la vente: {str(e)}"
        )

@router.get("/available-hours")
async def get_available_hours(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None, description="ID de la branche"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
):
    """
    Récupère les heures où des ventes ont été effectuées.
    Utile pour le filtre par heure.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        query = db.query(
            func.extract('hour', Sale.created_at).label("hour"),
            func.extract('minute', Sale.created_at).label("minute"),
            func.count(Sale.id).label("count")
        ).filter(
            Sale.tenant_id == tenant_id,
            Sale.status == "completed"
        )
        
        if branch_id:
            query = query.filter(Sale.branch_id == branch_id)
        
        if start_date:
            query = query.filter(func.date(Sale.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(Sale.created_at) <= end_date)
        
        results = query.group_by("hour", "minute").order_by("hour", "minute").all()
        
        hours = {}
        for r in results:
            hour_str = f"{int(r.hour):02d}:{int(r.minute):02d}"
            hours[hour_str] = r.count
        
        return {
            "available_hours": list(hours.keys()),
            "hours_with_counts": hours
        }
        
    except Exception as e:
        logger.error(f"Erreur récupération heures disponibles: {str(e)}")
        return {"available_hours": [], "hours_with_counts": {}}
    
@property
def nom_complet(self):
    return self.full_name
# =======================
# Endpoint: Statistiques par période
# =======================

@router.get("/stats/period", response_model=PeriodStatsResponse)
async def get_period_stats(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    period: str = Query("day", description="Période: day, week, month, year"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Récupère les statistiques agrégées par période.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        today = datetime.now().date()
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            if pharmacy_id:
                pharmacies = [pharmacy_id]
            else:
                pharmacies_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
                if tenant_id:
                    pharmacies_query = pharmacies_query.filter(Pharmacy.tenant_id == tenant_id)
                pharmacies = [p.id for p in pharmacies_query.all()]
        else:
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in accessible_pharmacies:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            pharmacies = [pharmacy_id] if pharmacy_id else accessible_pharmacies
        
        if not pharmacies:
            return PeriodStatsResponse(
                period=period,
                start_date=datetime.now().isoformat(),
                end_date=datetime.now().isoformat(),
                data=[]
            )
        
        # Déterminer le nombre de jours à retourner
        if period == "day":
            days = 1
            group_by = func.date(Sale.created_at)
        elif period == "week":
            days = 7
            group_by = func.date_trunc('week', Sale.created_at)
        elif period == "month":
            days = 30
            group_by = func.date_trunc('month', Sale.created_at)
        elif period == "year":
            days = 365
            group_by = func.date_trunc('year', Sale.created_at)
        else:
            days = 7
            group_by = func.date(Sale.created_at)
        
        start_date = datetime.combine(today - timedelta(days=days), datetime.min.time())
        
        # Requête
        results = db.query(
            group_by.label("date"),
            func.count(distinct(Sale.id)).label("sales_count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("total_amount"),
            func.avg(Sale.total_amount).label("average_basket")
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacies),
            Sale.created_at >= start_date
        )
        
        if tenant_id:
            results = results.filter(Sale.tenant_id == tenant_id)
        
        results = results.group_by("date").order_by("date").all()
        
        return PeriodStatsResponse(
            period=period,
            start_date=start_date.isoformat(),
            end_date=datetime.now().isoformat(),
            data=[
                {
                    "date": r.date.isoformat() if r.date else None,
                    "sales_count": r.sales_count,
                    "total_amount": float(r.total_amount),
                    "average_basket": float(r.average_basket or 0)
                }
                for r in results
            ]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération stats par période: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération stats: {str(e)}"
        )


# =======================
# Endpoint de test
# =======================

@router.get("/test", include_in_schema=False)
async def test_sales(
    current_user: User = Depends(get_current_active_user)
):
    """
    Endpoint de test
    """
    return {
        "message": "Module Ventes avec Pharmacies opérationnel",
        "version": "3.1.0",
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role
        },
        "features": [
            "Gestion complète des ventes par pharmacie",
            "Multi-pharmacies pour les admin",
            "Contrôle d'accès par pharmacie",
            "Statistiques par pharmacie",
            "Gestion des stocks par pharmacie",
            "Communication bidirectionnelle avec le module stock",
            "Traçabilité complète des mouvements de stock"
        ],
        "timestamp": datetime.utcnow().isoformat()
    }

@router.get("/service-status")
async def get_service_status(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """
    Vérifie le statut du service de vente
    """
    return {
        "status": "operational",
        "service": "sales",
        "version": "3.1.0",
        "timestamp": datetime.utcnow().isoformat(),
        "features": [
            "sales_management",
            "credit_sales",
            "receipt_generation",
            "statistics"
        ]
    }

@router.get("/health")
async def sales_health_check(
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """
    Endpoint de health check pour le service de ventes.
    Utilisé par le frontend pour vérifier la disponibilité du service.
    """
    return {
        "status": "healthy",
        "service": "sales",
        "timestamp": datetime.utcnow().isoformat(),
        "endpoints_available": [
            "/api/v1/sales",
            "/api/v1/sales/stats/daily",
            "/api/v1/sales/stats/overview",
            "/api/v1/sales/next-invoice-number"
        ]
    }


@router.get("/ping")
async def sales_ping():
    """
    Endpoint simple pour vérifier que le service est accessible.
    """
    return {
        "status": "ok",
        "service": "sales",
        "timestamp": datetime.utcnow().isoformat()
    }

@router.get("/sellers")
async def get_sellers(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    role: Optional[str] = Query(None, description="Filtrer par rôle (vendeur, caissier, gerant)")
):
    """
    Récupère la liste des vendeurs/caissiers ayant accès aux pharmacies.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Base query - récupérer les utilisateurs qui ont des rôles de vente
        allowed_roles = ["vendeur", "caissier", "gerant", "admin", "superadmin", "super_admin"]
        
        # Si un rôle spécifique est demandé
        if role and role.lower() in allowed_roles:
            allowed_roles = [role.lower()]
        
        # Récupérer les utilisateurs actifs avec les rôles appropriés
        query = db.query(User).filter(
            User.is_active == True,
            User.role.in_(allowed_roles)
        )
        
        if tenant_id:
            query = query.filter(User.tenant_id == tenant_id)
        
        # Si une pharmacie est spécifiée, filtrer par accès
        if pharmacy_id:
            # Récupérer les utilisateurs qui ont accès à cette pharmacie
            user_pharmacy_query = db.query(UserPharmacy.user_id).filter(
                UserPharmacy.pharmacy_id == pharmacy_id
            )
            query = query.filter(User.id.in_(user_pharmacy_query))
        
        users = query.order_by(User.nom_complet, User.email).all()
        
        return {
            "success": True,
            "sellers": [
                {
                    "id": str(user.id),
                    "email": user.email,
                    "nom_complet": getattr(user, 'nom_complet', user.email),
                    "role": user.role,
                    "is_active": user.is_active
                }
                for user in users
            ],
            "total": len(users)
        }
        
    except Exception as e:
        logger.error(f"Erreur récupération vendeurs: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération vendeurs: {str(e)}"
        )


@router.get("/sellers/{pharmacy_id}")
async def get_sellers_by_pharmacy(
    pharmacy_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """
    Récupère la liste des vendeurs pour une pharmacie spécifique.
    """
    return await get_sellers(
        db=db,
        current_tenant=current_tenant,
        current_user=current_user,
        pharmacy_id=pharmacy_id
    )

@router.post("/refund", status_code=status.HTTP_200_OK)
async def refund_sale(
    refund_data: dict,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    Annule une vente et rembourse le client.
    RESTAURE LE STOCK AUTOMATIQUEMENT.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Vérifier les permissions
        if current_user.role.lower() not in ["super_admin", "superadmin", "admin", "gerant"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Seuls les administrateurs peuvent annuler des ventes"
            )
        
        sale_id = refund_data.get("sale_id")
        if not sale_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="sale_id requis"
            )
        
        # Récupérer la vente
        sale = db.query(Sale).filter(
            Sale.id == sale_id,
            Sale.tenant_id == tenant_id
        ).first()
        
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vente non trouvée"
            )
        
        if sale.status == "cancelled":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cette vente est déjà annulée"
            )

        # Une vente annulée/retournée est SUPPRIMÉE : on crée le retour (seule trace
        # conservée), on réintègre le stock puis on supprime la vente et ses dépendances.
        from app.services.sale_deletion_service import (
            cancel_sale_with_return, SaleDeletionError
        )
        invoice_number = sale.invoice_number
        sale_ref = sale.reference
        sale_uuid = sale.id
        try:
            result = cancel_sale_with_return(
                db, sale, current_user,
                reason_text=refund_data.get("reason") or "Annulation par administrateur",
                restore_stock=refund_data.get("restore_stock", True),
            )
        except SaleDeletionError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

        db.commit()
        
        logger.info(f"Vente annulée et supprimée: {sale_ref} par {current_user.email}")
        
        return {
            "success": True,
            "message": f"Vente {invoice_number} annulée et supprimée avec succès",
            "sale_id": str(sale_uuid),
            "sale_deleted": result["sale_deleted"],
            "return_id": result["return_id"],
            "return_number": result["return_number"],
            "restored_items": result["restored_count"]
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur annulation vente: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur annulation: {str(e)}"
        )

@router.get("/test-invoices")
async def test_invoices_endpoint(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None, description="ID de la branche"),
    start_date: Optional[str] = Query(None, description="Date de début YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="Date de fin YYYY-MM-DD"),
):
    """
    Endpoint de test pour vérifier que le client peut récupérer les factures.
    Point d'entrée temporaire pour debug.
    """
    tenant_id = current_tenant.id if current_tenant else None
    
    # Construire la requête
    query = db.query(Sale).filter(Sale.tenant_id == tenant_id, Sale.status == "completed")
    
    if branch_id:
        query = query.filter(Sale.branch_id == branch_id)
    
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.filter(Sale.created_at >= start_dt)
        except:
            pass
    
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            end_dt = end_dt.replace(hour=23, minute=59, second=59)
            query = query.filter(Sale.created_at <= end_dt)
        except:
            pass
    
    sales = query.order_by(desc(Sale.created_at)).limit(100).all()
    
    # Format simplifié pour test
    result = []
    for sale in sales:
        result.append({
            "id": str(sale.id),
            "invoice_number": sale.invoice_number,
            "reference": sale.reference,
            "customer_name": sale.customer_name,
            "total_amount": float(sale.total_amount) if sale.total_amount else 0,
            "created_at": sale.created_at.isoformat() if sale.created_at else None,
            "payment_method": sale.payment_method,
            "status": sale.status,
        })
    
    return {
        "success": True,
        "count": len(result),
        "sales": result,
        "message": "Utilisez /sales pour les données complètes"
    }
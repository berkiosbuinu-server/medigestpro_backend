# app/services/sync_service.py - Service de synchronisation complet avec gestion des branches

from sqlalchemy.orm import Session, joinedload
from typing import List, Dict, Any, Optional
from datetime import datetime, date, timedelta
from decimal import Decimal
from uuid import UUID
import logging

logger = logging.getLogger(__name__)


# ==========================================================
# FONCTIONS PRINCIPALES DE SYNCHRONISATION
# ==========================================================

def process_sync(
    db: Session,
    tenant_id: UUID,
    items: List[Any],
    user_branch_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    is_super_admin: bool = False,
) -> Dict[str, Any]:
    """
    Traite les données de synchronisation envoyées par un client mobile.
    Supporte: products, categories, customers, sales, debts, users, branches, 
              pharmacies, subscriptions, stock_movements, expenses, returns
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        items: Liste des items à synchroniser
        user_branch_id: ID de la branche de l'utilisateur (pour validation des droits)
        user_id: ID de l'utilisateur qui synchronise
        is_super_admin: Si True, ignore les restrictions de branche
        
    Returns:
        Dict contenant le statut, le nombre d'items traités et les erreurs
    """
    processed = 0
    errors = []
    permission_errors = []
    results_by_table = {}
    
    # Tables qui nécessitent une vérification de branche
    tables_requiring_branch_check = {
        'products', 'produits', 'produit',
        'sales', 'ventes', 'vente', 'orders', 'commandes', 'commande',
        'debts', 'dettes', 'dette',
        'customers', 'clients', 'client',
        'stock_movements', 'mouvements_stock',
        'expenses', 'depenses', 'depense',
        'returns', 'retours', 'retour'
    }
    
    # Tables où la branche est implicite (ex: branches elle-même)
    tables_with_implicit_branch = {'branches', 'succursales', 'succursale'}
    
    # Table mapping pour normalisation
    table_mapping = {
        'produits': 'products', 'produit': 'products',
        'catégories': 'categories', 'categorie': 'categories', 'categories': 'categories',
        'commandes': 'sales', 'commande': 'sales',
        'clients': 'customers', 'client': 'customers',
        'factures': 'invoices', 'facture': 'invoices',
        'utilisateurs': 'users', 'utilisateur': 'users',
        'tenants': 'tenants', 'tenant': 'tenants',
        'subscriptions': 'branch_subscriptions', 'abonnements': 'branch_subscriptions',
        'abonnement': 'branch_subscriptions', 'branch_subscriptions': 'branch_subscriptions',
        'ventes': 'sales', 'vente': 'sales', 'sales': 'sales',
        'dettes': 'debts', 'dette': 'debts', 'debts': 'debts',
        'retours': 'returns', 'retour': 'returns', 'returns': 'returns',
        'branches': 'branches', 'succursales': 'branches', 'succursale': 'branches',
        'pharmacies': 'pharmacies', 'pharmacie': 'pharmacies',
        'mouvements_stock': 'stock_movements', 'mouvement_stock': 'stock_movements',
        'paiements_dette': 'debt_payments', 'paiement_dette': 'debt_payments',
        'depenses': 'expenses', 'depense': 'expenses', 'expenses': 'expenses',
        'charges': 'expenses', 'charge': 'expenses',
    }
    
    for item in items:
        # Gestion des objets et dictionnaires
        if hasattr(item, 'table_name'):
            raw_table_name = item.table_name
            action = item.action
            data = item.data if hasattr(item, 'data') else {}
            original_item = item
        else:
            raw_table_name = item.get("table_name")
            action = item.get("action")
            data = item.get("data", {})
            original_item = item
        
        # Normaliser le nom de la table
        table_name = table_mapping.get(raw_table_name.lower(), raw_table_name.lower())
        
        # Validation minimale
        if not table_name or not action:
            errors.append({
                "item": original_item,
                "error": "table_name ou action manquant",
            })
            continue
        
        # VÉRIFICATION DES DROITS D'ACCÈS À LA BRANCHE
        if not is_super_admin and user_branch_id:
            table_normalized = table_name.lower()
            
            # Vérification pour les tables qui nécessitent un branch_id
            if table_normalized in tables_requiring_branch_check:
                data_branch_id = data.get('branch_id')
                
                # Si pas de branch_id dans les données, essayer de le trouver via d'autres champs
                if not data_branch_id and table_normalized in ['customers', 'clients', 'client']:
                    data_branch_id = data.get('branch_id') or data.get('default_branch_id')
                
                if data_branch_id:
                    if str(data_branch_id) != str(user_branch_id):
                        permission_errors.append({
                            "table": table_name,
                            "action": action,
                            "error": f"Accès non autorisé: cette donnée appartient à la branche {data_branch_id} mais l'utilisateur est sur la branche {user_branch_id}",
                            "data_id": data.get('id'),
                            "user_branch": str(user_branch_id),
                            "data_branch": str(data_branch_id)
                        })
                        continue
                elif table_normalized in ['products', 'produits'] and not data_branch_id:
                    pharmacy_id = data.get('pharmacy_id')
                    if pharmacy_id:
                        from app.models.pharmacy import Pharmacy
                        from app.models.branch import Branch
                        pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
                        if pharmacy and pharmacy.tenant_id == tenant_id:
                            branch = db.query(Branch).filter(
                                Branch.parent_pharmacy_id == pharmacy.id,
                                Branch.tenant_id == tenant_id,
                                Branch.id == user_branch_id
                            ).first()
                            if not branch:
                                permission_errors.append({
                                    "table": table_name,
                                    "action": action,
                                    "error": f"Accès non autorisé: la pharmacie {pharmacy_id} n'appartient pas à la branche de l'utilisateur",
                                    "data_id": data.get('id'),
                                    "user_branch": str(user_branch_id)
                                })
                                continue
            
            # Vérification pour les tables où la branche est implicite
            elif table_normalized in tables_with_implicit_branch:
                data_id = data.get('id')
                if data_id and str(data_id) != str(user_branch_id):
                    permission_errors.append({
                        "table": table_name,
                        "action": action,
                        "error": f"Accès non autorisé: l'utilisateur ne peut modifier que sa propre branche",
                        "data_id": data_id,
                        "user_branch": str(user_branch_id)
                    })
                    continue
        
        logger.info(f"Traitement de {table_name} - {action} pour tenant {tenant_id} (user_branch={user_branch_id})")
        
        try:
            result = None
            
            # Dispatch vers le handler approprié
            if table_name in ['products', 'produits', 'produit']:
                result = _sync_product(db, tenant_id, action, data, user_branch_id, user_id)
            elif table_name in ['categories', 'categorie']:
                result = _sync_category(db, tenant_id, action, data, user_branch_id, user_id)
            elif table_name in ['customers', 'clients', 'client']:
                result = _sync_customer(db, tenant_id, action, data, user_branch_id, user_id)
            elif table_name in ['sales', 'ventes', 'vente', 'orders', 'commandes', 'commande']:
                result = _sync_sale(db, tenant_id, action, data, user_branch_id, user_id)
            elif table_name in ['debts', 'dettes', 'dette']:
                result = _sync_debt(db, tenant_id, action, data, user_branch_id, user_id)
            elif table_name in ['debt_payments', 'paiements_dette']:
                result = _sync_debt_payment(db, tenant_id, action, data, user_branch_id, user_id)
            elif table_name in ['users', 'utilisateurs', 'utilisateur']:
                result = _sync_user(db, tenant_id, action, data, user_branch_id, user_id)
            elif table_name in ['branches', 'succursales', 'succursale']:
                result = _sync_branch(db, tenant_id, action, data, user_branch_id, user_id, is_super_admin)
            elif table_name in ['pharmacies', 'pharmacie']:
                result = _sync_pharmacy(db, tenant_id, action, data, user_branch_id, user_id)
            elif table_name in ['subscriptions', 'abonnements', 'abonnement', 'branch_subscriptions']:
                result = _sync_subscription(db, tenant_id, action, data, user_branch_id, user_id)
            elif table_name in ['stock_movements', 'mouvements_stock']:
                result = _sync_stock_movement(db, tenant_id, action, data, user_branch_id, user_id)
            elif table_name in ['expenses', 'depenses', 'depense']:
                result = _sync_expense(db, tenant_id, action, data, user_branch_id, user_id)
            elif table_name in ['returns', 'retours', 'retour']:
                result = _sync_return(db, tenant_id, action, data, user_branch_id, user_id)
            else:
                logger.warning(f"Table non supportée: {table_name}")
                errors.append({
                    "table": table_name,
                    "action": action,
                    "error": f"Table non supportée: {table_name}"
                })
                continue
            
            if result and result.get('success'):
                processed += 1
                if table_name not in results_by_table:
                    results_by_table[table_name] = []
                results_by_table[table_name].append(result)
            elif result and not result.get('success'):
                errors.append({
                    "table": table_name,
                    "action": action,
                    "error": result.get('error', 'Erreur inconnue'),
                    "data_id": data.get('id') if data else None
                })
                
        except Exception as e:
            logger.error(f"Erreur lors du traitement de {table_name}: {str(e)}", exc_info=True)
            errors.append({
                "table": table_name,
                "action": action,
                "error": str(e),
                "data_id": data.get('id') if data else None
            })
            db.rollback()
    
    if processed > 0:
        db.commit()
    
    return {
        "status": "success" if not errors and not permission_errors else "partial",
        "processed": processed,
        "total_items": len(items),
        "errors": errors,
        "permission_errors": permission_errors,
        "results_by_table": results_by_table,
        "synced_at": datetime.utcnow().isoformat(),
        "context": {
            "tenant_id": str(tenant_id),
            "user_branch_id": str(user_branch_id) if user_branch_id else None,
            "user_id": str(user_id) if user_id else None,
            "is_super_admin": is_super_admin
        }
    }


def get_changes_since(
    db: Session,
    tenant_id: UUID,
    since: Optional[datetime] = None,
    branch_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    is_super_admin: bool = False
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Récupère tous les changements depuis une date pour toutes les entités.
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        since: Date de dernière synchronisation (optionnel)
        branch_id: ID de la branche (si non super admin)
        user_id: ID de l'utilisateur
        is_super_admin: Si True, récupère toutes les branches
        
    Returns:
        Dictionnaire contenant les changements par table
    """
    changes = {
        'products': [],
        'categories': [],
        'customers': [],
        'sales': [],
        'debts': [],
        'debt_payments': [],
        'users': [],
        'branches': [],
        'pharmacies': [],
        'subscriptions': [],
        'stock_movements': [],
        'expenses': [],
        'returns': []
    }
    
    try:
        # 1. Produits
        from app.models.product import Product
        query = db.query(Product).filter(Product.tenant_id == tenant_id)
        if not is_super_admin and branch_id:
            query = query.filter(Product.branch_id == branch_id)
        if since:
            query = query.filter(
                (Product.updated_at >= since) | (Product.created_at >= since)
            )
        
        for product in query.all():
            changes['products'].append({
                'id': str(product.id),
                'action': 'update' if (product.updated_at and since and product.updated_at >= since) else 'create',
                'data': _serialize_product(product),
                'timestamp': (product.updated_at or product.created_at).isoformat() if (product.updated_at or product.created_at) else None
            })
        
        # 2. Catégories
        from app.models.category import Category
        query = db.query(Category).filter(Category.tenant_id == tenant_id)
        if since:
            query = query.filter(
                (Category.updated_at >= since) | (Category.created_at >= since)
            )
        
        for category in query.all():
            changes['categories'].append({
                'id': str(category.id),
                'action': 'update' if (category.updated_at and since and category.updated_at >= since) else 'create',
                'data': {
                    'id': str(category.id),
                    'name': category.name,
                    'description': category.description,
                    'parent_id': str(category.parent_id) if category.parent_id else None,
                    'is_active': category.is_active
                },
                'timestamp': (category.updated_at or category.created_at).isoformat() if (category.updated_at or category.created_at) else None
            })
        
        # 3. Clients
        from app.models.customer import Customer
        query = db.query(Customer).filter(Customer.tenant_id == tenant_id)
        if not is_super_admin and branch_id:
            query = query.filter(Customer.branch_id == branch_id)
        if since:
            query = query.filter(
                (Customer.updated_at >= since) | (Customer.created_at >= since)
            )
        
        for customer in query.all():
            changes['customers'].append({
                'id': str(customer.id),
                'action': 'update' if (customer.updated_at and since and customer.updated_at >= since) else 'create',
                'data': {
                    'id': str(customer.id),
                    'name': customer.name,
                    'email': customer.email,
                    'phone': customer.phone,
                    'address': customer.address,
                    'city': customer.city,
                    'type': getattr(customer, 'type', 'regular'),
                    'total_debt': float(getattr(customer, 'total_debt', 0)),
                    'total_purchases': float(getattr(customer, 'total_purchases', 0)),
                    'is_active': customer.is_active,
                    'branch_id': str(customer.branch_id) if customer.branch_id else None,
                    'pharmacy_id': str(customer.pharmacy_id) if customer.pharmacy_id else None
                },
                'timestamp': (customer.updated_at or customer.created_at).isoformat() if (customer.updated_at or customer.created_at) else None
            })
        
        # 4. Ventes
        from app.models.sale import Sale, SaleItem
        query = db.query(Sale).filter(Sale.tenant_id == tenant_id)
        if not is_super_admin and branch_id:
            query = query.filter(Sale.branch_id == branch_id)
        if since:
            query = query.filter(
                (Sale.updated_at >= since) | (Sale.created_at >= since)
            )
        
        for sale in query.all():
            changes['sales'].append({
                'id': str(sale.id),
                'action': 'update' if (sale.updated_at and since and sale.updated_at >= since) else 'create',
                'data': {
                    'id': str(sale.id),
                    'reference': sale.reference,
                    'total_amount': float(sale.total_amount),
                    'customer_name': sale.customer_name,
                    'customer_phone': sale.customer_phone,
                    'customer_email': sale.customer_email,
                    'payment_method': sale.payment_method,
                    'is_credit': sale.is_credit,
                    'status': sale.status,
                    'branch_id': str(sale.branch_id) if sale.branch_id else None,
                    'pharmacy_id': str(sale.pharmacy_id) if sale.pharmacy_id else None,
                    'created_by': str(sale.created_by) if sale.created_by else None,
                    'seller_name': sale.seller_name,
                    'global_discount': float(sale.global_discount) if sale.global_discount else 0,
                    'notes': sale.notes,
                    'created_at': sale.created_at.isoformat() if sale.created_at else None,
                    'updated_at': sale.updated_at.isoformat() if sale.updated_at else None,
                    'items': [
                        {
                            'id': str(item.id),
                            'product_id': str(item.product_id),
                            'product_name': item.product_name,
                            'product_code': item.product_code,
                            'quantity': float(item.quantity),
                            'unit_price': float(item.unit_price),
                            'discount_percent': float(item.discount_percent) if item.discount_percent else 0,
                            'discount_amount': float(item.discount_amount) if item.discount_amount else 0,
                            'tva_rate': float(item.tva_rate) if item.tva_rate else 0,
                            'tva_amount': float(item.tva_amount) if item.tva_amount else 0,
                            'subtotal': float(item.subtotal) if item.subtotal else 0,
                            'total': float(item.total) if item.total else 0
                        }
                        for item in sale.items
                    ]
                },
                'timestamp': (sale.updated_at or sale.created_at).isoformat() if (sale.updated_at or sale.created_at) else None
            })
        
        # 5. Dettes
        from app.models.debt import Debt
        query = db.query(Debt).filter(Debt.tenant_id == tenant_id)
        if not is_super_admin and branch_id:
            query = query.filter(Debt.branch_id == branch_id)
        if since:
            query = query.filter(
                (Debt.updated_at >= since) | (Debt.created_at >= since)
            )
        
        for debt in query.all():
            changes['debts'].append({
                'id': str(debt.id),
                'action': 'update' if (debt.updated_at and since and debt.updated_at >= since) else 'create',
                'data': {
                    'id': str(debt.id),
                    'customer_id': str(debt.customer_id) if debt.customer_id else None,
                    'customer_name': debt.customer.name if debt.customer else None,
                    'sale_id': str(debt.sale_id) if debt.sale_id else None,
                    'debt_number': getattr(debt, 'debt_number', f"DEBT-{debt.created_at.strftime('%Y%m%d')}" if debt.created_at else None),
                    'initial_amount': float(debt.initial_amount),
                    'total_amount': float(debt.total_amount) if hasattr(debt, 'total_amount') else float(debt.initial_amount),
                    'paid_amount': float(debt.paid_amount),
                    'remaining_amount': float(debt.remaining_amount),
                    'interest_rate': float(debt.interest_rate) if debt.interest_rate else 0,
                    'interest_amount': float(debt.interest_amount) if debt.interest_amount else 0,
                    'issue_date': debt.issue_date.isoformat() if debt.issue_date else None,
                    'due_date': debt.due_date.isoformat() if debt.due_date else None,
                    'status': debt.status,
                    'notes': debt.notes,
                    'is_active': debt.is_active,
                    'branch_id': str(debt.branch_id) if hasattr(debt, 'branch_id') and debt.branch_id else None,
                    'pharmacy_id': str(debt.pharmacy_id) if hasattr(debt, 'pharmacy_id') and debt.pharmacy_id else None,
                    'created_at': debt.created_at.isoformat() if debt.created_at else None,
                    'updated_at': debt.updated_at.isoformat() if debt.updated_at else None
                },
                'timestamp': (debt.updated_at or debt.created_at).isoformat() if (debt.updated_at or debt.created_at) else None
            })
        
        # 6. Paiements de dettes
        from app.models.debt_payment import DebtPayment
        query = db.query(DebtPayment).filter(DebtPayment.tenant_id == tenant_id)
        if since:
            query = query.filter(DebtPayment.created_at >= since)
        
        for payment in query.all():
            changes['debt_payments'].append({
                'id': str(payment.id),
                'action': 'create',
                'data': {
                    'id': str(payment.id),
                    'debt_id': str(payment.debt_id),
                    'customer_id': str(payment.customer_id) if payment.customer_id else None,
                    'amount': float(payment.amount),
                    'payment_method': payment.payment_method,
                    'payment_date': payment.payment_date.isoformat() if hasattr(payment, 'payment_date') and payment.payment_date else payment.created_at.isoformat(),
                    'reference': payment.reference,
                    'notes': payment.notes,
                    'processed_by': str(payment.processed_by) if payment.processed_by else None,
                    'created_at': payment.created_at.isoformat() if payment.created_at else None
                },
                'timestamp': payment.created_at.isoformat() if payment.created_at else None
            })
        
        # 7. Utilisateurs
        from app.models.user import User
        query = db.query(User).filter(User.tenant_id == tenant_id)
        if not is_super_admin and user_id:
            query = query.filter(User.id == user_id)
        if since:
            query = query.filter(
                (User.updated_at >= since) | (User.created_at >= since)
            )
        
        for user in query.all():
            changes['users'].append({
                'id': str(user.id),
                'action': 'update' if (user.updated_at and since and user.updated_at >= since) else 'create',
                'data': {
                    'id': str(user.id),
                    'email': user.email,
                    'nom_complet': user.nom_complet,
                    'role': user.role,
                    'telephone': user.telephone,
                    'adresse': user.adresse,
                    'actif': user.actif,
                    'active_pharmacy_id': str(user.active_pharmacy_id) if user.active_pharmacy_id else None,
                    'active_branch_id': str(user.active_branch_id) if user.active_branch_id else None,
                    'permissions': user.permissions,
                    'last_login': user.last_login.isoformat() if user.last_login else None,
                    'created_at': user.created_at.isoformat() if user.created_at else None,
                    'updated_at': user.updated_at.isoformat() if user.updated_at else None
                },
                'timestamp': (user.updated_at or user.created_at).isoformat() if (user.updated_at or user.created_at) else None
            })
        
        # 8. Branches
        from app.models.branch import Branch
        query = db.query(Branch).filter(Branch.tenant_id == tenant_id)
        if not is_super_admin and branch_id:
            query = query.filter(Branch.id == branch_id)
        if since:
            query = query.filter(
                (Branch.updated_at >= since) | (Branch.created_at >= since)
            )
        
        for branch in query.all():
            changes['branches'].append({
                'id': str(branch.id),
                'action': 'update' if (branch.updated_at and since and branch.updated_at >= since) else 'create',
                'data': {
                    'id': str(branch.id),
                    'name': branch.name,
                    'code': branch.code,
                    'address': branch.address,
                    'city': branch.city,
                    'country': branch.country,
                    'phone': branch.phone,
                    'email': branch.email,
                    'latitude': float(branch.latitude) if branch.latitude else None,
                    'longitude': float(branch.longitude) if branch.longitude else None,
                    'manager_name': branch.manager_name,
                    'manager_id': str(branch.manager_id) if branch.manager_id else None,
                    'opening_hours': branch.opening_hours,
                    'config': branch.config,
                    'is_active': branch.is_active,
                    'is_main_branch': branch.is_main_branch,
                    'parent_pharmacy_id': str(branch.parent_pharmacy_id) if branch.parent_pharmacy_id else None,
                    'created_at': branch.created_at.isoformat() if branch.created_at else None,
                    'updated_at': branch.updated_at.isoformat() if branch.updated_at else None
                },
                'timestamp': (branch.updated_at or branch.created_at).isoformat() if (branch.updated_at or branch.created_at) else None
            })
        
        # 9. Pharmacies
        from app.models.pharmacy import Pharmacy
        query = db.query(Pharmacy).filter(Pharmacy.tenant_id == tenant_id)
        if since:
            query = query.filter(
                (Pharmacy.updated_at >= since) | (Pharmacy.created_at >= since)
            )
        
        for pharmacy in query.all():
            changes['pharmacies'].append({
                'id': str(pharmacy.id),
                'action': 'update' if (pharmacy.updated_at and since and pharmacy.updated_at >= since) else 'create',
                'data': {
                    'id': str(pharmacy.id),
                    'name': pharmacy.name,
                    'license_number': pharmacy.license_number,
                    'address': pharmacy.address,
                    'city': pharmacy.city,
                    'country': pharmacy.country,
                    'phone': pharmacy.phone,
                    'email': pharmacy.email,
                    'is_active': pharmacy.is_active,
                    'opening_hours': pharmacy.opening_hours,
                    'pharmacist_in_charge': pharmacy.pharmacist_in_charge,
                    'pharmacist_license': pharmacy.pharmacist_license,
                    'config': pharmacy.config,
                    'created_at': pharmacy.created_at.isoformat() if pharmacy.created_at else None,
                    'updated_at': pharmacy.updated_at.isoformat() if pharmacy.updated_at else None
                },
                'timestamp': (pharmacy.updated_at or pharmacy.created_at).isoformat() if (pharmacy.updated_at or pharmacy.created_at) else None
            })
        
        # 10. Abonnements des branches
        from app.models.branch_subscription import BranchSubscription
        from app.models.branch import Branch

        branches_query = db.query(Branch.id).filter(Branch.tenant_id == tenant_id)
        if not is_super_admin and branch_id:
            branches_query = branches_query.filter(Branch.id == branch_id)
        
        branch_ids = [b[0] for b in branches_query.all()]

        if branch_ids:
            query = db.query(BranchSubscription).filter(
                BranchSubscription.branch_id.in_(branch_ids),
                BranchSubscription.tenant_id == tenant_id
            )
            if since:
                query = query.filter(
                    (BranchSubscription.updated_at >= since) | 
                    (BranchSubscription.created_at >= since)
                )
            
            for sub in query.all():
                changes['subscriptions'].append({
                    'id': str(sub.id),
                    'action': 'update' if (sub.updated_at and since and sub.updated_at >= since) else 'create',
                    'data': {
                        'id': str(sub.id),
                        'branch_id': str(sub.branch_id),
                        'tenant_id': str(sub.tenant_id),
                        'pharmacy_id': str(sub.pharmacy_id) if sub.pharmacy_id else None,
                        'plan_name': sub.plan_name,
                        'plan_type': sub.plan,
                        'status': sub.status,
                        'max_users': sub.max_users,
                        'max_products': sub.max_products,
                        'max_storage_mb': sub.max_storage_mb,
                        'billing_cycle': sub.billing_cycle,
                        'price': sub.price,
                        'currency': sub.currency,
                        'current_period_start': sub.start_date.isoformat() if sub.start_date else None,
                        'current_period_end': sub.end_date.isoformat() if sub.end_date else None,
                        'trial_end_date': sub.trial_end_date.isoformat() if sub.trial_end_date else None,
                        'is_trial': sub.status == 'TRIAL',
                        'days_remaining': sub.days_remaining(),
                        'auto_renew': sub.auto_renew,
                        'cancel_at_period_end': not sub.auto_renew,
                        'is_active': sub.is_active(),
                        'created_at': sub.created_at.isoformat() if sub.created_at else None,
                        'updated_at': sub.updated_at.isoformat() if sub.updated_at else None
                    },
                    'timestamp': (sub.updated_at or sub.created_at).isoformat() if (sub.updated_at or sub.created_at) else None
                })
        
        # 11. Mouvements de stock
        from app.models.stock_movement import StockMovement
        query = db.query(StockMovement).filter(StockMovement.tenant_id == tenant_id)
        if not is_super_admin and branch_id:
            query = query.filter(StockMovement.branch_id == branch_id)
        if since:
            query = query.filter(StockMovement.created_at >= since)
        
        for movement in query.order_by(StockMovement.created_at.desc()).limit(500).all():
            changes['stock_movements'].append({
                'id': str(movement.id),
                'action': 'create',
                'data': {
                    'id': str(movement.id),
                    'product_id': str(movement.product_id),
                    'product_name': getattr(movement.product, 'name', None),
                    'quantity_before': float(movement.quantity_before or 0),
                    'quantity_after': float(movement.quantity_after or 0),
                    'quantity_change': float(movement.quantity_change or 0),
                    'movement_type': movement.movement_type,
                    'reason': movement.reason,
                    'reference': movement.reference,
                    'branch_id': str(movement.branch_id) if movement.branch_id else None,
                    'pharmacy_id': str(movement.pharmacy_id) if movement.pharmacy_id else None,
                    'created_at': movement.created_at.isoformat() if movement.created_at else None
                },
                'timestamp': movement.created_at.isoformat() if movement.created_at else None
            })
        
        # 12. Dépenses
        from app.models.finance import Expense
        query = db.query(Expense).filter(Expense.tenant_id == tenant_id)
        if not is_super_admin and branch_id:
            query = query.filter(Expense.branch_id == branch_id)
        if since:
            query = query.filter(Expense.updated_at >= since)
        
        for expense in query.all():
            changes['expenses'].append({
                'id': str(expense.id),
                'action': 'update' if (expense.updated_at and since and expense.updated_at >= since) else 'create',
                'data': {
                    'id': str(expense.id),
                    'tenant_id': str(expense.tenant_id),
                    'branch_id': str(expense.branch_id) if expense.branch_id else None,
                    'user_id': str(expense.user_id) if expense.user_id else None,
                    'expense_date': expense.expense_date.isoformat() if expense.expense_date else None,
                    'expense_type': expense.expense_type,
                    'amount': float(expense.amount),
                    'tax_amount': float(expense.tax_amount) if expense.tax_amount else 0,
                    'total_amount': float(expense.total_amount),
                    'supplier': expense.supplier,
                    'payee': expense.payee,
                    'payment_method': expense.payment_method,
                    'payment_reference': expense.payment_reference,
                    'description': expense.description,
                    'notes': expense.notes,
                    'invoice_number': expense.invoice_number,
                    'invoice_date': expense.invoice_date.isoformat() if expense.invoice_date else None,
                    'is_recurring': expense.is_recurring,
                    'recurrence_interval': expense.recurrence_interval,
                    'next_due_date': expense.next_due_date.isoformat() if expense.next_due_date else None,
                    'approved_by': str(expense.approved_by) if expense.approved_by else None,
                    'approval_status': expense.approval_status,
                    'rejection_reason': expense.rejection_reason,
                    'cost_center': expense.cost_center,
                    'project_code': expense.project_code,
                    'created_at': expense.created_at.isoformat() if expense.created_at else None,
                    'updated_at': expense.updated_at.isoformat() if expense.updated_at else None
                },
                'timestamp': (expense.updated_at or expense.created_at).isoformat() if (expense.updated_at or expense.created_at) else None
            })
        
        logger.info(f"Récupéré {sum(len(v) for v in changes.values())} changements pour tenant {tenant_id}")
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des changements: {str(e)}", exc_info=True)
        raise
    
    return changes


def get_sync_status(db: Session, tenant_id: UUID, branch_id: Optional[UUID] = None) -> Dict[str, Any]:
    """
    Récupère le statut de synchronisation pour un tenant et une branche.
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        branch_id: Identifiant de la branche (optionnel)
        
    Returns:
        Dict contenant le statut de synchronisation
    """
    from app.models.sale import Sale
    from app.models.product import Product
    from app.models.user import User
    from app.models.branch import Branch
    from app.models.debt import Debt
    from app.models.customer import Customer
    
    # Base query filters
    sale_filter = Sale.tenant_id == tenant_id
    product_filter = Product.tenant_id == tenant_id
    debt_filter = Debt.tenant_id == tenant_id
    customer_filter = Customer.tenant_id == tenant_id
    
    if branch_id:
        sale_filter = sale_filter & (Sale.branch_id == branch_id)
        product_filter = product_filter & (Product.branch_id == branch_id)
        debt_filter = debt_filter & (Debt.branch_id == branch_id)
        customer_filter = customer_filter & (Customer.branch_id == branch_id)
    
    last_sale = db.query(Sale).filter(sale_filter).order_by(Sale.updated_at.desc()).first()
    last_product = db.query(Product).filter(product_filter).order_by(Product.updated_at.desc()).first()
    last_user = db.query(User).filter(User.tenant_id == tenant_id).order_by(User.updated_at.desc()).first()
    last_branch = db.query(Branch).filter(Branch.tenant_id == tenant_id).order_by(Branch.updated_at.desc()).first()
    last_debt = db.query(Debt).filter(debt_filter).order_by(Debt.updated_at.desc()).first()
    last_customer = db.query(Customer).filter(customer_filter).order_by(Customer.updated_at.desc()).first()
    
    counts = {
        'sales': db.query(Sale).filter(sale_filter).count(),
        'products': db.query(Product).filter(product_filter).count(),
        'users': db.query(User).filter(User.tenant_id == tenant_id).count(),
        'branches': db.query(Branch).filter(Branch.tenant_id == tenant_id).count(),
        'debts': db.query(Debt).filter(debt_filter).count(),
        'customers': db.query(Customer).filter(customer_filter).count(),
    }
    
    total_pending_debts = db.query(Debt).filter(
        debt_filter,
        Debt.remaining_amount > 0,
        Debt.status.in_(["pending", "partial", "overdue"])
    ).count()
    
    total_overdue_amount = db.query(Debt).filter(
        debt_filter,
        Debt.due_date < date.today(),
        Debt.remaining_amount > 0
    ).with_entities(Debt.remaining_amount).all()
    
    total_overdue = sum(float(d[0]) for d in total_overdue_amount) if total_overdue_amount else 0
    
    return {
        'last_sync': None,
        'status': 'active',
        'tenant_id': str(tenant_id),
        'branch_id': str(branch_id) if branch_id else None,
        'last_update': {
            'sales': last_sale.updated_at.isoformat() if last_sale else None,
            'products': last_product.updated_at.isoformat() if last_product else None,
            'users': last_user.updated_at.isoformat() if last_user else None,
            'branches': last_branch.updated_at.isoformat() if last_branch else None,
            'debts': last_debt.updated_at.isoformat() if last_debt else None,
            'customers': last_customer.updated_at.isoformat() if last_customer else None
        },
        'counts': counts,
        'debts_summary': {
            'total_pending_debts': total_pending_debts,
            'total_overdue_amount': total_overdue
        }
    }


# ==========================================================
# HANDLERS PAR TABLE
# ==========================================================

def _sync_product(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any], 
                  user_branch_id: Optional[UUID] = None, user_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Synchronise un produit avec vérification de branche"""
    from app.models.product import Product
    from uuid import UUID as UUIDType
    
    # Ajouter le branch_id par défaut si nécessaire
    if user_branch_id and 'branch_id' not in data:
        data['branch_id'] = user_branch_id
    
    try:
        product_id = data.get('id')
        if product_id and isinstance(product_id, str):
            product_id = UUIDType(product_id)
        
        existing = None
        if product_id:
            existing = db.query(Product).filter(
                Product.id == product_id,
                Product.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.is_active = False
                existing.deleted_at = datetime.utcnow()
                db.flush()
                return {'success': True, 'id': str(product_id), 'action': 'deleted'}
            return {'success': False, 'error': 'Produit non trouvé'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            if hasattr(existing, 'refresh_statuses'):
                existing.refresh_statuses()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            product = Product(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Product, k)}
            )
            if hasattr(product, 'refresh_statuses'):
                product.refresh_statuses()
            db.add(product)
            db.flush()
            return {'success': True, 'id': str(product.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync product: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_category(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any],
                   user_branch_id: Optional[UUID] = None, user_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Synchronise une catégorie"""
    from app.models.category import Category
    from uuid import UUID as UUIDType
    
    try:
        category_id = data.get('id')
        if category_id and isinstance(category_id, str):
            category_id = UUIDType(category_id)
        
        existing = None
        if category_id:
            existing = db.query(Category).filter(
                Category.id == category_id,
                Category.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.is_active = False
                db.flush()
                return {'success': True, 'id': str(category_id), 'action': 'deactivated'}
            return {'success': False, 'error': 'Catégorie non trouvée'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            category = Category(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Category, k)}
            )
            db.add(category)
            db.flush()
            return {'success': True, 'id': str(category.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync category: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_customer(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any],
                   user_branch_id: Optional[UUID] = None, user_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Synchronise un client avec vérification de branche"""
    from app.models.customer import Customer
    from uuid import UUID as UUIDType
    
    # Ajouter le branch_id par défaut si nécessaire
    if user_branch_id and 'branch_id' not in data:
        data['branch_id'] = user_branch_id
    
    try:
        customer_id = data.get('id')
        if customer_id and isinstance(customer_id, str):
            customer_id = UUIDType(customer_id)
        
        existing = None
        if customer_id:
            existing = db.query(Customer).filter(
                Customer.id == customer_id,
                Customer.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.is_active = False
                db.flush()
                return {'success': True, 'id': str(customer_id), 'action': 'deactivated'}
            return {'success': False, 'error': 'Client non trouvé'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            customer = Customer(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Customer, k)}
            )
            db.add(customer)
            db.flush()
            return {'success': True, 'id': str(customer.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync customer: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_sale(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any],
               user_branch_id: Optional[UUID] = None, user_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Synchronise une vente avec vérification de branche"""
    from app.models.sale import Sale, SaleItem
    from app.models.product import Product
    from app.models.stock_movement import StockMovement
    from uuid import UUID as UUIDType
    from decimal import Decimal
    
    # Ajouter le branch_id par défaut si nécessaire
    if user_branch_id and 'branch_id' not in data:
        data['branch_id'] = user_branch_id
    
    # Ajouter l'utilisateur qui crée la vente
    if user_id and 'created_by' not in data:
        data['created_by'] = user_id
    
    try:
        sale_id = data.get('id')
        if sale_id and isinstance(sale_id, str):
            sale_id = UUIDType(sale_id)
        
        existing = None
        if sale_id:
            existing = db.query(Sale).filter(
                Sale.id == sale_id,
                Sale.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                # Vente supprimée = supprimée de la base (le retour éventuel est conservé)
                from app.services.sale_deletion_service import purge_sale, SaleDeletionError
                try:
                    purge_sale(db, existing, adjust_customer=False, delete_stock_movements=False)
                except SaleDeletionError as e:
                    return {'success': False, 'error': str(e)}
                return {'success': True, 'id': str(sale_id), 'action': 'deleted'}
            return {'success': False, 'error': 'Vente non trouvée'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            items_data = data.pop('items', [])
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            items_data = data.pop('items', [])
            data.pop('id', None)
            
            sale = Sale(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Sale, k)}
            )
            db.add(sale)
            db.flush()
            
            for item_data in items_data:
                item_data.pop('id', None)
                
                product_id = item_data.get('product_id')
                if product_id and isinstance(product_id, str):
                    product_id = UUIDType(product_id)
                
                product = None
                if product_id:
                    product = db.query(Product).filter(
                        Product.id == product_id,
                        Product.tenant_id == tenant_id
                    ).first()
                
                quantity = Decimal(str(item_data.get('quantity', 1)))
                
                sale_item = SaleItem(
                    tenant_id=tenant_id,
                    sale_id=sale.id,
                    **{k: v for k, v in item_data.items() if hasattr(SaleItem, k)}
                )
                db.add(sale_item)
                db.flush()
                
                if product and sale.status == "completed":
                    old_quantity = product.quantity or 0
                    new_quantity = max(0, old_quantity - int(quantity))
                    product.quantity = new_quantity
                    if hasattr(product, 'available_quantity'):
                        product.available_quantity = max(0, new_quantity - (product.reserved_quantity or 0))
                    if hasattr(product, 'refresh_statuses'):
                        product.refresh_statuses()
                    
                    movement = StockMovement(
                        tenant_id=tenant_id,
                        product_id=product.id,
                        pharmacy_id=sale.pharmacy_id,
                        branch_id=sale.branch_id,
                        quantity_before=old_quantity,
                        quantity_after=new_quantity,
                        quantity_change=-int(quantity),
                        movement_type="sale",
                        reason=f"Vente #{sale.reference}",
                        reference=sale.reference,
                        sale_id=sale.id,
                        sale_item_id=sale_item.id,
                        created_by=sale.created_by
                    )
                    db.add(movement)
            
            db.flush()
            return {'success': True, 'id': str(sale.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync sale: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_debt(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any],
               user_branch_id: Optional[UUID] = None, user_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Synchronise une dette avec vérification de branche"""
    from app.models.debt import Debt
    from app.models.customer import Customer
    from uuid import UUID as UUIDType
    from decimal import Decimal
    
    # Ajouter le branch_id par défaut si nécessaire
    if user_branch_id and 'branch_id' not in data:
        data['branch_id'] = user_branch_id
    
    try:
        debt_id = data.get('id')
        if debt_id and isinstance(debt_id, str):
            debt_id = UUIDType(debt_id)
        
        existing = None
        if debt_id:
            existing = db.query(Debt).filter(
                Debt.id == debt_id,
                Debt.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                db.delete(existing)
                db.flush()
                return {'success': True, 'id': str(debt_id), 'action': 'deleted'}
            return {'success': False, 'error': 'Dette non trouvée'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    if field in ['initial_amount', 'paid_amount', 'remaining_amount', 'interest_rate', 'interest_amount']:
                        value = Decimal(str(value)) if value else Decimal('0')
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            
            if hasattr(existing, 'update_status'):
                existing.update_status()
            
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            customer_id = data.get('customer_id')
            if customer_id:
                if isinstance(customer_id, str):
                    customer_id = UUIDType(customer_id)
                
                customer = db.query(Customer).filter(
                    Customer.id == customer_id,
                    Customer.tenant_id == tenant_id
                ).first()
                
                if not customer and data.get('customer_name'):
                    customer = Customer(
                        tenant_id=tenant_id,
                        id=customer_id,
                        name=data.get('customer_name'),
                        phone=data.get('customer_phone'),
                        email=data.get('customer_email'),
                        branch_id=data.get('branch_id')
                    )
                    db.add(customer)
                    db.flush()
            
            data.pop('id', None)
            
            for field in ['initial_amount', 'paid_amount', 'remaining_amount', 'interest_rate', 'interest_amount']:
                if field in data and data[field] is not None:
                    data[field] = Decimal(str(data[field]))
            
            debt = Debt(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Debt, k)}
            )
            db.add(debt)
            db.flush()
            
            if debt.customer_id:
                total_debt = db.query(Debt).filter(
                    Debt.customer_id == debt.customer_id,
                    Debt.remaining_amount > 0
                ).with_entities(Debt.remaining_amount).all()
                total = sum(float(d[0]) for d in total_debt) if total_debt else 0
                
                customer = db.query(Customer).filter(Customer.id == debt.customer_id).first()
                if customer:
                    customer.total_debt = total
                    db.flush()
            
            return {'success': True, 'id': str(debt.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync debt: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_debt_payment(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any],
                       user_branch_id: Optional[UUID] = None, user_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Synchronise un paiement de dette"""
    from app.models.debt import Debt, DebtPayment
    from uuid import UUID as UUIDType
    from decimal import Decimal
    
    try:
        payment_id = data.get('id')
        if payment_id and isinstance(payment_id, str):
            payment_id = UUIDType(payment_id)
        
        existing = None
        if payment_id:
            existing = db.query(DebtPayment).filter(
                DebtPayment.id == payment_id,
                DebtPayment.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                debt = db.query(Debt).filter(Debt.id == existing.debt_id).first()
                if debt:
                    debt.remaining_amount += existing.amount
                    debt.paid_amount -= existing.amount
                    if debt.remaining_amount > 0:
                        debt.status = "partial" if debt.paid_amount > 0 else "pending"
                    if hasattr(debt, 'update_status'):
                        debt.update_status()
                
                db.delete(existing)
                db.flush()
                return {'success': True, 'id': str(payment_id), 'action': 'deleted'}
            return {'success': False, 'error': 'Paiement non trouvé'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            return {'success': True, 'id': str(existing.id), 'action': 'ignored', 'reason': 'Debt payments are immutable'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            debt_id = data.get('debt_id')
            if debt_id and isinstance(debt_id, str):
                debt_id = UUIDType(debt_id)
            
            debt = db.query(Debt).filter(
                Debt.id == debt_id,
                Debt.tenant_id == tenant_id
            ).first()
            
            if not debt:
                return {'success': False, 'error': f'Dette {debt_id} non trouvée'}
            
            amount = Decimal(str(data.get('amount', 0)))
            
            if amount > debt.remaining_amount:
                return {'success': False, 'error': f'Le paiement ({amount}) dépasse le solde restant ({debt.remaining_amount})'}
            
            data.pop('id', None)
            data['tenant_id'] = tenant_id
            data['debt_id'] = debt.id
            
            payment = DebtPayment(
                **{k: v for k, v in data.items() if hasattr(DebtPayment, k)}
            )
            db.add(payment)
            db.flush()
            
            debt.remaining_amount -= amount
            debt.paid_amount += amount
            
            if debt.remaining_amount <= 0:
                debt.status = "paid"
            elif debt.paid_amount > 0:
                debt.status = "partial"
            
            if hasattr(debt, 'update_status'):
                debt.update_status()
            
            db.flush()
            
            return {'success': True, 'id': str(payment.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync debt payment: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_user(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any],
               user_branch_id: Optional[UUID] = None, user_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Synchronise un utilisateur"""
    from app.models.user import User
    from app.core.security import hash_password
    from uuid import UUID as UUIDType
    
    try:
        target_user_id = data.get('id')
        if target_user_id and isinstance(target_user_id, str):
            target_user_id = UUIDType(target_user_id)
        
        existing = None
        if target_user_id:
            existing = db.query(User).filter(
                User.id == target_user_id,
                User.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.actif = False
                db.flush()
                return {'success': True, 'id': str(target_user_id), 'action': 'deactivated'}
            return {'success': False, 'error': 'Utilisateur non trouvé'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and field != 'password' and hasattr(existing, field):
                    setattr(existing, field, value)
            if data.get('password'):
                existing.password_hash = hash_password(data['password'])
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            password = data.pop('password', None)
            # Tout utilisateur créé (hors admin) doit être affecté à une branche du tenant
            from app.services.user_branch_service import (
                BranchAssignmentError, resolve_required_branch, assign_user_to_branch,
            )
            target_branch = None
            if str(data.get('role', '')).lower() not in ('admin', 'tenant_admin', 'super_admin', 'superadmin'):
                try:
                    target_branch = resolve_required_branch(
                        db, tenant_id, data.get('branch_id') or data.get('active_branch_id')
                    )
                except BranchAssignmentError as exc:
                    return {'success': False, 'error': exc.message, 'code': exc.code}
            user = User(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(User, k)}
            )
            if password:
                user.password_hash = hash_password(password)
            db.add(user)
            db.flush()
            if target_branch is not None:
                assign_user_to_branch(db, user, target_branch, role=user.role)
            return {'success': True, 'id': str(user.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync user: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_branch(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any],
                 user_branch_id: Optional[UUID] = None, user_id: Optional[UUID] = None,
                 is_super_admin: bool = False) -> Dict[str, Any]:
    """Synchronise une branche - seule la branche de l'utilisateur peut être modifiée"""
    from app.models.branch import Branch
    from uuid import UUID as UUIDType
    
    # Un utilisateur normal ne peut modifier que sa propre branche
    if not is_super_admin and user_branch_id:
        branch_id = data.get('id')
        if branch_id and str(branch_id) != str(user_branch_id):
            return {'success': False, 'error': 'Vous ne pouvez modifier que votre propre branche'}
    
    try:
        branch_id = data.get('id')
        if branch_id and isinstance(branch_id, str):
            branch_id = UUIDType(branch_id)
        
        existing = None
        if branch_id:
            existing = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.is_active = False
                db.flush()
                return {'success': True, 'id': str(branch_id), 'action': 'deactivated'}
            return {'success': False, 'error': 'Branche non trouvée'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            branch = Branch(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Branch, k)}
            )
            db.add(branch)
            db.flush()
            return {'success': True, 'id': str(branch.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync branch: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_pharmacy(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any],
                   user_branch_id: Optional[UUID] = None, user_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Synchronise une pharmacie"""
    from app.models.pharmacy import Pharmacy
    from uuid import UUID as UUIDType
    
    try:
        pharmacy_id = data.get('id')
        if pharmacy_id and isinstance(pharmacy_id, str):
            pharmacy_id = UUIDType(pharmacy_id)
        
        existing = None
        if pharmacy_id:
            existing = db.query(Pharmacy).filter(
                Pharmacy.id == pharmacy_id,
                Pharmacy.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.is_active = False
                db.flush()
                return {'success': True, 'id': str(pharmacy_id), 'action': 'deactivated'}
            return {'success': False, 'error': 'Pharmacie non trouvée'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            pharmacy = Pharmacy(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Pharmacy, k)}
            )
            db.add(pharmacy)
            db.flush()
            return {'success': True, 'id': str(pharmacy.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync pharmacy: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_subscription(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any],
                       user_branch_id: Optional[UUID] = None, user_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Synchronise un abonnement de branche"""
    from app.models.branch_subscription import BranchSubscription, SubscriptionPlan, SubscriptionStatus
    from app.models.branch import Branch
    from uuid import UUID as UUIDType
    from datetime import datetime, timedelta
    
    try:
        sub_id = data.get('id')
        if sub_id and isinstance(sub_id, str):
            sub_id = UUIDType(sub_id)
        
        branch_id = data.get('branch_id')
        if branch_id and isinstance(branch_id, str):
            branch_id = UUIDType(branch_id)
        
        # Vérifier que la branche existe et appartient au tenant
        branch = None
        if branch_id:
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id
            ).first()
            
            if not branch:
                return {'success': False, 'error': f'Branche {branch_id} non trouvée ou n\'appartient pas au tenant'}
        
        existing = None
        if sub_id:
            existing = db.query(BranchSubscription).filter(
                BranchSubscription.id == sub_id,
                BranchSubscription.tenant_id == tenant_id
            ).first()
        elif branch_id:
            existing = db.query(BranchSubscription).filter(
                BranchSubscription.branch_id == branch_id,
                BranchSubscription.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.status = SubscriptionStatus.CANCELLED.value
                existing.cancelled_at = datetime.utcnow()
                existing.updated_at = datetime.utcnow()
                db.flush()
                return {'success': True, 'id': str(existing.id), 'action': 'cancelled'}
            return {'success': False, 'error': 'Abonnement non trouvé'}
        
        data['branch_id'] = branch_id
        data['tenant_id'] = tenant_id
        data.pop('id', None)
        
        if 'plan' in data:
            plan_value = data['plan'].upper() if isinstance(data['plan'], str) else data['plan']
            data['plan'] = plan_value
        
        if 'status' in data:
            status_value = data['status'].upper() if isinstance(data['status'], str) else data['status']
            data['status'] = status_value
        
        if data.get('is_trial') and not data.get('trial_end_date'):
            data['trial_end_date'] = datetime.utcnow() + timedelta(days=14)
            if not data.get('end_date'):
                data['end_date'] = data['trial_end_date']
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            updatable_fields = ['status', 'auto_renew', 'billing_cycle', 'plan', 'plan_name', 
                                'max_products', 'max_users', 'max_storage_mb', 'end_date', 
                                'trial_end_date', 'price', 'currency', 'cancelled_reason']
            for field in updatable_fields:
                if field in data and hasattr(existing, field):
                    setattr(existing, field, data[field])
            existing.updated_at = datetime.utcnow()
            
            if existing.end_date and existing.end_date < datetime.utcnow():
                existing.status = SubscriptionStatus.EXPIRED.value
            
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            if not branch:
                return {'success': False, 'error': 'branch_id requis pour la création'}
            
            existing_for_branch = db.query(BranchSubscription).filter(
                BranchSubscription.branch_id == branch_id,
                BranchSubscription.tenant_id == tenant_id
            ).first()
            
            if existing_for_branch:
                for field, value in data.items():
                    if hasattr(existing_for_branch, field):
                        setattr(existing_for_branch, field, value)
                existing_for_branch.updated_at = datetime.utcnow()
                db.flush()
                return {'success': True, 'id': str(existing_for_branch.id), 'action': 'updated'}
            
            subscription = BranchSubscription(
                branch_id=branch_id,
                tenant_id=tenant_id,
                pharmacy_id=branch.parent_pharmacy_id if branch.parent_pharmacy_id else None,
                plan=data.get('plan', SubscriptionPlan.TRIAL.value),
                plan_name=data.get('plan_name', 'Essai'),
                start_date=data.get('start_date', datetime.utcnow()),
                end_date=data.get('end_date', datetime.utcnow() + timedelta(days=30)),
                trial_end_date=data.get('trial_end_date'),
                status=data.get('status', SubscriptionStatus.TRIAL.value if data.get('is_trial') else SubscriptionStatus.ACTIVE.value),
                billing_cycle=data.get('billing_cycle', 'monthly'),
                price=data.get('price', 0.0),
                currency=data.get('currency', 'EUR'),
                auto_renew=data.get('auto_renew', True),
                max_products=data.get('max_products', 100),
                max_users=data.get('max_users', 5),
                max_storage_mb=data.get('max_storage_mb', 100)
            )
            db.add(subscription)
            db.flush()
            
            branch.subscription_id = subscription.id
            db.flush()
            
            return {'success': True, 'id': str(subscription.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync subscription: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_stock_movement(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any],
                         user_branch_id: Optional[UUID] = None, user_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Synchronise un mouvement de stock"""
    from app.models.stock_movement import StockMovement
    from uuid import UUID as UUIDType
    
    # Ajouter le branch_id par défaut si nécessaire
    if user_branch_id and 'branch_id' not in data:
        data['branch_id'] = user_branch_id
    
    try:
        movement_id = data.get('id')
        if movement_id and isinstance(movement_id, str):
            movement_id = UUIDType(movement_id)
        
        existing = None
        if movement_id:
            existing = db.query(StockMovement).filter(
                StockMovement.id == movement_id,
                StockMovement.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if not existing and action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            movement = StockMovement(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(StockMovement, k)}
            )
            db.add(movement)
            db.flush()
            return {'success': True, 'id': str(movement.id), 'action': 'created'}
        
        return {'success': True, 'action': 'ignored', 'reason': 'Stock movements are read-only'}
        
    except Exception as e:
        logger.error(f"Erreur sync stock movement: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_expense(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any],
                  user_branch_id: Optional[UUID] = None, user_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Synchronise une dépense"""
    from app.models.finance import Expense
    from uuid import UUID as UUIDType
    
    # Ajouter le branch_id par défaut si nécessaire
    if user_branch_id and 'branch_id' not in data:
        data['branch_id'] = user_branch_id
    
    try:
        expense_id = data.get('id')
        if expense_id and isinstance(expense_id, str):
            expense_id = UUIDType(expense_id)
        
        existing = None
        if expense_id:
            existing = db.query(Expense).filter(
                Expense.id == expense_id,
                Expense.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                if hasattr(existing, 'is_active'):
                    existing.is_active = False
                else:
                    db.delete(existing)
                db.flush()
                return {'success': True, 'id': str(expense_id), 'action': 'deleted'}
            return {'success': False, 'error': 'Dépense non trouvée'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.total_amount = existing.amount + (existing.tax_amount or 0)
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            expense = Expense(
                tenant_id=tenant_id,
                user_id=user_id,
                **{k: v for k, v in data.items() if hasattr(Expense, k)}
            )
            expense.total_amount = expense.amount + (expense.tax_amount or 0)
            db.add(expense)
            db.flush()
            return {'success': True, 'id': str(expense.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync expense: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_return(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any],
                 user_branch_id: Optional[UUID] = None, user_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Synchronise un retour produit"""
    from app.models.return_product import Return
    from uuid import UUID as UUIDType
    
    # Ajouter le branch_id par défaut si nécessaire
    if user_branch_id and 'branch_id' not in data:
        data['branch_id'] = user_branch_id
    
    try:
        return_id = data.get('id')
        if return_id and isinstance(return_id, str):
            return_id = UUIDType(return_id)
        
        existing = None
        if return_id:
            existing = db.query(Return).filter(
                Return.id == return_id,
                Return.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                if hasattr(existing, 'is_active'):
                    existing.is_active = False
                else:
                    db.delete(existing)
                db.flush()
                return {'success': True, 'id': str(return_id), 'action': 'deleted'}
            return {'success': False, 'error': 'Retour non trouvé'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            return_obj = Return(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Return, k)}
            )
            db.add(return_obj)
            db.flush()
            return {'success': True, 'id': str(return_obj.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync return: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


# ==========================================================
# FONCTIONS UTILITAIRES
# ==========================================================

def _serialize_product(product) -> Dict[str, Any]:
    """Sérialise un produit en dictionnaire"""
    return {
        'id': str(product.id),
        'code': product.code,
        'name': product.name,
        'commercial_name': product.commercial_name,
        'barcode': product.barcode,
        'quantity': product.quantity or 0,
        'available_quantity': getattr(product, 'available_quantity', product.quantity or 0),
        'purchase_price': float(product.purchase_price or 0),
        'selling_price': float(product.selling_price or 0),
        'category': product.category,
        'category_id': str(product.category_id) if product.category_id else None,
        'product_type': product.product_type,
        'unit': product.unit,
        'alert_threshold': product.alert_threshold,
        'minimum_stock': product.minimum_stock,
        'maximum_stock': product.maximum_stock,
        'expiry_date': product.expiry_date.isoformat() if product.expiry_date else None,
        'batch_number': product.batch_number,
        'location': product.location,
        'main_supplier': product.main_supplier,
        'has_tva': product.has_tva,
        'tva_rate': product.tva_rate,
        'prescription_required': product.prescription_required,
        'stock_status': product.stock_status,
        'expiry_status': product.expiry_status,
        'is_active': product.is_active,
        'branch_id': str(product.branch_id) if product.branch_id else None,
        'pharmacy_id': str(product.pharmacy_id) if product.pharmacy_id else None,
        'created_at': product.created_at.isoformat() if product.created_at else None,
        'updated_at': product.updated_at.isoformat() if product.updated_at else None
    }
# app/services/admin_sync_service.py
"""
Service de synchronisation pour admin offline
Gère l'export/import complet des données multi-tenant
"""

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, desc
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import json
import hashlib
import time
from decimal import Decimal

from app.models.admin_sync import (
    AdminSyncLog, AdminSyncCheckpoint, AdminSyncBatch, AdminSyncFilter,
    SyncEntityType, SyncOperation, SyncStatus
)
from app.models.tenant import Tenant
from app.models.branch import Branch
from app.models.user import User
from app.models.product import Product, ProductStock
from app.models.sale import Sale
from app.models.invoice import Invoice, InvoicePayment
from app.models.purchase import Purchase, PurchaseItem, PurchasePayment
from app.models.debt_payment import DebtPayment
from app.models.debt import Debt
from app.models.capital import Capital, CapitalTransaction, Turnover
from app.models.finance import Expense
from app.models.customer import Customer
from app.models.cost import Supplier
from app.models.stock_movement import StockMovement
from app.models.order import Order
from app.models.payment import Payment
from app.models.category import Category
from app.models.stock_adjustment import StockAdjustment
from app.models.transfert import ProductTransfer
from app.models.audit_log import AuditLog
from app.models.user_history import UserHistory
from app.db.session import SessionLocal


class AdminSyncService:
    """Service central de synchronisation admin"""
    
    def __init__(self, db: Session):
        self.db = db
        
    # ==================== EXPORT ====================
    
    def export_all_tenant_data(self, tenant_id: int, 
                               branch_ids: List[int] = None,
                               entity_types: List[SyncEntityType] = None,
                               since: datetime = None,
                               include_deleted: bool = False) -> Dict[str, Any]:
        """
        Exporte toutes les données d'un tenant et ses branches
        
        Args:
            tenant_id: ID du tenant
            branch_ids: Liste des IDs de branches (None = toutes)
            entity_types: Types d'entités à exporter
            since: Export uniquement les données modifiées depuis cette date
            include_deleted: Inclure les entités supprimées
        """
        start_time = time.time()
        
        # Vérifier le tenant
        tenant = self.db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} non trouvé")
        
        # Récupérer les branches
        branches_query = self.db.query(Branch).filter(Branch.tenant_id == tenant_id)
        if branch_ids:
            branches_query = branches_query.filter(Branch.id.in_(branch_ids))
        branches = branches_query.all()
        
        if not branches:
            raise ValueError(f"Aucune branche trouvée pour tenant {tenant_id}")
        
        # Déterminer les entités à exporter
        if not entity_types:
            entity_types = [e for e in SyncEntityType]
        
        export_data = {
            "metadata": {
                "exported_at": datetime.utcnow().isoformat(),
                "tenant_id": tenant_id,
                "tenant_name": tenant.name,
                "branches": [{"id": b.id, "name": b.name} for b in branches],
                "entity_types": [e.value for e in entity_types],
                "since": since.isoformat() if since else None,
                "version": "1.0"
            },
            "data": {}
        }
        
        # Exporter chaque type d'entité
        for entity_type in entity_types:
            export_method = getattr(self, f"_export_{entity_type.value}", None)
            if export_method:
                data = export_method(tenant_id, branches, since, include_deleted)
                if data:
                    export_data["data"][entity_type.value] = data
        
        # Calculer la taille des données
        data_size = len(json.dumps(export_data, default=self._json_serializer))
        
        # Logger l'export
        sync_log = AdminSyncLog(
            source_tenant_id=tenant_id,
            entity_type=SyncEntityType.TENANT,
            entity_id=tenant_id,
            entity_version=hashlib.md5(json.dumps(export_data).encode()).hexdigest(),
            entity_data=export_data,
            operation=SyncOperation.MERGE,
            sync_status=SyncStatus.SYNCED,
            sync_duration_ms=int((time.time() - start_time) * 1000),
            data_size_bytes=data_size
        )
        self.db.add(sync_log)
        self.db.commit()
        
        export_data["metadata"]["sync_log_id"] = sync_log.id
        export_data["metadata"]["data_size_bytes"] = data_size
        
        return export_data
    
    def _export_products(self, tenant_id: int, branches: List[Branch], 
                         since: datetime = None, include_deleted: bool = False) -> List[Dict]:
        """Exporte tous les produits et stocks"""
        query = self.db.query(Product).filter(Product.tenant_id == tenant_id)
        
        if since:
            query = query.filter(Product.updated_at >= since)
        
        products = query.all()
        
        result = []
        for product in products:
            # Récupérer les stocks par branche
            stocks = self.db.query(ProductStock).filter(
                ProductStock.product_id == product.id,
                ProductStock.branch_id.in_([b.id for b in branches])
            ).all()
            
            result.append({
                "id": product.id,
                "name": product.name,
                "code": product.code,
                "barcode": product.barcode,
                "category_id": product.category_id,
                "purchase_price": float(product.purchase_price) if product.purchase_price else None,
                "selling_price": float(product.selling_price) if product.selling_price else None,
                "wholesale_price": float(product.wholesale_price) if product.wholesale_price else None,
                "stock_threshold": product.stock_threshold,
                "unit": product.unit,
                "tax_rate": float(product.tax_rate) if product.tax_rate else None,
                "description": product.description,
                "is_active": product.is_active,
                "stocks": [
                    {
                        "branch_id": stock.branch_id,
                        "quantity": float(stock.quantity),
                        "reserved_quantity": float(stock.reserved_quantity) if stock.reserved_quantity else 0,
                        "min_stock": stock.min_stock,
                        "max_stock": stock.max_stock
                    }
                    for stock in stocks
                ],
                "created_at": product.created_at.isoformat() if product.created_at else None,
                "updated_at": product.updated_at.isoformat() if product.updated_at else None
            })
        
        return result
    
    def _export_sales(self, tenant_id: int, branches: List[Branch],
                      since: datetime = None, include_deleted: bool = False) -> List[Dict]:
        """Exporte toutes les ventes"""
        query = self.db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.branch_id.in_([b.id for b in branches])
        )
        
        if since:
            query = query.filter(Sale.updated_at >= since)
        
        sales = query.options(
            joinedload(Sale.items),
            joinedload(Sale.payments)
        ).all()
        
        result = []
        for sale in sales:
            result.append({
                "id": sale.id,
                "invoice_number": sale.invoice_number,
                "branch_id": sale.branch_id,
                "user_id": sale.user_id,
                "customer_id": sale.customer_id,
                "total_amount": float(sale.total_amount),
                "discount": float(sale.discount) if sale.discount else 0,
                "tax": float(sale.tax) if sale.tax else 0,
                "paid_amount": float(sale.paid_amount),
                "due_amount": float(sale.due_amount),
                "status": sale.status,
                "payment_method": sale.payment_method,
                "items": [
                    {
                        "product_id": item.product_id,
                        "quantity": float(item.quantity),
                        "unit_price": float(item.unit_price),
                        "total": float(item.total)
                    }
                    for item in sale.items
                ],
                "created_at": sale.created_at.isoformat() if sale.created_at else None,
                "updated_at": sale.updated_at.isoformat() if sale.updated_at else None
            })
        
        return result
    
    def _export_invoices(self, tenant_id: int, branches: List[Branch],
                         since: datetime = None, include_deleted: bool = False) -> List[Dict]:
        """Exporte toutes les factures"""
        query = self.db.query(Invoice).filter(
            Invoice.tenant_id == tenant_id,
            Invoice.branch_id.in_([b.id for b in branches])
        )
        
        if since:
            query = query.filter(Invoice.updated_at >= since)
        
        invoices = query.all()
        
        return [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "type": inv.type,
                "branch_id": inv.branch_id,
                "customer_id": inv.customer_id,
                "total_amount": float(inv.total_amount),
                "paid_amount": float(inv.paid_amount),
                "due_amount": float(inv.due_amount),
                "status": inv.status,
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
                "updated_at": inv.updated_at.isoformat() if inv.updated_at else None
            }
            for inv in invoices
        ]
    
    def _export_debts(self, tenant_id: int, branches: List[Branch],
                      since: datetime = None, include_deleted: bool = False) -> List[Dict]:
        """Exporte toutes les dettes"""
        query = self.db.query(Debt).filter(
            Debt.tenant_id == tenant_id,
            Debt.branch_id.in_([b.id for b in branches])
        )
        
        if since:
            query = query.filter(Debt.updated_at >= since)
        
        debts = query.all()
        
        result = []
        for debt in debts:
            payments = self.db.query(DebtPayment).filter(DebtPayment.debt_id == debt.id).all()
            
            result.append({
                "id": debt.id,
                "branch_id": debt.branch_id,
                "customer_id": debt.customer_id,
                "supplier_id": debt.supplier_id,
                "type": debt.type,
                "amount": float(debt.amount),
                "paid_amount": float(debt.paid_amount),
                "remaining_amount": float(debt.remaining_amount),
                "due_date": debt.due_date.isoformat() if debt.due_date else None,
                "status": debt.status,
                "payments": [
                    {
                        "amount": float(p.amount),
                        "payment_date": p.payment_date.isoformat() if p.payment_date else None,
                        "payment_method": p.payment_method
                    }
                    for p in payments
                ],
                "created_at": debt.created_at.isoformat() if debt.created_at else None,
                "updated_at": debt.updated_at.isoformat() if debt.updated_at else None
            })
        
        return result
    
    def _export_capital(self, tenant_id: int, branches: List[Branch],
                        since: datetime = None, include_deleted: bool = False) -> List[Dict]:
        """Exporte les capitaux et transactions"""
        query = self.db.query(Capital).filter(
            Capital.tenant_id == tenant_id,
            Capital.branch_id.in_([b.id for b in branches])
        )
        
        if since:
            query = query.filter(Capital.updated_at >= since)
        
        capitals = query.all()
        
        result = []
        for capital in capitals:
            transactions = self.db.query(CapitalTransaction).filter(
                CapitalTransaction.capital_id == capital.id
            ).all()
            
            turnovers = self.db.query(Turnover).filter(
                Turnover.capital_id == capital.id
            ).all()
            
            result.append({
                "id": capital.id,
                "branch_id": capital.branch_id,
                "initial_amount": float(capital.initial_amount),
                "current_amount": float(capital.current_amount),
                "transactions": [
                    {
                        "amount": float(t.amount),
                        "type": t.type,
                        "description": t.description,
                        "date": t.date.isoformat() if t.date else None
                    }
                    for t in transactions
                ],
                "turnovers": [
                    {
                        "period": t.period,
                        "amount": float(t.amount),
                        "profit_loss": float(t.profit_loss) if t.profit_loss else None
                    }
                    for t in turnovers
                ],
                "updated_at": capital.updated_at.isoformat() if capital.updated_at else None
            })
        
        return result
    
    def _export_expenses(self, tenant_id: int, branches: List[Branch],
                         since: datetime = None, include_deleted: bool = False) -> List[Dict]:
        """Exporte toutes les dépenses"""
        query = self.db.query(Expense).filter(
            Expense.tenant_id == tenant_id,
            Expense.branch_id.in_([b.id for b in branches])
        )
        
        if since:
            query = query.filter(Expense.updated_at >= since)
        
        expenses = query.all()
        
        return [
            {
                "id": exp.id,
                "branch_id": exp.branch_id,
                "category": exp.category,
                "amount": float(exp.amount),
                "description": exp.description,
                "expense_date": exp.expense_date.isoformat() if exp.expense_date else None,
                "created_by": exp.created_by,
                "created_at": exp.created_at.isoformat() if exp.created_at else None
            }
            for exp in expenses
        ]
    
    def _export_customers(self, tenant_id: int, branches: List[Branch],
                          since: datetime = None, include_deleted: bool = False) -> List[Dict]:
        """Exporte tous les clients"""
        query = self.db.query(Customer).filter(
            Customer.tenant_id == tenant_id,
            Customer.branch_id.in_([b.id for b in branches])
        )
        
        if since:
            query = query.filter(Customer.updated_at >= since)
        
        customers = query.all()
        
        return [
            {
                "id": c.id,
                "branch_id": c.branch_id,
                "name": c.name,
                "phone": c.phone,
                "email": c.email,
                "address": c.address,
                "total_purchases": float(c.total_purchases) if c.total_purchases else 0,
                "total_debt": float(c.total_debt) if c.total_debt else 0,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None
            }
            for c in customers
        ]
    
    def _export_suppliers(self, tenant_id: int, branches: List[Branch],
                          since: datetime = None, include_deleted: bool = False) -> List[Dict]:
        """Exporte tous les fournisseurs"""
        query = self.db.query(Supplier).filter(
            Supplier.tenant_id == tenant_id,
            Supplier.branch_id.in_([b.id for b in branches])
        )
        
        if since:
            query = query.filter(Supplier.updated_at >= since)
        
        suppliers = query.all()
        
        return [
            {
                "id": s.id,
                "branch_id": s.branch_id,
                "name": s.name,
                "contact": s.contact,
                "phone": s.phone,
                "email": s.email,
                "address": s.address,
                "created_at": s.created_at.isoformat() if s.created_at else None
            }
            for s in suppliers
        ]
    
    def _export_users(self, tenant_id: int, branches: List[Branch],
                      since: datetime = None, include_deleted: bool = False) -> List[Dict]:
        """Exporte tous les utilisateurs du tenant"""
        query = self.db.query(User).filter(User.tenant_id == tenant_id)
        
        if since:
            query = query.filter(User.updated_at >= since)
        
        users = query.all()
        
        # Récupérer les historiques utilisateur
        user_histories = {}
        if since:
            histories = self.db.query(UserHistory).filter(
                UserHistory.tenant_id == tenant_id,
                UserHistory.created_at >= since if since else True
            ).all()
            
            for history in histories:
                if history.user_id not in user_histories:
                    user_histories[history.user_id] = []
                user_histories[history.user_id].append({
                    "action": history.action,
                    "details": history.details,
                    "created_at": history.created_at.isoformat() if history.created_at else None
                })
        
        return [
            {
                "id": u.id,
                "email": u.email,
                "full_name": u.full_name,
                "role": u.role,
                "is_active": u.is_active,
                "branch_ids": [ub.branch_id for ub in u.branches] if u.branches else [],
                "history": user_histories.get(u.id, []),
                "last_login": u.last_login.isoformat() if u.last_login else None,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "updated_at": u.updated_at.isoformat() if u.updated_at else None
            }
            for u in users
        ]
    
    # ==================== IMPORT ====================
    
    def import_admin_data(self, admin_user_id: int, 
                         import_data: Dict[str, Any],
                         strategy: str = "merge") -> Dict[str, Any]:
        """
        Importe les données modifiées par l'admin
        
        Args:
            admin_user_id: ID de l'admin qui importe
            import_data: Données à importer
            strategy: 'merge', 'overwrite', 'skip'
        """
        start_time = time.time()
        
        results = {
            "total_processed": 0,
            "success": 0,
            "failed": 0,
            "conflicts": 0,
            "details": {}
        }
        
        data = import_data.get("data", {})
        
        for entity_type_str, entities in data.items():
            try:
                entity_type = SyncEntityType(entity_type_str)
                import_method = getattr(self, f"_import_{entity_type.value}", None)
                
                if import_method:
                    entity_results = import_method(entities, admin_user_id, strategy)
                    results["details"][entity_type_str] = entity_results
                    results["total_processed"] += entity_results.get("total", 0)
                    results["success"] += entity_results.get("success", 0)
                    results["failed"] += entity_results.get("failed", 0)
                    results["conflicts"] += entity_results.get("conflicts", 0)
            except Exception as e:
                results["details"][entity_type_str] = {"error": str(e)}
                results["failed"] += len(entities)
        
        results["duration_ms"] = int((time.time() - start_time) * 1000)
        
        # Logger l'import
        sync_log = AdminSyncLog(
            admin_user_id=admin_user_id,
            entity_type=SyncEntityType.TENANT,
            entity_id=0,  # Import global
            entity_version=hashlib.md5(json.dumps(import_data).encode()).hexdigest(),
            entity_data=results,
            operation=SyncOperation.MERGE,
            sync_status=SyncStatus.SYNCED if results["failed"] == 0 else SyncStatus.CONFLICT,
            sync_duration_ms=results["duration_ms"]
        )
        self.db.add(sync_log)
        self.db.commit()
        
        return results
    
    def _import_products(self, products_data: List[Dict], admin_user_id: int,
                        strategy: str = "merge") -> Dict:
        """Importe/update les produits"""
        results = {"total": len(products_data), "success": 0, "failed": 0, "conflicts": 0}
        
        for product_data in products_data:
            try:
                existing = self.db.query(Product).filter(
                    Product.id == product_data["id"],
                    Product.tenant_id == product_data.get("tenant_id")
                ).first()
                
                if existing:
                    if strategy == "skip":
                        results["success"] += 1
                        continue
                    elif strategy == "overwrite":
                        # Mettre à jour toutes les données
                        for key, value in product_data.items():
                            if hasattr(existing, key) and key not in ["id", "tenant_id", "created_at"]:
                                setattr(existing, key, value)
                    else:  # merge
                        # Fusion intelligente (seulement certains champs)
                        updatable_fields = ["selling_price", "purchase_price", "stock_threshold", 
                                          "description", "is_active"]
                        for field in updatable_fields:
                            if field in product_data:
                                setattr(existing, field, product_data[field])
                else:
                    # Créer nouveau produit
                    product = Product(**product_data)
                    self.db.add(product)
                
                results["success"] += 1
            except Exception as e:
                results["failed"] += 1
                results["conflicts"] += 1
        
        self.db.commit()
        return results
    
    def _import_sales(self, sales_data: List[Dict], admin_user_id: int,
                     strategy: str = "merge") -> Dict:
        """Importe/update les ventes"""
        results = {"total": len(sales_data), "success": 0, "failed": 0, "conflicts": 0}
        
        for sale_data in sales_data:
            try:
                existing = self.db.query(Sale).filter(Sale.id == sale_data["id"]).first()
                
                if existing:
                    if strategy != "skip":
                        # Mettre à jour le statut et les paiements
                        existing.status = sale_data.get("status", existing.status)
                        existing.paid_amount = sale_data.get("paid_amount", existing.paid_amount)
                        existing.due_amount = sale_data.get("due_amount", existing.due_amount)
                else:
                    # Créer nouvelle vente
                    items_data = sale_data.pop("items", [])
                    sale = Sale(**sale_data)
                    self.db.add(sale)
                    self.db.flush()
                    
                    # Ajouter les items
                    for item_data in items_data:
                        item_data["sale_id"] = sale.id
                        from app.models.sale import SaleItem
                        sale_item = SaleItem(**item_data)
                        self.db.add(sale_item)
                
                results["success"] += 1
            except Exception as e:
                results["failed"] += 1
                results["conflicts"] += 1
        
        self.db.commit()
        return results
    
    # ==================== UTILITAIRES ====================
    
    def create_sync_batch(self, tenant_id: int, branch_ids: List[int] = None,
                          entity_types: List[SyncEntityType] = None,
                          admin_user_id: int = None) -> AdminSyncBatch:
        """Crée un lot de synchronisation"""
        batch_id = f"batch_{tenant_id}_{datetime.utcnow().timestamp()}"
        
        batch = AdminSyncBatch(
            batch_id=batch_id,
            tenant_id=tenant_id,
            branch_ids=branch_ids,
            entity_types=[e.value for e in entity_types] if entity_types else None,
            exported_by=admin_user_id,
            status=SyncStatus.PENDING
        )
        self.db.add(batch)
        self.db.commit()
        
        return batch
    
    def get_sync_status(self, tenant_id: int = None, branch_id: int = None,
                       entity_type: SyncEntityType = None,
                       limit: int = 100) -> List[Dict]:
        """Récupère le statut de synchronisation"""
        query = self.db.query(AdminSyncLog)
        
        if tenant_id:
            query = query.filter(AdminSyncLog.source_tenant_id == tenant_id)
        if branch_id:
            query = query.filter(AdminSyncLog.source_branch_id == branch_id)
        if entity_type:
            query = query.filter(AdminSyncLog.entity_type == entity_type)
        
        logs = query.order_by(desc(AdminSyncLog.created_at)).limit(limit).all()
        
        return [
            {
                "id": log.id,
                "entity_type": log.entity_type.value,
                "entity_id": log.entity_id,
                "status": log.sync_status.value,
                "operation": log.operation.value,
                "synced_at": log.synced_at.isoformat() if log.synced_at else None,
                "duration_ms": log.sync_duration_ms,
                "data_size_bytes": log.data_size_bytes
            }
            for log in logs
        ]
    
    def _json_serializer(self, obj):
        """Sérialiseur JSON pour les objets spéciaux"""
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")
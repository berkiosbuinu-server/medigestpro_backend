# app/models/__init__.py
"""
Initialisation des modèles.
Objectif: importer les modèles pour enregistrer les tables dans Base.metadata
(utilisé par app/create_tables.py pour créer le schéma au démarrage).
"""

# =========================
# 1. MODÈLES DE BASE (sans dépendances externes)
# =========================
from .tenant import Tenant
from .user import User
from .subscription import Subscription
from .pharmacy import Pharmacy
from .user_pharmacy import UserPharmacy
from .sync_log import SyncLog
from .branch import Branch
from .category import Category
from .customer import Customer

# =========================
# 2. MODÈLES DE STRUCTURE (dépendent des modèles de base)
# =========================
from .department import Department  
from .project import Project        
from .user_branch import UserBranch
from .branch_subscription import BranchSubscription
from .user_subscription import UserSubscription

# =========================
# 3. MODÈLES FINANCIERS (dépendent des modèles de structure)
# =========================
from .cost import Cost, Budget, Supplier, CostAllocation
from .finance import FinancialPeriod, FinancialTransaction, Expense
from .capital import Capital, CapitalAccount, CapitalTransaction, Turnover, AdjustedCapital

# =========================
# 4. MODÈLES DE PRODUITS ET STOCK
# =========================
from .product import Product, ProductStock
from .stock_movement import StockMovement, InventoryCount, InventoryCountItem
from .stock_adjustment import StockAdjustment, StockAdjustmentItem
from .inventory import PhysicalInventory, InventoryItem, InventorySchedule

# =========================
# 5. MODÈLES DE TRANSACTIONS
# =========================
from .sale import Sale
from .purchase import Purchase, PurchaseItem, PurchasePayment
from .invoice import Invoice
from .invoice_payment import InvoicePayment
from .payment import Payment
from .debt import Debt
from .debt_payment import DebtPayment
from .refund import Refund
from .order import Order, OrderStatus, PaymentStatus

# =========================
# 6. MODÈLES DE CRÉDIT FOURNISSEUR
# =========================
from .supplier_credit import (
    SupplierCreditConfig,
    SupplierDebt,
    PurchaseCredit,
    ProductCreditItem,
    SaleCreditAllocation,
    SupplierCreditTransaction,
    CreditStatus,
    PaymentFrequency,
    ProductOwnershipStatus
)

# =========================
# 7. TRANSFERTS ET RETOURS
# =========================
from .transfert import ProductTransfer, TransferItem, TransferStatus, TransferType, TransferPriority
from .return_product import Return, ReturnItem

# =========================
# 8. AUTRES MODÈLES
# =========================
from .audit_log import AuditLog
from .user_session import UserSession
from .user_history import UserHistory
from .trash_bin import TrashBin
from .user_expense import UserExpense
from .subscription_code import SubscriptionCode, SubscriptionCodeStatus

__all__ = [
    # Core
    "Tenant",
    "User",
    "Subscription",
    "Pharmacy",
    "UserPharmacy",
    "SyncLog",
    "Branch",
    "Category",
    "Customer",
    
    # Structure
    "Department",
    "Project",
    "UserBranch",
    "BranchSubscription",
    "UserSubscription",
    
    # Finance
    "Cost",
    "Budget",
    "Supplier",
    "CostAllocation",
    "FinancialPeriod",
    "FinancialTransaction",
    "Expense",
    "Capital",
    "CapitalAccount",
    "CapitalTransaction",
    "Turnover",
    "AdjustedCapital",
    
    # Produits
    "Product",
    "ProductStock",
    "StockMovement",
    "InventoryCount",
    "InventoryCountItem",
    "StockAdjustment",
    "StockAdjustmentItem",
    "PhysicalInventory",
    "InventoryItem",
    "InventorySchedule",
    
    # Transactions
    "Sale",
    "Purchase",
    "PurchaseItem",
    "PurchasePayment",
    "Invoice",
    "InvoicePayment",
    "Payment",
    "Debt",
    "DebtPayment",
    "Refund",
    "Order",
    "OrderStatus",
    "PaymentStatus",
    
    # Crédit
    "SupplierCreditConfig",
    "SupplierDebt",
    "PurchaseCredit",
    "ProductCreditItem",
    "SaleCreditAllocation",
    "SupplierCreditTransaction",
    "CreditStatus",
    "PaymentFrequency",
    "ProductOwnershipStatus",
    
    # Transferts
    "ProductTransfer",
    "TransferItem",
    "TransferStatus",
    "TransferType",
    "TransferPriority",
    "Return",
    "ReturnItem",
    
    # Autres
    "AuditLog",
    "UserSession",
    "UserHistory",
    "TrashBin",
    "UserExpense",
    "SubscriptionCode",
    "SubscriptionCodeStatus",
    "PharmacyConfig",  # Si importé ailleurs
]
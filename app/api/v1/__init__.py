# app/api/v1/__init__.py
"""
Router principal de l'API v1
"""
from fastapi import APIRouter

from app.api.v1 import auth, users, tenants, superadmin, subscriptions, orders
from .sales import router as sales_router
from .customers import router as customers_router
from app.api.v1.endpoints.stock import router as stock_router 
from app.api.v1.subscription_codes import router as subscription_codes_router
from app.api.v1 import sales, categories
from app.api.routes import pharmacies
from app.api.v1.endpoints.branches import router as branches_router
from app.api.v1.sync import router as sync_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.endpoints.transfers import router as transfers_router
from app.api.v1.capital import router as capital_router
from app.api.v1.endpoints.expenses import router as expenses_router
from app.api.v1.endpoints.profit import router as profit_router
from app.api.v1.endpoints.admin_sync import router as admin_sync
from app.api.v1.endpoints.returns import router as returns_router
from app.api.v1.endpoints import invoices
from app.api.v1.endpoints.sellers import router as sellers_router
from app.api.routes.cost import router as cost_router
from app.api.v1.endpoints.supplier_credit import router as supplier_credit_router



api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(tenants.router)
api_router.include_router(superadmin.router)
api_router.include_router(subscriptions.router)
api_router.include_router(sales_router)
api_router.include_router(customers_router)
api_router.include_router(stock_router)
api_router.include_router(subscription_codes_router)
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(sales.router, prefix="/sales", tags=["Sales"])
api_router.include_router(profit_router, prefix="/profit", tags=["Bénéfices"])
api_router.include_router(categories.router, prefix="/categories", tags=["Categories"])
api_router.include_router(pharmacies.router, prefix="/pharmacies", tags=["Pharmacies"])
api_router.include_router(sync_router, prefix="/sync", tags=["Synchronization"]) 
api_router.include_router(dashboard_router, prefix="/dashboard",tags=["Dashboard"])
api_router.include_router(orders.router, prefix="/orders", tags=["Orders"])
api_router.include_router(transfers_router, prefix="/transfers", tags=["Transfers"])
api_router.include_router(capital_router)
api_router.include_router(branches_router, prefix="/api/v1")
api_router.include_router(
    expenses_router,
    prefix="/expenses",
    tags=["Dépenses"]
)
api_router.include_router(users.router)
api_router.include_router(returns_router)
api_router.include_router(admin_sync)
api_router.include_router(cost_router)
api_router.include_router(supplier_credit_router)

# Ajouter le router
api_router.include_router(invoices.router, prefix="/invoices", tags=["Factures"])
api_router.include_router(sellers_router, prefix="/users", tags=["Users"])
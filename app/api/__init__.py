# app/api/__init__.py
"""
Configuration des routes API
"""

from fastapi import APIRouter
from app.api.v1 import auth, tenants, users
from app.api.routes import pharmacies
from app.api import main_routes
from app.api.v1.endpoints.stock import router as stock_router

# Router principal
api_router = APIRouter()

# Inclure les routes principales
api_router.include_router(main_routes.router)

# Inclure les routes 
api_router.include_router(auth.router, prefix="/api/v1", tags=["Auth"])
api_router.include_router(pharmacies.router, prefix="/api/routes", tags=["Pharmacies"])
api_router.include_router(tenants.router, prefix="/api/v1", tags=["Tenants"])
api_router.include_router(users.router, prefix="/api/v1", tags=["Users"])
api_router.include_router(stock_router)
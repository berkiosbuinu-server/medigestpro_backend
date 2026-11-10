from __future__ import annotations

import datetime
import logging
from typing import Optional, List, Union, Sequence, cast
from enum import Enum
import gc
import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter, APIRoute
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import text

from app.db.session import engine

# ---------------------------------------------------------------------------
# ROUTERS V1
# ---------------------------------------------------------------------------
from app.api.v1.tenants import router as tenant_router
from app.api.v1.auth import router as auth_router
from app.api.v1.subscriptions import router as subscriptions_router
from app.api.v1.subscription_codes import router as subscription_codes_router
from app.api.v1.payments import router as payment_router
from app.api.v1.superadmin import router as superadmin_router
from app.api.v1.endpoints.admin_sync import router as admin_sync_router
from app.api.v1.sync import router as sync_router
from app.api.v1.sales import router as sales_router
from app.api.v1.customers import router as customers_router
from app.api.v1.reports import router as reports_router
from app.api.v1.payments_saas import router as saas_payments_router
from app.api.v1.endpoints.stock import router as stock_router
from app.api.v1.categories import router as categories_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.endpoints.transfers import router as transfers_router
from app.api.v1.orders import router as orders_router
from app.api.v1.capital import router as capital_router
from app.api.v1.endpoints.expenses import router as expenses_router
from app.api.v1.endpoints.branches import router as branches_router
from app.api.v1.endpoints.profit import router as profit_router
from app.api.v1.endpoints.returns import router as returns_router
from app.api.v1.endpoints.invoices import router as invoices_router
from app.api.v1.endpoints.sellers import router as sellers_router
from app.api.v1.endpoints.supplier_credit import router as supplier_credit_router
from app.api.v3.simple_dashboard import router as dashboard_v3_router
from app.api.v1 import users
from app.api.v1 import session

# ---------------------------------------------------------------------------
# ROUTERS ADMIN / LEGACY
# ---------------------------------------------------------------------------
from app.api.routes.pharmacies import router as pharmacies_router
from app.api.routes.tenants import router as admin_tenants_router
from app.api.routes.inventory import router as inventory_router

# ---------------------------------------------------------------------------
# CORE
# ---------------------------------------------------------------------------
from app.core.startup import init_storage
from app.core.exceptions import setup_exception_handlers
from app.core.config import settings

# ---------------------------------------------------------------------------
# MIDDLEWARES MÉTIER
# ---------------------------------------------------------------------------
from app.middleware.tenant_context import TenantContextMiddleware
from app.middleware.branchless_admin_guard import BranchlessAdminGuardMiddleware
from app.middleware.rate_limit_middleware import RateLimitMiddleware
from app.middleware.auth_middleware import UnifiedAuthSubscriptionMiddleware
from app.middleware.auth_middleware import AuthMiddleware
from app.middleware.middleware import SubscriptionMiddleware
from app.core.middleware import SubscriptionCheckMiddleware


# ===========================================================================
# HELPERS
# ===========================================================================

def utc_iso() -> str:
    """Retourne la date UTC actuelle au format ISO 8601."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def include_router_auto(
    application: FastAPI,
    router: APIRouter,
    *,
    default_prefix: Optional[str] = "/api/v1",
    tags: Optional[Sequence[str]] = None,  # Changé: list[str] → Sequence[str]
) -> None:
    """
    Inclut un routeur en gérant automatiquement son préfixe.
    
    Si le routeur a déjà un préfixe qui commence par /api/v1,
    on l'inclut tel quel. Sinon, on ajoute le préfixe par défaut.
    """
    router_prefix = getattr(router, "prefix", "") or ""

    if router_prefix.startswith("/api/v1"):
        final_prefix = None
    elif default_prefix:
        final_prefix = default_prefix
    else:
        final_prefix = None

    if final_prefix:
        application.include_router(router, prefix=final_prefix, tags=tags)
        logger.info(
            "✅ Router inclus: prefix ajouté=%s | router.prefix=%s",
            final_prefix,
            router_prefix
        )
    else:
        application.include_router(router, tags=tags)
        logger.info(
            "✅ Router inclus sans prefix ajouté | router.prefix=%s",
            router_prefix
        )


# ===========================================================================
# LOGGING
# ===========================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("app").setLevel(logging.INFO)


# ===========================================================================
# APPLICATION
# ===========================================================================

app = FastAPI(
    title="MEDIGEST API",
    description="API pour la gestion des pharmacies MEDIGEST",
    version="1.0.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    default_response_class=JSONResponse,
)


# ===========================================================================
# ÉVÉNEMENT DE DÉMARRAGE
# ===========================================================================

@app.on_event("startup")
async def startup_event():
    """Exécuté au démarrage de l'application."""
    logger.info("🚀 Démarrage de l'application...")

    storage_ready = init_storage()
    if storage_ready:
        logger.info("✅ Stockage initialisé avec succès")
    else:
        logger.warning("⚠️ Problème d'initialisation du stockage")

    logger.info("✅ Application prête")


@app.middleware("http")
async def clean_memory(request: Request, call_next):
    response = await call_next(request)
    gc.collect()  # Force garbage collection après chaque requête
    return response

# ===========================================================================
# GESTIONNAIRES D'EXCEPTIONS
# ===========================================================================

setup_exception_handlers(app)


# ===========================================================================
# MIDDLEWARE FORCE JSON
# ===========================================================================

class ForceJSONMiddleware(BaseHTTPMiddleware):
    """
    Middleware qui force les réponses à être en JSON.
    
    - Ajoute l'en-tête Accept: application/json si absent.
    - Convertit les erreurs HTML en réponses JSON.
    - Ne modifie PAS les requêtes OPTIONS (nécessaires pour CORS preflight).
    """

    async def dispatch(self, request: Request, call_next):
        # ⚠️ Ne pas modifier les requêtes OPTIONS (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Ajouter l'en-tête Accept: application/json par défaut
        if "accept" not in request.headers:
            headers = dict(request.headers)
            headers["accept"] = "application/json"
            from starlette.datastructures import Headers
            # Recréer la requête avec les nouveaux en-têtes
            request._headers = Headers(headers)  # type: ignore

        response = await call_next(request)

        # Convertir les erreurs HTML en JSON
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400 and "text/html" in content_type:
            return JSONResponse(
                status_code=response.status_code,
                content={
                    "detail": f"Erreur {response.status_code}",
                    "path": str(request.url.path),
                    "method": request.method,
                    "fallback": True,
                },
            )

        return response


# ===========================================================================
# MIDDLEWARES — ORDRE CRITIQUE
# ===========================================================================
# L'ordre est IMPORTANT :
#   1. CORS (doit être le PREMIER pour intercepter les requêtes OPTIONS)
#   2. Compression GZip
#   3. ForceJSON
#   4. Sécurité (TrustedHost)
#   5. Middlewares métier (tenant, rate limit, audit, auth, subscription)

# ---------------------------------------------------------------------------
# 0. GARDE « ADMIN SANS BRANCHE » (lecture seule sans abonnement de branche)
# ---------------------------------------------------------------------------
# Ajouté AVANT le CORS : Starlette exécute le dernier middleware ajouté en premier,
# donc celui-ci est placé À L'INTÉRIEUR du CORS. Ainsi ses réponses 403 reçoivent
# les headers CORS et le frontend peut lire l'erreur (au lieu d'une erreur CORS).
app.add_middleware(BranchlessAdminGuardMiddleware)

# ---------------------------------------------------------------------------
# 1. CORS — PREMIER MIDDLEWARE (OBLIGATOIRE)
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://medigestpro.net",
        "https://www.medigestpro.net",
        "https://frontend-medigest.vercel.app",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=600,  # Cache preflight pendant 10 minutes
)

# ---------------------------------------------------------------------------
# 2. COMPRESSION
# ---------------------------------------------------------------------------
app.add_middleware(GZipMiddleware, minimum_size=1000)

# ---------------------------------------------------------------------------
# 3. FORCE JSON
# ---------------------------------------------------------------------------
app.add_middleware(ForceJSONMiddleware)

# ---------------------------------------------------------------------------
# 4. MIDDLEWARES MÉTIER (dans l'ordre d'exécution souhaité)
# ---------------------------------------------------------------------------
app.add_middleware(TenantContextMiddleware)
app.add_middleware(RateLimitMiddleware, request_limit=100, window_seconds=60)
app.add_middleware(UnifiedAuthSubscriptionMiddleware)
app.add_middleware(AuthMiddleware)
#app.add_middleware(SubscriptionCheckMiddleware)
#app.add_middleware(SubscriptionMiddleware)


# ===========================================================================
# ROUTES SYSTÈME
# ===========================================================================

@app.get("/", tags=["System"])
def read_root():
    """Page d'accueil de l'API."""
    return {
        "message": "Bienvenue sur l'API MEDIGEST",
        "version": app.version,
        "docs": "/api/docs",
        "health": "/health",
        "timestamp": utc_iso(),
    }


@app.get("/health", tags=["System"])
def health_check():
    """Vérification de l'état de l'API."""
    return {
        "status": "healthy",
        "service": "medigest-api",
        "version": app.version,
        "timestamp": utc_iso(),
    }


@app.get("/debug/tables", tags=["Debug"])
def debug_tables():
    """Liste les tables de la base de données (debug)."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename;
            """)
        ).fetchall()
    return {"tables": [row[0] for row in rows]}


@app.get("/debug/routes", tags=["Debug"])
def debug_routes():
    """Liste toutes les routes enregistrées (debug)."""
    routes = []
    for route in app.routes:
        # Solution: Utiliser isinstance pour vérifier le type APIRoute
        if isinstance(route, APIRoute):
            methods = sorted(list(route.methods)) if route.methods else []
            routes.append({
                "path": route.path,
                "name": route.name,
                "methods": methods,
            })
        else:
            # Fallback pour les autres types de routes
            routes.append({
                "path": getattr(route, "path", "unknown"),
                "name": getattr(route, "name", None),
                "methods": [],
            })
    return {"routes": routes}


# ===========================================================================
# ROUTES API V1
# ===========================================================================

# Utilitaire avec préfixe automatique
include_router_auto(app, tenant_router)
include_router_auto(app, auth_router)
include_router_auto(app, subscriptions_router)
include_router_auto(app, subscription_codes_router)
include_router_auto(app, payment_router)
include_router_auto(app, superadmin_router)
include_router_auto(app, sales_router, tags=["Ventes"])
include_router_auto(app, customers_router, tags=["Clients"])
include_router_auto(app, reports_router, tags=["Rapports"])
include_router_auto(app, saas_payments_router)
include_router_auto(app, stock_router, tags=["Stock"])
include_router_auto(app, inventory_router, tags=["Inventaire"])
include_router_auto(app, categories_router, tags=["Catégories"])
include_router_auto(app, dashboard_router, tags=["Tableau de bord"])
include_router_auto(app, orders_router, tags=["Commandes"])
include_router_auto(app, transfers_router, tags=["Transferts"])
include_router_auto(app, capital_router, tags=["Capital"])
include_router_auto(app, profit_router, tags=["Bénéfices"])
include_router_auto(app, admin_sync_router, tags=["Synchronisation Admin"])
include_router_auto(app, invoices_router, tags=["Factures"])
include_router_auto(app, returns_router, tags=["Retours"])
include_router_auto(app, subscription_codes_router)

# Routes avec préfixe explicite
app.include_router(session.router, prefix="/api/v1", tags=["Session"])
app.include_router(sync_router, prefix="/api/v1", tags=["Synchronisation"])
app.include_router(sellers_router, prefix="/api/v1/users", tags=["Vendeurs"])
app.include_router(users.router, prefix="/api/v1", tags=["Utilisateurs"])

#Routes pour api\v3
app.include_router(dashboard_v3_router, prefix="/api/v3")

# ===========================================================================
# ROUTES LEGACY / ADMIN
# ===========================================================================
app.include_router(branches_router, prefix="/api/v1/branches", tags=["Succursales"])
app.include_router(expenses_router, prefix="/api/v1/expenses", tags=["Dépenses"])
app.include_router(pharmacies_router, prefix="/api/v1/pharmacies", tags=["Pharmacies"])
include_router_auto(app, admin_tenants_router, default_prefix=None)


# ===========================================================================
# RÉSUMÉ DES ROUTES AU DÉMARRAGE
# ===========================================================================

@app.on_event("startup")
async def log_registered_routes():
    """Affiche toutes les routes enregistrées au démarrage."""
    logger.info("📋 Routes enregistrées :")
    for route in app.routes:
        # Correction: Utiliser isinstance au lieu de hasattr pour APIRoute
        if isinstance(route, APIRoute):
            methods = ", ".join(sorted(route.methods))
            logger.info(f"   {methods:<30} {route.path}")
        elif hasattr(route, "methods") and hasattr(route, "path"):
            # Fallback pour compatibilité
            methods = ", ".join(sorted(route.methods))  # type: ignore
            logger.info(f"   {methods:<30} {route.path}")  # type: ignore

# Supprimer la déclaration dupliquée de logger
# La clé JWT ne doit jamais être journalisée.

if __name__ == "__main__":
    import uvicorn
    # Lisez le port depuis la variable d'environnement PORT
    # Utilisez 80000 comme valeur par défaut pour les tests en local
    port = int(os.environ.get("PORT", 8000))
    # ⚠️ CRUCIAL : le host doit être "0.0.0.0" pour Render
    uvicorn.run(app, host="0.0.0.0", port=port)
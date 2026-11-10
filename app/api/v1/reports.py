"""
app/api/v1/reports.py
Routes API pour la génération de rapports
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
from uuid import UUID
import logging
import json

from app.db.session import get_db
from app.models.user import User
from app.models.tenant import Tenant
from app.schemas.report import (
    SalesReportRequest, InventoryReportRequest, FinancialReportRequest,
    CustomerReportRequest, ReportResponse, ReportType, ExportFormat
)
from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission
from app.services.reporting import ReportService
from app.services.export import ExportService
from app.utils.cache import cache_report, get_cached_report

router = APIRouter(prefix="/reports", tags=["Reports"])
logger = logging.getLogger(__name__)


# ============================================================================
# RAPPORTS DE VENTES
# ============================================================================

@router.post("/sales", response_model=ReportResponse)
@require_permission("report_view")
def generate_sales_report(
    report_data: SalesReportRequest,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Génère un rapport des ventes
    """
    try:
        # Vérifier les dates
        if report_data.start_date > report_data.end_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La date de début doit être antérieure à la date de fin"
            )
        
        # Limiter la période à 1 an maximum
        max_period = timedelta(days=365)
        if (report_data.end_date - report_data.start_date) > max_period:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La période ne peut pas dépasser 1 an"
            )
        
        # Vérifier si le rapport est en cache
        cache_key = f"sales_report:{current_tenant.id}:{report_data.start_date}:{report_data.end_date}:{report_data.group_by}"
        cached_data = get_cached_report(cache_key)
        
        if cached_data and not report_data.force_refresh:
            logger.info(f"Rapport de ventes récupéré du cache: {cache_key}")
            return ReportResponse(
                type=ReportType.SALES,
                period_start=report_data.start_date,
                period_end=report_data.end_date,
                generated_at=datetime.utcnow(),
                data=cached_data
            )
        
        # Générer le rapport
        report_service = ReportService(db)
        report = report_service.generate_sales_report(
            tenant_id=current_tenant.id,
            start_date=report_data.start_date,
            end_date=report_data.end_date,
            group_by=report_data.group_by.value
        )
        
        # Mettre en cache
        cache_report(cache_key, report, ttl=3600)  # Cache pendant 1 heure
        
        # Mettre à jour les stats en arrière-plan si demandé
        if report_data.update_stats:
            background_tasks.add_task(
                report_service.update_daily_sales_stats,
                tenant_id=current_tenant.id,
                target_date=date.today()
            )
        
        logger.info(f"Rapport de ventes généré pour le tenant {current_tenant.id}")
        
        return ReportResponse(
            type=ReportType.SALES,
            period_start=report_data.start_date,
            period_end=report_data.end_date,
            generated_at=datetime.utcnow(),
            data=report
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur lors de la génération du rapport de ventes: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération du rapport de ventes"
        )


@router.get("/sales/daily")
@require_permission("report_view")
def get_daily_sales_report(
    date: date = Query(..., description="Date du rapport (format: YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère le rapport quotidien des ventes
    """
    try:
        report_service = ReportService(db)
        
        report = report_service.generate_sales_report(
            tenant_id=current_tenant.id,
            start_date=date,
            end_date=date,
            group_by="day"
        )
        
        return {
            "date": date.isoformat(),
            "report": report
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du rapport quotidien: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération du rapport quotidien"
        )


@router.get("/sales/top-products")
@require_permission("report_view")
def get_top_products_report(
    limit: int = Query(10, ge=1, le=50, description="Nombre de produits à afficher"),
    days: int = Query(30, ge=1, le=365, description="Nombre de jours à analyser"),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les produits les plus vendus
    """
    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        report_service = ReportService(db)
        
        report = report_service.generate_sales_report(
            tenant_id=current_tenant.id,
            start_date=start_date,
            end_date=end_date,
            group_by="product"
        )
        
        # Limiter le nombre de produits
        if "products" in report and len(report["products"]) > limit:
            report["products"] = report["products"][:limit]
        
        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": days
            },
            "top_products": report.get("products", []),
            "summary": report.get("summary", {})
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des top produits: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération du rapport des top produits"
        )


# ============================================================================
# RAPPORTS D'INVENTAIRE
# ============================================================================

@router.post("/inventory", response_model=ReportResponse)
@require_permission("report_view")
def generate_inventory_report(
    report_data: InventoryReportRequest,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Génère un rapport d'inventaire
    """
    try:
        report_service = ReportService(db)
        
        report = report_service.generate_inventory_report(
            tenant_id=current_tenant.id,
            report_type=report_data.report_type.value,
            include_zero_stock=report_data.include_zero_stock
        )
        
        logger.info(f"Rapport d'inventaire généré pour le tenant {current_tenant.id}")
        
        return ReportResponse(
            type=ReportType.INVENTORY,
            period_start=date.today(),
            period_end=date.today(),
            generated_at=datetime.utcnow(),
            data=report
        )
        
    except Exception as e:
        logger.error(f"Erreur lors de la génération du rapport d'inventaire: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération du rapport d'inventaire"
        )


@router.get("/inventory/low-stock")
@require_permission("report_view")
def get_low_stock_report(
    threshold: int = Query(10, ge=1, le=100, description="Seuil d'alerte en jours"),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère le rapport des produits en stock faible
    """
    try:
        report_service = ReportService(db)
        
        # Générer un rapport d'inventaire détaillé
        report = report_service.generate_inventory_report(
            tenant_id=current_tenant.id,
            report_type="detailed",
            include_zero_stock=True
        )
        
        # Filtrer les produits en stock faible
        low_stock_products = []
        if "products" in report:
            for product in report["products"]:
                if product.get("stock_status") in ["low_stock", "out_of_stock"]:
                    low_stock_products.append(product)
        
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "threshold_days": threshold,
            "low_stock_count": len(low_stock_products),
            "products": low_stock_products,
            "summary": {
                "total_products": report.get("summary", {}).get("total_products", 0),
                "out_of_stock": report.get("summary", {}).get("stock_status", {}).get("out_of_stock", 0),
                "low_stock": report.get("summary", {}).get("stock_status", {}).get("low_stock", 0)
            }
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du rapport de stock faible: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération du rapport de stock faible"
        )


@router.get("/inventory/expiring")
@require_permission("report_view")
def get_expiring_products_report(
    days: int = Query(30, ge=1, le=365, description="Jours avant expiration"),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère le rapport des produits qui expirent bientôt
    """
    try:
        report_service = ReportService(db)
        
        report = report_service.generate_inventory_report(
            tenant_id=current_tenant.id,
            report_type="expiry",
            include_zero_stock=True
        )
        
        # Filtrer par période si nécessaire
        if days < 30:
            # Filtrer les produits qui expirent dans moins de 'days' jours
            for category in ["expired", "critical", "warning"]:
                if category in report:
                    report[category] = [
                        p for p in report[category]
                        if p.get("days_until", 999) <= days
                    ]
        
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "days_threshold": days,
            "report": report
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du rapport de péremption: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération du rapport de péremption"
        )


@router.get("/inventory/valuation")
@require_permission("report_view")
def get_inventory_valuation_report(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère le rapport d'évaluation de l'inventaire
    """
    try:
        report_service = ReportService(db)
        
        report = report_service.generate_inventory_report(
            tenant_id=current_tenant.id,
            report_type="valuation",
            include_zero_stock=True
        )
        
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "report": report
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du rapport d'évaluation: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération du rapport d'évaluation"
        )


# ============================================================================
# RAPPORTS FINANCIERS
# ============================================================================

@router.post("/financial", response_model=ReportResponse)
@require_permission("report_view")
def generate_financial_report(
    report_data: FinancialReportRequest,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Génère un rapport financier
    """
    try:
        # Vérifier les dates
        if report_data.start_date > report_data.end_date:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La date de début doit être antérieure à la date de fin"
            )
        
        # Limiter la période à 1 an maximum
        max_period = timedelta(days=365)
        if (report_data.end_date - report_data.start_date) > max_period:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La période ne peut pas dépasser 1 an"
            )
        
        report_service = ReportService(db)
        
        report = report_service.generate_financial_report(
            tenant_id=current_tenant.id,
            start_date=report_data.start_date,
            end_date=report_data.end_date
        )
        
        logger.info(f"Rapport financier généré pour le tenant {current_tenant.id}")
        
        return ReportResponse(
            type=ReportType.FINANCIAL,
            period_start=report_data.start_date,
            period_end=report_data.end_date,
            generated_at=datetime.utcnow(),
            data=report
        )
        
    except Exception as e:
        logger.error(f"Erreur lors de la génération du rapport financier: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération du rapport financier"
        )


@router.get("/financial/summary")
@require_permission("report_view")
def get_financial_summary(
    period: str = Query("month", pattern="^(day|week|month|quarter|year)$"),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère un résumé financier
    """
    try:
        today = date.today()
        
        # Déterminer la période
        if period == "day":
            start_date = today
            end_date = today
        elif period == "week":
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=6)
        elif period == "month":
            start_date = date(today.year, today.month, 1)
            end_date = date(today.year, today.month, 1) + timedelta(days=32)
            end_date = date(end_date.year, end_date.month, 1) - timedelta(days=1)
        elif period == "quarter":
            quarter = (today.month - 1) // 3
            start_date = date(today.year, quarter * 3 + 1, 1)
            end_date = date(today.year, quarter * 3 + 4, 1) - timedelta(days=1)
        else:  # year
            start_date = date(today.year, 1, 1)
            end_date = date(today.year, 12, 31)
        
        report_service = ReportService(db)
        
        report = report_service.generate_financial_report(
            tenant_id=current_tenant.id,
            start_date=start_date,
            end_date=end_date
        )
        
        return {
            "period": period,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "summary": report,
            "generated_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du résumé financier: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération du résumé financier"
        )


# ============================================================================
# RAPPORTS CLIENTS (CUSTOMERS)
# ============================================================================

@router.post("/customers", response_model=ReportResponse)
@require_permission("report_view")
def generate_customer_report(
    report_data: CustomerReportRequest,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Génère un rapport clients (customers)
    """
    try:
        from sqlalchemy import func
        from app.models.customer import Customer
        
        # Statistiques clients de base
        total_customers = db.query(func.count(Customer.id)).filter(
            Customer.tenant_id == current_tenant.id,
            Customer.is_active == True
        ).scalar()
        
        customers_with_credit = db.query(func.count(Customer.id)).filter(
            Customer.tenant_id == current_tenant.id,
            Customer.is_active == True,
            Customer.eligible_credit == True
        ).scalar()
        
        blacklisted_customers = db.query(func.count(Customer.id)).filter(
            Customer.tenant_id == current_tenant.id,
            Customer.is_active == True,
            Customer.blacklisted == True
        ).scalar()
        
        total_debt = db.query(func.coalesce(func.sum(Customer.dette_actuelle), 0)).filter(
            Customer.tenant_id == current_tenant.id,
            Customer.is_active == True
        ).scalar()
        
        total_sales = db.query(func.coalesce(func.sum(Customer.total_achats), 0)).filter(
            Customer.tenant_id == current_tenant.id,
            Customer.is_active == True
        ).scalar()
        
        # Clients par type
        customers_by_type = db.query(
            Customer.type_client,
            func.count(Customer.id).label("count")
        ).filter(
            Customer.tenant_id == current_tenant.id,
            Customer.is_active == True
        ).group_by(Customer.type_client).all()
        
        # Top clients par chiffre d'affaires
        top_customers = db.query(Customer).filter(
            Customer.tenant_id == current_tenant.id,
            Customer.is_active == True
        ).order_by(Customer.total_achats.desc()).limit(10).all()
        
        report = {
            "summary": {
                "total_customers": total_customers,
                "customers_with_credit": customers_with_credit,
                "blacklisted_customers": blacklisted_customers,
                "total_debt": float(total_debt),
                "total_sales": float(total_sales),
                "average_sales_per_customer": float(total_sales / total_customers if total_customers > 0 else 0)
            },
            "distribution_by_type": [
                {"type": type_client, "count": count}
                for type_client, count in customers_by_type if type_client
            ],
            "top_customers": [
                {
                    "id": str(customer.id),
                    "nom": customer.nom,
                    "prenom": customer.prenom,
                    "full_name": customer.full_name,
                    "type": customer.type_client,
                    "total_achats": float(customer.total_achats),
                    "nombre_achats": customer.nombre_achats,
                    "dette_actuelle": float(customer.dette_actuelle),
                    "dernier_achat": customer.dernier_achat.isoformat() if customer.dernier_achat else None
                }
                for customer in top_customers
            ]
        }
        
        logger.info(f"Rapport clients généré pour le tenant {current_tenant.id}")
        
        return ReportResponse(
            type=ReportType.CUSTOMERS,
            period_start=date.today(),
            period_end=date.today(),
            generated_at=datetime.utcnow(),
            data=report
        )
        
    except Exception as e:
        logger.error(f"Erreur lors de la génération du rapport clients: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération du rapport clients"
        )


@router.get("/customers/debtors")
@require_permission("report_view")
def get_debtors_report(
    min_debt: float = Query(0, ge=0, description="Dette minimale"),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère le rapport des clients débiteurs
    """
    try:
        from app.models.customer import Customer
        
        debtors = db.query(Customer).filter(
            Customer.tenant_id == current_tenant.id,
            Customer.is_active == True,
            Customer.dette_actuelle > min_debt
        ).order_by(Customer.dette_actuelle.desc()).all()
        
        total_debt = sum(customer.dette_actuelle for customer in debtors)
        
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "min_debt": min_debt,
            "debtor_count": len(debtors),
            "total_debt": float(total_debt),
            "debtors": [
                {
                    "id": str(customer.id),
                    "nom": customer.nom,
                    "prenom": customer.prenom,
                    "full_name": customer.full_name,
                    "telephone": customer.telephone,
                    "dette_actuelle": float(customer.dette_actuelle),
                    "credit_limit": float(customer.credit_limit),
                    "dernier_achat": customer.dernier_achat.isoformat() if customer.dernier_achat else None,
                    "date_dernier_paiement": customer.date_dernier_paiement.isoformat() if customer.date_dernier_paiement else None
                }
                for customer in debtors
            ]
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du rapport des débiteurs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la génération du rapport des débiteurs"
        )


# ============================================================================
# EXPORT DE RAPPORTS
# ============================================================================

@router.post("/export")
@require_permission("report_export")
def export_report(
    report_type: ReportType,
    export_format: ExportFormat = ExportFormat.EXCEL,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Exporte un rapport dans différents formats
    """
    try:
        if background_tasks:
            # Lancer l'export en arrière-plan
            export_service = ExportService(current_tenant)
            
            background_tasks.add_task(
                export_service.export_report,
                report_type=report_type.value,
                export_format=export_format.value,
                start_date=start_date,
                end_date=end_date,
                user_id=current_user.id
            )
            
            return {
                "message": "Export démarré en arrière-plan",
                "report_type": report_type.value,
                "format": export_format.value,
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
                "task_id": f"export_{datetime.utcnow().timestamp()}"
            }
        
        # Pour les petits rapports, export synchrone
        if report_type == ReportType.SALES:
            report_service = ReportService(db)
            report_data = report_service.generate_sales_report(
                tenant_id=current_tenant.id,
                start_date=start_date or date.today() - timedelta(days=30),
                end_date=end_date or date.today(),
                group_by="day"
            )
        elif report_type == ReportType.INVENTORY:
            report_service = ReportService(db)
            report_data = report_service.generate_inventory_report(
                tenant_id=current_tenant.id,
                report_type="detailed",
                include_zero_stock=True
            )
        else:
            report_data = {"message": "Export synchrone non disponible pour ce type de rapport"}
        
        # Convertir en format demandé
        if export_format == ExportFormat.JSON:
            content = json.dumps(report_data, ensure_ascii=False, indent=2)
            content_type = "application/json"
            filename = f"report_{report_type.value}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        else:
            # Pour Excel/PDF, retourner un message d'attente
            return {
                "message": "Les exports Excel et PDF doivent être lancés en arrière-plan",
                "suggestion": "Ajoutez background_tasks à votre requête"
            }
        
        return {
            "filename": filename,
            "content": content,
            "content_type": content_type,
            "size": len(content.encode('utf-8'))
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de l'export du rapport: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'export du rapport"
        )


# ============================================================================
# UTILITAIRES
# ============================================================================

@router.get("/types")
@require_permission("report_view")
def get_report_types():
    """
    Récupère la liste des types de rapports disponibles
    """
    return [
        {
            "id": report_type.value,
            "name": report_type.name,
            "description": {
                ReportType.SALES: "Rapport des ventes par période",
                ReportType.INVENTORY: "État des stocks et inventaire",
                ReportType.FINANCIAL: "Rapport financier et comptable",
                ReportType.CUSTOMERS: "Rapport clients et statistiques",
                ReportType.STOCK_MOVEMENTS: "Mouvements de stock",
                ReportType.PURCHASES: "Rapport des achats (à venir)",
                ReportType.TAX: "Rapport fiscal (à venir)"
            }.get(report_type, "Rapport non spécifié")
        }
        for report_type in ReportType
    ]


@router.get("/available-formats")
@require_permission("report_view")
def get_available_formats():
    """
    Récupère la liste des formats d'export disponibles
    """
    return [
        {
            "id": export_format.value,
            "name": export_format.name,
            "description": {
                ExportFormat.EXCEL: "Fichier Excel (.xlsx)",
                ExportFormat.PDF: "Document PDF",
                ExportFormat.CSV: "Fichier CSV",
                ExportFormat.JSON: "Fichier JSON"
            }.get(export_format, "Format non spécifié")
        }
        for export_format in ExportFormat
    ]


@router.get("/health")
def report_health_check():
    """
    Vérifie l'état du service de rapports
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "services": {
            "sales_reports": "available",
            "inventory_reports": "available",
            "financial_reports": "available",
            "customer_reports": "available",
            "export_service": "available"
        }
    }


@router.get("/test")
def test_reports():
    """
    Route de test pour le module Rapports
    """
    return {
        "message": "Module Rapports opérationnel",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "available_endpoints": [
            "/reports/sales",
            "/reports/inventory",
            "/reports/financial",
            "/reports/customers",
            "/reports/export",
            "/reports/types",
            "/reports/available-formats"
        ]
    }
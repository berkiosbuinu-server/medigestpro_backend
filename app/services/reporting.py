# app/services/reporting.py
import logging
from typing import Dict, List, Any, Optional
from uuid import UUID
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from app.models.sale import Sale, SaleItem
from app.models.product import Product
from app.models.stock_movement import StockMovement
from app.models.customer import Customer
# Supprimer l'import de Purchase qui n'existe pas encore
# from app.models.purchase import Purchase  # Supprimé
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)


class ReportService:
    """Service de génération de rapports"""
    
    def __init__(self, db: Session):
        self.db = db
    
    # =======================
    # Rapports de vente
    # =======================
    
    def generate_sales_report(
        self, 
        tenant_id: UUID,
        start_date: date,
        end_date: date,
        group_by: str = "day"
    ) -> Dict[str, Any]:
        """
        Génère un rapport des ventes
        
        Args:
            tenant_id: ID du tenant
            start_date: Date de début
            end_date: Date de fin
            group_by: Regroupement (day, week, month, product, category, seller)
        
        Returns:
            Rapport des ventes
        """
        # Base query
        query = self.db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.status == "completed",
            func.date(Sale.created_at) >= start_date,
            func.date(Sale.created_at) <= end_date
        )
        
        if group_by == "day":
            return self._sales_by_day(query, start_date, end_date)
        elif group_by == "week":
            return self._sales_by_week(query, start_date, end_date)
        elif group_by == "month":
            return self._sales_by_month(query, start_date, end_date)
        elif group_by == "product":
            return self._sales_by_product(tenant_id, start_date, end_date)
        elif group_by == "category":
            return self._sales_by_category(tenant_id, start_date, end_date)
        elif group_by == "seller":
            return self._sales_by_seller(query, start_date, end_date)
        else:
            return self._sales_summary(query, start_date, end_date)
    
    def _sales_by_day(self, query, start_date: date, end_date: date) -> Dict[str, Any]:
        """Ventes par jour"""
        # Grouper par jour
        daily_sales = query.with_entities(
            func.date(Sale.created_at).label("date"),
            func.count(Sale.id).label("count"),
            func.sum(Sale.total_amount).label("amount"),
            func.sum(Sale.total_tva).label("tva"),
            func.avg(Sale.total_amount).label("average")
        ).group_by(func.date(Sale.created_at)).order_by(func.date(Sale.created_at)).all()
        
        # Remplir les jours manquants
        all_dates = []
        current_date = start_date
        while current_date <= end_date:
            all_dates.append(current_date)
            current_date += timedelta(days=1)
        
        daily_data = {}
        for day in all_dates:
            day_str = day.isoformat()
            daily_data[day_str] = {
                "date": day_str,
                "sales_count": 0,
                "total_amount": 0.0,
                "total_tva": 0.0,
                "average_amount": 0.0
            }
        
        # Remplir avec les données réelles
        for row in daily_sales:
            day_str = row.date.isoformat()
            daily_data[day_str] = {
                "date": day_str,
                "sales_count": row.count or 0,
                "total_amount": float(row.amount or 0),
                "total_tva": float(row.tva or 0),
                "average_amount": float(row.average or 0)
            }
        
        # Calculer les totaux
        total_sales = query.count()
        total_amount = query.with_entities(func.sum(Sale.total_amount)).scalar() or 0
        average_amount = total_amount / total_sales if total_sales > 0 else 0
        
        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": (end_date - start_date).days + 1
            },
            "summary": {
                "total_sales": total_sales,
                "total_amount": float(total_amount),
                "average_sale": float(average_amount),
                "days_with_sales": len([d for d in daily_data.values() if d["sales_count"] > 0])
            },
            "daily_data": list(daily_data.values()),
            "best_day": max(daily_data.values(), key=lambda x: x["total_amount"]) if daily_data else None,
            "worst_day": min(daily_data.values(), key=lambda x: x["total_amount"]) if daily_data else None
        }
    
    def _sales_by_product(self, tenant_id: UUID, start_date: date, end_date: date) -> Dict[str, Any]:
        """Ventes par produit"""
        product_sales = self.db.query(
            SaleItem.product_id,
            SaleItem.product_name,
            SaleItem.product_code,
            func.sum(SaleItem.quantity).label("total_quantity"),
            func.sum(SaleItem.total).label("total_amount"),
            func.avg(SaleItem.unit_price).label("average_price")
        ).join(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.status == "completed",
            func.date(Sale.created_at) >= start_date,
            func.date(Sale.created_at) <= end_date
        ).group_by(
            SaleItem.product_id,
            SaleItem.product_name,
            SaleItem.product_code
        ).order_by(desc("total_quantity")).all()
        
        total_quantity = sum(row.total_quantity or 0 for row in product_sales)
        total_amount = sum(float(row.total_amount or 0) for row in product_sales)
        
        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            },
            "summary": {
                "total_products_sold": len(product_sales),
                "total_quantity": total_quantity,
                "total_amount": float(total_amount)
            },
            "products": [
                {
                    "product_id": str(row.product_id),
                    "product_name": row.product_name,
                    "product_code": row.product_code,
                    "quantity_sold": row.total_quantity or 0,
                    "total_amount": float(row.total_amount or 0),
                    "average_price": float(row.average_price or 0),
                    "percentage_of_total": (row.total_quantity / total_quantity * 100) if total_quantity > 0 else 0
                }
                for row in product_sales
            ]
        }
    
    # =======================
    # Rapports de stock
    # =======================
    
    def generate_inventory_report(
        self, 
        tenant_id: UUID,
        report_type: str = "summary",
        include_zero_stock: bool = False
    ) -> Dict[str, Any]:
        """
        Génère un rapport d'inventaire
        
        Args:
            tenant_id: ID du tenant
            report_type: Type de rapport (summary, detailed, valuation, expiry)
            include_zero_stock: Inclure les produits en rupture de stock
        
        Returns:
            Rapport d'inventaire
        """
        query = self.db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.is_active == True
        )
        
        if not include_zero_stock:
            query = query.filter(Product.quantity > 0)
        
        products = query.all()
        
        if report_type == "summary":
            return self._inventory_summary(products)
        elif report_type == "detailed":
            return self._inventory_detailed(products)
        elif report_type == "valuation":
            return self._inventory_valuation(products)
        elif report_type == "expiry":
            return self._inventory_expiry_report(products)
        else:
            return self._inventory_summary(products)
    
    def _inventory_summary(self, products: List[Product]) -> Dict[str, Any]:
        """Résumé de l'inventaire"""
        if not products:
            return {
                "total_products": 0,
                "total_items": 0,
                "total_purchase_value": 0.0,
                "total_selling_value": 0.0,
                "total_margin": 0.0,
                "average_margin_rate": 0.0,
                "stock_status": {
                    "out_of_stock": 0,
                    "low_stock": 0,
                    "normal": 0,
                    "over_stock": 0
                },
                "expiry_status": {
                    "expired": 0,
                    "critical": 0,
                    "warning": 0,
                    "normal": 0
                }
            }
        
        total_items = sum(p.quantity for p in products)
        total_purchase_value = sum(p.purchase_value for p in products)
        total_selling_value = sum(p.selling_value for p in products)
        total_margin = total_selling_value - total_purchase_value
        average_margin_rate = sum(p.margin_rate for p in products) / len(products)
        
        # Distribution par statut de stock
        stock_status = {
            "out_of_stock": 0,
            "low_stock": 0,
            "normal": 0,
            "over_stock": 0
        }
        
        # Distribution par statut de péremption
        expiry_status = {
            "expired": 0,
            "critical": 0,
            "warning": 0,
            "normal": 0
        }
        
        for product in products:
            stock_status[product.stock_status] = stock_status.get(product.stock_status, 0) + 1
            expiry_status[product.expiry_status] = expiry_status.get(product.expiry_status, 0) + 1
        
        return {
            "total_products": len(products),
            "total_items": total_items,
            "total_purchase_value": float(total_purchase_value),
            "total_selling_value": float(total_selling_value),
            "total_margin": float(total_margin),
            "average_margin_rate": float(average_margin_rate),
            "stock_status": stock_status,
            "expiry_status": expiry_status,
            "category_distribution": self._get_category_distribution(products),
            "supplier_distribution": self._get_supplier_distribution(products)
        }
    
    def _inventory_detailed(self, products: List[Product]) -> Dict[str, Any]:
        """Inventaire détaillé"""
        summary = self._inventory_summary(products)
        
        detailed_products = []
        for product in products:
            detailed_products.append({
                "id": str(product.id),
                "code": product.code,
                "name": product.name,
                "category": product.category,
                "quantity": product.quantity,
                "unit": product.unit,
                "purchase_price": float(product.purchase_price),
                "selling_price": float(product.selling_price),
                "purchase_value": float(product.purchase_value),
                "selling_value": float(product.selling_value),
                "margin_rate": float(product.margin_rate),
                "expiry_date": product.expiry_date.isoformat() if product.expiry_date else None,
                "days_until_expiry": product.days_until_expiry,
                "batch_number": product.batch_number,
                "stock_status": product.stock_status,
                "expiry_status": product.expiry_status,
                "alert_threshold": product.alert_threshold,
                "main_supplier": product.main_supplier,
                "location": product.location,
                "last_movement": self._get_last_movement(product.id)
            })
        
        summary["products"] = detailed_products
        return summary
    
    def _inventory_valuation(self, products: List[Product]) -> Dict[str, Any]:
        """Évaluation de l'inventaire"""
        if not products:
            return {
                "valuation": {
                    "total_purchase": 0.0,
                    "total_selling": 0.0,
                    "total_margin": 0.0,
                    "margin_rate": 0.0
                },
                "by_category": {},
                "by_status": {},
                "top_valuable": [],
                "bottom_valuable": []
            }
        
        # Valeurs totales
        total_purchase = sum(p.purchase_value for p in products)
        total_selling = sum(p.selling_value for p in products)
        total_margin = total_selling - total_purchase
        margin_rate = (total_margin / total_purchase * 100) if total_purchase > 0 else 0
        
        # Par catégorie
        by_category = {}
        for product in products:
            category = product.category or "Non catégorisé"
            if category not in by_category:
                by_category[category] = {
                    "purchase_value": 0.0,
                    "selling_value": 0.0,
                    "margin": 0.0,
                    "product_count": 0
                }
            by_category[category]["purchase_value"] += float(product.purchase_value)
            by_category[category]["selling_value"] += float(product.selling_value)
            by_category[category]["margin"] += float(product.selling_value - product.purchase_value)
            by_category[category]["product_count"] += 1
        
        # Par statut
        by_status = {}
        for product in products:
            status = product.stock_status
            if status not in by_status:
                by_status[status] = {
                    "value": 0.0,
                    "product_count": 0
                }
            by_status[status]["value"] += float(product.selling_value)
            by_status[status]["product_count"] += 1
        
        # Top 10 produits les plus valuables
        sorted_by_value = sorted(products, key=lambda p: p.selling_value, reverse=True)
        top_valuable = [
            {
                "id": str(p.id),
                "name": p.name,
                "quantity": p.quantity,
                "selling_value": float(p.selling_value),
                "percentage": (float(p.selling_value) / float(total_selling)) * 100 if total_selling > 0 else 0
            }
            for p in sorted_by_value[:10]
        ]
        
        # Bottom 10 produits (moins valuables)
        bottom_valuable = [
            {
                "id": str(p.id),
                "name": p.name,
                "quantity": p.quantity,
                "selling_value": float(p.selling_value)
            }
            for p in sorted_by_value[-10:] if p.selling_value > 0
        ]
        
        return {
            "valuation": {
                "total_purchase": float(total_purchase),
                "total_selling": float(total_selling),
                "total_margin": float(total_margin),
                "margin_rate": float(margin_rate)
            },
            "by_category": by_category,
            "by_status": by_status,
            "top_valuable": top_valuable,
            "bottom_valuable": bottom_valuable,
            "abc_analysis": self._perform_abc_analysis(products)
        }
    
    def _inventory_expiry_report(self, products: List[Product]) -> Dict[str, Any]:
        """Rapport de péremption"""
        today = datetime.utcnow().date()
        
        expired = []
        critical = []  # Expire dans 7 jours
        warning = []   # Expire dans 30 jours
        normal = []    # Expire après 30 jours
        
        for product in products:
            if not product.expiry_date:
                continue
            
            days_until = (product.expiry_date - today).days
            
            item = {
                "id": str(product.id),
                "name": product.name,
                "code": product.code,
                "quantity": product.quantity,
                "expiry_date": product.expiry_date.isoformat(),
                "days_until": days_until,
                "selling_value": float(product.selling_value)
            }
            
            if days_until < 0:
                expired.append(item)
            elif days_until <= 7:
                critical.append(item)
            elif days_until <= 30:
                warning.append(item)
            else:
                normal.append(item)
        
        # Trier par date de péremption
        expired.sort(key=lambda x: x["days_until"])
        critical.sort(key=lambda x: x["days_until"])
        warning.sort(key=lambda x: x["days_until"])
        normal.sort(key=lambda x: x["days_until"])
        
        total_expired_value = sum(item["selling_value"] for item in expired)
        total_critical_value = sum(item["selling_value"] for item in critical)
        total_warning_value = sum(item["selling_value"] for item in warning)
        
        return {
            "summary": {
                "expired_count": len(expired),
                "expired_value": total_expired_value,
                "critical_count": len(critical),
                "critical_value": total_critical_value,
                "warning_count": len(warning),
                "warning_value": total_warning_value,
                "normal_count": len(normal),
                "total_at_risk": total_expired_value + total_critical_value + total_warning_value
            },
            "expired": expired,
            "critical": critical,
            "warning": warning,
            "normal": normal[:100]  # Limiter à 100 produits normaux
        }
    
    # =======================
    # Rapports financiers (SANS Purchase pour l'instant)
    # =======================
    
    def generate_financial_report(
        self,
        tenant_id: UUID,
        start_date: date,
        end_date: date
    ) -> Dict[str, Any]:
        """
        Génère un rapport financier SANS les achats pour l'instant
        
        Returns:
            Rapport financier simplifié
        """
        # Ventes uniquement (pas d'achats pour l'instant)
        sales_query = self.db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.status == "completed",
            func.date(Sale.created_at) >= start_date,
            func.date(Sale.created_at) <= end_date
        )
        
        sales_amount = sales_query.with_entities(func.sum(Sale.total_amount)).scalar() or Decimal('0')
        sales_count = sales_query.count()
        sales_tva = sales_query.with_entities(func.sum(Sale.total_tva)).scalar() or Decimal('0')
        sales_net = sales_amount - sales_tva
        
        # Pour l'instant, on ne peut pas calculer les achats
        # purchase_amount = Decimal('0')
        
        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            },
            "sales": {
                "amount": float(sales_amount),
                "count": sales_count,
                "tva": float(sales_tva),
                "net": float(sales_net),
                "daily_average": float(sales_amount / ((end_date - start_date).days + 1))
            },
            "note": "Le module d'achat n'est pas encore implémenté. Les calculs de marge ne sont pas disponibles."
        }
    
    # =======================
    # Méthodes utilitaires
    # =======================
    
    def _get_category_distribution(self, products: List[Product]) -> Dict[str, int]:
        """Distribution par catégorie"""
        distribution = {}
        for product in products:
            category = product.category or "Non catégorisé"
            distribution[category] = distribution.get(category, 0) + 1
        return distribution
    
    def _get_supplier_distribution(self, products: List[Product]) -> Dict[str, int]:
        """Distribution par fournisseur"""
        distribution = {}
        for product in products:
            supplier = product.main_supplier or "Non spécifié"
            distribution[supplier] = distribution.get(supplier, 0) + 1
        return distribution
    
    def _get_last_movement(self, product_id: UUID) -> Optional[Dict[str, Any]]:
        """Récupère le dernier mouvement de stock"""
        movement = self.db.query(StockMovement).filter(
            StockMovement.product_id == product_id
        ).order_by(StockMovement.created_at.desc()).first()
        
        if movement:
            return {
                "date": movement.created_at.isoformat(),
                "type": movement.movement_type,
                "change": float(movement.quantity_change),
                "reason": movement.reason
            }
        return None
    
    def _perform_abc_analysis(self, products: List[Product]) -> Dict[str, Any]:
        """Analyse ABC des produits"""
        if not products:
            return {"category_a": [], "category_b": [], "category_c": []}
        
        # Trier par valeur de vente
        sorted_products = sorted(products, key=lambda p: p.selling_value, reverse=True)
        total_value = sum(p.selling_value for p in products)
        
        cumulative_value = Decimal('0')
        category_a = []  # 80% de la valeur
        category_b = []  # 15% supplémentaires
        category_c = []  # 5% restants
        
        for product in sorted_products:
            cumulative_value += product.selling_value
            percentage = (cumulative_value / total_value * 100) if total_value > 0 else 0
            
            if percentage <= 80:
                category = "A"
            elif percentage <= 95:
                category = "B"
            else:
                category = "C"
            
            if category == "A":
                category_a.append({
                    "id": str(product.id),
                    "name": product.name,
                    "value": float(product.selling_value),
                    "percentage": (float(product.selling_value) / float(total_value)) * 100
                })
            elif category == "B":
                category_b.append({
                    "id": str(product.id),
                    "name": product.name,
                    "value": float(product.selling_value),
                    "percentage": (float(product.selling_value) / float(total_value)) * 100
                })
            else:
                category_c.append({
                    "id": str(product.id),
                    "name": product.name,
                    "value": float(product.selling_value),
                    "percentage": (float(product.selling_value) / float(total_value)) * 100
                })
        
        return {
            "category_a": {
                "count": len(category_a),
                "value": sum(item["value"] for item in category_a),
                "percentage": (sum(item["value"] for item in category_a) / float(total_value)) * 100,
                "products": category_a
            },
            "category_b": {
                "count": len(category_b),
                "value": sum(item["value"] for item in category_b),
                "percentage": (sum(item["value"] for item in category_b) / float(total_value)) * 100,
                "products": category_b
            },
            "category_c": {
                "count": len(category_c),
                "value": sum(item["value"] for item in category_c),
                "percentage": (sum(item["value"] for item in category_c) / float(total_value)) * 100,
                "products": category_c
            }
        }
    
    def _sales_by_week(self, query, start_date: date, end_date: date) -> Dict[str, Any]:
        """Ventes par semaine (implémentation simplifiée)"""
        return self._sales_summary(query, start_date, end_date)
    
    def _sales_by_month(self, query, start_date: date, end_date: date) -> Dict[str, Any]:
        """Ventes par mois (implémentation simplifiée)"""
        return self._sales_summary(query, start_date, end_date)
    
    def _sales_by_category(self, tenant_id: UUID, start_date: date, end_date: date) -> Dict[str, Any]:
        """Ventes par catégorie"""
        # Implémentation simplifiée - nécessite une jointure avec Product
        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            },
            "summary": {
                "note": "Fonctionnalité à implémenter avec jointure Product-Category"
            }
        }
    
    def _sales_by_seller(self, query, start_date: date, end_date: date) -> Dict[str, Any]:
        """Ventes par vendeur"""
        seller_sales = query.with_entities(
            Sale.created_by,
            Sale.seller_name,
            func.count(Sale.id).label("count"),
            func.sum(Sale.total_amount).label("amount"),
            func.avg(Sale.total_amount).label("average")
        ).group_by(
            Sale.created_by,
            Sale.seller_name
        ).order_by(desc("amount")).all()
        
        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            },
            "sellers": [
                {
                    "seller_id": str(row.created_by),
                    "seller_name": row.seller_name,
                    "sales_count": row.count or 0,
                    "total_amount": float(row.amount or 0),
                    "average_sale": float(row.average or 0)
                }
                for row in seller_sales
            ]
        }
    
    def _sales_summary(self, query, start_date: date, end_date: date) -> Dict[str, Any]:
        """Résumé des ventes"""
        total_sales = query.count()
        total_amount = query.with_entities(func.sum(Sale.total_amount)).scalar() or 0
        total_tva = query.with_entities(func.sum(Sale.total_tva)).scalar() or 0
        
        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            },
            "summary": {
                "total_sales": total_sales,
                "total_amount": float(total_amount),
                "total_tva": float(total_tva),
                "average_sale": float(total_amount / total_sales if total_sales > 0 else 0)
            }
        }
    
    # =======================
    # Mise à jour des statistiques
    # =======================
    
    async def update_daily_sales_stats(self, tenant_id: UUID, pharmacy_id: UUID, target_date: date):
        """Met à jour les statistiques quotidiennes des ventes pour une pharmacie"""
        try:
            logger.info(f"Mise à jour stats ventes pour {target_date} - tenant {tenant_id} - pharmacy {pharmacy_id}")
            
            # Calculer les stats pour cette pharmacie spécifique
            sales_today = self.db.query(Sale).filter(
                Sale.tenant_id == tenant_id,
                Sale.pharmacy_id == pharmacy_id,  # Ajouter le filtre par pharmacie
                Sale.status == "completed",
                func.date(Sale.created_at) == target_date
            ).all()
            
            total_amount = sum(sale.total_amount for sale in sales_today)
            total_count = len(sales_today)
            
            stats = {
                "date": target_date.isoformat(),
                "tenant_id": str(tenant_id),
                "pharmacy_id": str(pharmacy_id),  # Ajouter la pharmacie
                "sales_count": total_count,
                "total_amount": float(total_amount),
                "average_amount": float(total_amount / total_count if total_count > 0 else 0),
                "updated_at": datetime.utcnow().isoformat()
            }
            
            logger.info(f"Stats calculées: {stats}")
            
            # Ici vous pourriez sauvegarder dans une table DailySalesStats ou Redis
            
        except Exception as e:
            logger.error(f"Erreur mise à jour stats: {str(e)}")
# app/services/stock.py
from typing import List, Dict, Any, Optional, Tuple
from uuid import UUID
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
import logging

from app.models.product import Product
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

class StockService:
    """Service pour la gestion des stocks"""
    
    def calculate_stock_stats(self, products: List[Product]) -> Dict[str, Any]:
        """Calcule les statistiques de base du stock"""
        if not products:
            return {
                "total_products": 0,
                "total_value_purchase": 0.0,
                "total_value_selling": 0.0,
                "out_of_stock": 0,
                "low_stock": 0,
                "expired_soon": 0
            }
        
        total_purchase_value = sum(p.purchase_value for p in products)
        total_selling_value = sum(p.selling_value for p in products)
        
        out_of_stock = sum(1 for p in products if p.is_out_of_stock)
        low_stock = sum(1 for p in products if p.has_low_stock)
        expired_soon = sum(1 for p in products if p.is_expiring_soon)
        
        return {
            "total_products": len(products),
            "total_value_purchase": float(total_purchase_value),
            "total_value_selling": float(total_selling_value),
            "out_of_stock": out_of_stock,
            "low_stock": low_stock,
            "expired_soon": expired_soon,
            "average_margin_rate": float(sum(p.margin_rate for p in products) / len(products) if products else 0)
        }
    
    def calculate_detailed_stats(self, products: List[Product]) -> Dict[str, Any]:
        """Calcule des statistiques détaillées du stock"""
        if not products:
            return {
                "total_products": 0,
                "total_items": 0,
                "total_purchase_value": 0.0,
                "total_selling_value": 0.0,
                "average_margin_rate": 0.0,
                "out_of_stock_count": 0,
                "low_stock_count": 0,
                "expired_count": 0,
                "expiring_soon_count": 0,
                "category_distribution": {},
                "value_by_category": {}
            }
        
        # Statistiques de base
        stats = self.calculate_stock_stats(products)
        
        # Ajouter des statistiques supplémentaires
        total_items = sum(p.quantity for p in products)
        expired_count = sum(1 for p in products if p.is_expired)
        expiring_soon_count = sum(1 for p in products if p.is_expiring_soon)
        
        # Distribution par catégorie
        category_dist = {}
        value_by_category = {}
        
        for p in products:
            category = p.category or "Non catégorisé"
            category_dist[category] = category_dist.get(category, 0) + 1
            value_by_category[category] = value_by_category.get(category, 0.0) + float(p.selling_value)
        
        return {
            "total_products": stats["total_products"],
            "total_items": total_items,
            "total_purchase_value": stats["total_value_purchase"],
            "total_selling_value": stats["total_value_selling"],
            "average_margin_rate": stats["average_margin_rate"],
            "out_of_stock_count": stats["out_of_stock"],
            "low_stock_count": stats["low_stock"],
            "expired_count": expired_count,
            "expiring_soon_count": expiring_soon_count,
            "category_distribution": category_dist,
            "value_by_category": value_by_category
        }
    
    def merge_products(
        self, 
        products: List[Product], 
        keep_product: Product,
        merge_strategy: str = "average",
        expiry_strategy: str = "most_recent"
    ) -> Dict[str, Any]:
        """Fusionne plusieurs produits en un seul"""
        if len(products) < 2:
            raise ValueError("Au moins 2 produits requis pour la fusion")
        
        # Collecter les données des produits à fusionner
        total_quantity = sum(p.quantity for p in products)
        total_available = sum(p.available_quantity for p in products)
        total_reserved = sum(p.reserved_quantity for p in products)
        
        # Calculer les prix selon la stratégie
        purchase_prices = [float(p.purchase_price) for p in products]
        selling_prices = [float(p.selling_price) for p in products]
        
        if merge_strategy == "average":
            avg_purchase = sum(purchase_prices) / len(purchase_prices)
            avg_selling = sum(selling_prices) / len(selling_prices)
        elif merge_strategy == "max":
            avg_purchase = max(purchase_prices)
            avg_selling = max(selling_prices)
        elif merge_strategy == "min":
            avg_purchase = min(purchase_prices)
            avg_selling = min(selling_prices)
        elif merge_strategy == "first":  # Utiliser les prix du produit à conserver
            avg_purchase = float(keep_product.purchase_price)
            avg_selling = float(keep_product.selling_price)
        else:
            avg_purchase = sum(purchase_prices) / len(purchase_prices)
            avg_selling = sum(selling_prices) / len(selling_prices)
        
        # Déterminer la date de péremption
        expiry_dates = [p.expiry_date for p in products if p.expiry_date]
        merged_expiry = None
        
        if expiry_dates:
            if expiry_strategy == "most_recent":
                merged_expiry = max(expiry_dates)
            elif expiry_strategy == "most_ancient":
                merged_expiry = min(expiry_dates)
            elif expiry_strategy == "none":
                merged_expiry = None
        
        # Mettre à jour le produit à conserver
        keep_product.quantity = total_quantity
        keep_product.available_quantity = total_available
        keep_product.reserved_quantity = total_reserved
        keep_product.purchase_price = avg_purchase
        keep_product.selling_price = avg_selling
        keep_product.expiry_date = merged_expiry
        
        # Fusionner d'autres attributs
        categories = set(p.category for p in products if p.category)
        if categories:
            keep_product.category = ", ".join(categories)
        
        suppliers = set(p.main_supplier for p in products if p.main_supplier)
        if suppliers:
            keep_product.main_supplier = ", ".join(suppliers)
        
        # Mettre à jour les statuts
        keep_product.update_stock_status()
        keep_product.update_expiry_status()
        
        return {
            "merged_products": len(products),
            "total_quantity": total_quantity,
            "purchase_price": avg_purchase,
            "selling_price": avg_selling,
            "expiry_date": merged_expiry.isoformat() if merged_expiry else None,
            "keep_product_id": str(keep_product.id)
        }
    
    def find_duplicate_products(
        self, 
        db: Session, 
        tenant_id: UUID, 
        similarity_threshold: float = 0.8
    ) -> List[Dict[str, Any]]:
        """Recherche les produits potentiellement dupliqués"""
        # Cette implémentation utilise une approche simple par nom
        # Pour une approche plus avancée, utiliser des algorithmes de similarité textuelle
        
        products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.is_active == True
        ).order_by(Product.name).all()
        
        # Regrouper par nom similaire (approche simple)
        name_groups = {}
        for product in products:
            name_lower = product.name.lower().strip()
            
            # Trouver un groupe existant ou créer un nouveau
            found_group = None
            for group_name in name_groups.keys():
                # Vérifier la similarité (approche simple)
                if self._calculate_name_similarity(name_lower, group_name) >= similarity_threshold:
                    found_group = group_name
                    break
            
            if found_group:
                name_groups[found_group].append(product)
            else:
                name_groups[name_lower] = [product]
        
        # Filtrer les groupes avec plus d'un produit
        duplicates = []
        for name, products_in_group in name_groups.items():
            if len(products_in_group) > 1:
                duplicates.append({
                    "group_name": name,
                    "product_count": len(products_in_group),
                    "products": [
                        {
                            "id": str(p.id),
                            "name": p.name,
                            "code": p.code,
                            "quantity": p.quantity,
                            "expiry_date": p.expiry_date.isoformat() if p.expiry_date else None
                        }
                        for p in products_in_group
                    ]
                })
        
        return duplicates
    
    def _calculate_name_similarity(self, name1: str, name2: str) -> float:
        """Calcule la similarité entre deux noms (approche simple)"""
        # Utiliser une approche simple de ratio de caractères communs
        # Pour une approche plus avancée, utiliser difflib.SequenceMatcher
        set1 = set(name1.split())
        set2 = set(name2.split())
        
        if not set1 or not set2:
            return 0.0
        
        intersection = len(set1.intersection(set2))
        union = len(set1.union(set2))
        
        return intersection / union if union > 0 else 0.0
    
    def analyze_stock_value(self, products: List[Product]) -> Dict[str, Any]:
        """Analyse détaillée de la valeur du stock"""
        if not products:
            return {
                "total_value": 0.0,
                "value_by_status": {},
                "top_valuable_products": [],
                "value_distribution": {}
            }
        
        # Valeur totale
        total_purchase_value = sum(p.purchase_value for p in products)
        total_selling_value = sum(p.selling_value for p in products)
        
        # Valeur par statut de stock
        value_by_status = {
            "normal": 0.0,
            "low_stock": 0.0,
            "out_of_stock": 0.0,
            "over_stock": 0.0
        }
        
        for p in products:
            value_by_status[p.stock_status] = value_by_status.get(p.stock_status, 0.0) + float(p.selling_value)
        
        # Top 10 des produits les plus valuables
        sorted_by_value = sorted(products, key=lambda p: p.selling_value, reverse=True)[:10]
        top_valuable = [
            {
                "id": str(p.id),
                "name": p.name,
                "quantity": p.quantity,
                "selling_value": float(p.selling_value),
                "margin_rate": float(p.margin_rate)
            }
            for p in sorted_by_value
        ]
        
        # Distribution de valeur par catégorie
        value_by_category = {}
        for p in products:
            category = p.category or "Non catégorisé"
            value_by_category[category] = value_by_category.get(category, 0.0) + float(p.selling_value)
        
        return {
            "total_purchase_value": float(total_purchase_value),
            "total_selling_value": float(total_selling_value),
            "total_margin": float(total_selling_value - total_purchase_value),
            "average_margin_rate": float(sum(p.margin_rate for p in products) / len(products)),
            "value_by_status": value_by_status,
            "top_valuable_products": top_valuable,
            "value_by_category": value_by_category,
            "product_count": len(products),
            "item_count": sum(p.quantity for p in products)
        }
    
    def perform_abc_analysis(self, products: List[Product]) -> Dict[str, Any]:
        """Effectue une analyse ABC (Pareto) des stocks"""
        if not products:
            return {
                "category_a": [],
                "category_b": [],
                "category_c": [],
                "thresholds": {"a": 80, "b": 95, "c": 100}
            }
        
        # Trier les produits par valeur de vente décroissante
        sorted_products = sorted(products, key=lambda p: p.selling_value, reverse=True)
        
        # Calculer les valeurs cumulées
        total_value = sum(p.selling_value for p in products)
        cumulative_value = 0
        
        category_a = []  # 80% de la valeur
        category_b = []  # 15% supplémentaires
        category_c = []  # 5% restants
        
        for product in sorted_products:
            cumulative_value += float(product.selling_value)
            percentage = (cumulative_value / float(total_value)) * 100 if total_value > 0 else 0
            
            product_info = {
                "id": str(product.id),
                "name": product.name,
                "selling_value": float(product.selling_value),
                "percentage_of_total": (float(product.selling_value) / float(total_value)) * 100 if total_value > 0 else 0,
                "cumulative_percentage": percentage
            }
            
            if percentage <= 80:
                category_a.append(product_info)
            elif percentage <= 95:
                category_b.append(product_info)
            else:
                category_c.append(product_info)
        
        return {
            "category_a": category_a,
            "category_b": category_b,
            "category_c": category_c,
            "thresholds": {"a": 80, "b": 95, "c": 100},
            "summary": {
                "total_products": len(products),
                "total_value": float(total_value),
                "category_a_count": len(category_a),
                "category_b_count": len(category_b),
                "category_c_count": len(category_c),
                "category_a_value": sum(item["selling_value"] for item in category_a),
                "category_b_value": sum(item["selling_value"] for item in category_b),
                "category_c_value": sum(item["selling_value"] for item in category_c)
            }
        }
    
    def generate_stock_report(self, products: List[Product]) -> Dict[str, Any]:
        """Génère un rapport complet du stock"""
        stats = self.calculate_detailed_stats(products)
        value_analysis = self.analyze_stock_value(products)
        abc_analysis = self.perform_abc_analysis(products)
        
        # Alertes
        out_of_stock = [p for p in products if p.is_out_of_stock]
        low_stock = [p for p in products if p.has_low_stock and not p.is_out_of_stock]
        expired = [p for p in products if p.is_expired]
        expiring_soon = [p for p in products if p.is_expiring_soon and not p.is_expired]
        
        return {
            "metadata": {
                "generated_at": datetime.utcnow().isoformat(),
                "product_count": len(products)
            },
            "statistics": stats,
            "value_analysis": value_analysis,
            "abc_analysis": abc_analysis,
            "alerts": {
                "out_of_stock": {
                    "count": len(out_of_stock),
                    "products": [
                        {"id": str(p.id), "name": p.name, "code": p.code}
                        for p in out_of_stock[:20]  # Limiter pour le rapport
                    ]
                },
                "low_stock": {
                    "count": len(low_stock),
                    "products": [
                        {"id": str(p.id), "name": p.name, "quantity": p.quantity, "threshold": p.alert_threshold}
                        for p in low_stock[:20]
                    ]
                },
                "expired": {
                    "count": len(expired),
                    "products": [
                        {"id": str(p.id), "name": p.name, "expiry_date": p.expiry_date.isoformat() if p.expiry_date else None}
                        for p in expired[:20]
                    ]
                },
                "expiring_soon": {
                    "count": len(expiring_soon),
                    "products": [
                        {"id": str(p.id), "name": p.name, "expiry_date": p.expiry_date.isoformat() if p.expiry_date else None, "days_remaining": p.days_until_expiry}
                        for p in expiring_soon[:20]
                    ]
                }
            },
            "recommendations": self._generate_recommendations(products, stats, value_analysis)
        }
    
    def _generate_recommendations(
        self, 
        products: List[Product], 
        stats: Dict[str, Any],
        value_analysis: Dict[str, Any]
    ) -> List[str]:
        """Génère des recommandations basées sur l'analyse du stock"""
        recommendations = []
        
        # Recommandations basées sur les alertes
        if stats["out_of_stock_count"] > 0:
            recommendations.append(
                f"Recommandation: {stats['out_of_stock_count']} produit(s) en rupture de stock. "
                f"Envisagez de réapprovisionner ces produits."
            )
        
        if stats["low_stock_count"] > 0:
            recommendations.append(
                f"Recommandation: {stats['low_stock_count']} produit(s) avec stock critique. "
                f"Vérifiez les niveaux de stock et planifiez les commandes."
            )
        
        if stats["expired_count"] > 0:
            recommendations.append(
                f"Recommandation: {stats['expired_count']} produit(s) périmé(s). "
                f"Retirez ces produits du stock et procédez à leur élimination appropriée."
            )
        
        if stats["expiring_soon_count"] > 0:
            recommendations.append(
                f"Recommandation: {stats['expiring_soon_count']} produit(s) expire(nt) bientôt. "
                f"Envisagez des promotions ou des ventes prioritaires."
            )
        
        # Recommandations basées sur la valeur
        if value_analysis.get("total_margin", 0) < 0:
            recommendations.append(
                "Alerte: La marge totale est négative. Revoyez les prix d'achat et de vente."
            )
        
        # Recommandations basées sur l'analyse ABC
        abc_value = value_analysis.get("value_by_category", {})
        if abc_value:
            top_category = max(abc_value.items(), key=lambda x: x[1])
            if top_category[1] > value_analysis.get("total_selling_value", 0) * 0.5:
                recommendations.append(
                    f"Recommandation: La catégorie '{top_category[0]}' représente plus de 50% de la valeur du stock. "
                    f"Diversifiez les catégories pour réduire les risques."
                )
        
        return recommendations
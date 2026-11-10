from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime, date

from app.models.pharmacy import Pharmacy
from app.models.user import User
from app.models.product import Product
from app.models.sale import Sale
from app.utils.pharmacy_utils import PharmacyCalculator
from app.config.pharmacy_config import PharmacyConfigManager

class PharmacyService:
    """Service de gestion des pharmacies"""
    
    @staticmethod
    def get_pharmacy_stats(
        db: Session,
        pharmacy_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> Dict[str, Any]:
        """Récupère les statistiques d'une pharmacie"""
        # Configuration
        config = PharmacyConfigManager.get_config(pharmacy_id)
        
        # Produits
        products_query = db.query(Product).filter(Product.pharmacy_id == pharmacy_id)
        total_products = products_query.count()
        
        # Produits en rupture
        low_stock_threshold = config["pharmacy"]["low_stock_threshold"]
        low_stock_products = products_query.filter(Product.quantity <= low_stock_threshold).count()
        
        # Ventes
        sales_query = db.query(Sale).filter(Sale.pharmacy_id == pharmacy_id)
        
        if start_date:
            sales_query = sales_query.filter(Sale.sale_date >= start_date)
        if end_date:
            sales_query = sales_query.filter(Sale.sale_date <= end_date)
        
        total_sales = sales_query.count()
        total_revenue = sum(sale.total_amount for sale in sales_query.all() if sale.total_amount)
        
        # Produits expirés
        today = date.today()
        expired_products = products_query.filter(Product.expiry_date < today).count()
        
        return {
            "total_products": total_products,
            "low_stock_products": low_stock_products,
            "expired_products": expired_products,
            "total_sales": total_sales,
            "total_revenue": total_revenue,
            "config": config
        }
    
    @staticmethod
    def get_expiring_products(
        db: Session,
        pharmacy_id: int,
        days_threshold: int = 30
    ) -> List[Dict[str, Any]]:
        """Récupère les produits sur le point d'expirer"""
        today = date.today()
        expiry_date = today.replace(day=today.day + days_threshold)
        
        products = db.query(Product).filter(
            Product.pharmacy_id == pharmacy_id,
            Product.expiry_date >= today,
            Product.expiry_date <= expiry_date
        ).all()
        
        result = []
        for product in products:
            status = PharmacyCalculator.calculate_expiry_status(product.expiry_date, days_threshold)
            result.append({
                "id": product.id,
                "name": product.name,
                "quantity": product.quantity,
                "expiry_date": product.expiry_date,
                "status": status["status"],
                "days_until_expiry": status["days"]
            })
        
        return result
    
    @staticmethod
    def transfer_products(
        db: Session,
        from_pharmacy_id: int,
        to_pharmacy_id: int,
        product_transfers: List[Dict[str, Any]],
        user_id: int
    ) -> Dict[str, Any]:
        """Transfère des produits entre pharmacies"""
        # Vérifier que les deux pharmacies appartiennent au même tenant
        from_pharmacy = db.query(Pharmacy).filter(Pharmacy.id == from_pharmacy_id).first()
        to_pharmacy = db.query(Pharmacy).filter(Pharmacy.id == to_pharmacy_id).first()
        
        if not from_pharmacy or not to_pharmacy:
            raise ValueError("Une des pharmacies n'existe pas")
        
        if from_pharmacy.tenant_id != to_pharmacy.tenant_id:
            raise ValueError("Les pharmacies doivent appartenir au même tenant")
        
        transfers = []
        for transfer in product_transfers:
            product_id = transfer["product_id"]
            quantity = transfer["quantity"]
            
            # Vérifier le stock
            product = db.query(Product).filter(
                Product.id == product_id,
                Product.pharmacy_id == from_pharmacy_id
            ).first()
            
            if not product:
                raise ValueError(f"Produit {product_id} non trouvé dans la pharmacie source")
            
            if product.quantity < quantity:
                raise ValueError(f"Stock insuffisant pour le produit {product.name}")
            
            # Retirer du stock source
            product.quantity -= quantity
            
            # Ajouter au stock destination
            dest_product = db.query(Product).filter(
                Product.sku == product.sku,
                Product.pharmacy_id == to_pharmacy_id
            ).first()
            
            if dest_product:
                dest_product.quantity += quantity
            else:
                # Créer le produit dans la pharmacie destination
                new_product = Product(
                    **{k: v for k, v in product.__dict__.items() if k not in ['_sa_instance_state', 'id']},
                    pharmacy_id=to_pharmacy_id,
                    quantity=quantity
                )
                db.add(new_product)
            
            transfers.append({
                "product_id": product_id,
                "product_name": product.name,
                "quantity": quantity,
                "from_pharmacy": from_pharmacy.name,
                "to_pharmacy": to_pharmacy.name
            })
        
        db.commit()
        
        # TODO: Créer un log d'audit pour le transfert
        
        return {
            "message": f"Transfert de {len(transfers)} produits effectué",
            "transfers": transfers
        }
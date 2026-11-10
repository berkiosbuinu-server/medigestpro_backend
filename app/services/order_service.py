# app/services/order_service.py

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

from app.models.order import Order, OrderStatus, PaymentStatus
from app.models.customer import Customer
from app.schemas.order import OrderCreate, OrderUpdate, OrderResponse

logger = logging.getLogger(__name__)


class OrderService:
    """Service de gestion des commandes"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_order(self, tenant_id: str, order_data: OrderCreate) -> Order:
        """
        Crée une nouvelle commande.
        
        Args:
            tenant_id: Identifiant du tenant
            order_data: Données de la commande
            
        Returns:
            Order: La commande créée
        """
        try:
            # Vérifier si le client existe
            if order_data.customer_id:
                customer = self.db.query(Customer).filter(
                    Customer.id == order_data.customer_id,
                    Customer.tenant_id == tenant_id
                ).first()
                if not customer:
                    raise ValueError(f"Client {order_data.customer_id} non trouvé")
            
            # Créer la commande
            order = Order(
                tenant_id=tenant_id,
                customer_id=order_data.customer_id,
                customer_name=order_data.customer_name,
                customer_email=order_data.customer_email,
                customer_phone=order_data.customer_phone,
                customer_address=order_data.customer_address,
                order_number=order_data.order_number,
                items=[item.dict() for item in order_data.items],
                subtotal=order_data.subtotal,
                tax_amount=order_data.tax_amount,
                shipping_amount=order_data.shipping_amount,
                discount_amount=order_data.discount_amount,
                total_amount=order_data.total_amount,
                status=order_data.status,
                payment_status=order_data.payment_status,
                payment_method=order_data.payment_method,
                shipping_method=order_data.shipping_method,
                notes=order_data.notes,
                metadata=order_data.metadata
            )
            
            self.db.add(order)
            self.db.commit()
            self.db.refresh(order)
            
            logger.info(f"Commande créée: {order.order_number} pour tenant {tenant_id}")
            return order
            
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Erreur lors de la création de la commande: {str(e)}")
            raise
    
    def get_order(self, tenant_id: str, order_id: str) -> Optional[Order]:
        """
        Récupère une commande par son ID.
        
        Args:
            tenant_id: Identifiant du tenant
            order_id: Identifiant de la commande
            
        Returns:
            Order: La commande trouvée ou None
        """
        return self.db.query(Order).filter(
            Order.id == order_id,
            Order.tenant_id == tenant_id,
            Order.deleted_at.is_(None)
        ).first()
    
    def get_order_by_number(self, tenant_id: str, order_number: str) -> Optional[Order]:
        """
        Récupère une commande par son numéro.
        
        Args:
            tenant_id: Identifiant du tenant
            order_number: Numéro de commande
            
        Returns:
            Order: La commande trouvée ou None
        """
        return self.db.query(Order).filter(
            Order.order_number == order_number,
            Order.tenant_id == tenant_id,
            Order.deleted_at.is_(None)
        ).first()
    
    def get_orders(
        self,
        tenant_id: str,
        skip: int = 0,
        limit: int = 100,
        status: Optional[OrderStatus] = None,
        payment_status: Optional[PaymentStatus] = None,
        customer_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> tuple[List[Order], int]:
        """
        Récupère la liste des commandes avec filtres.
        
        Args:
            tenant_id: Identifiant du tenant
            skip: Nombre d'éléments à sauter
            limit: Nombre maximum d'éléments
            status: Filtre par statut
            payment_status: Filtre par statut de paiement
            customer_id: Filtre par client
            start_date: Date de début
            end_date: Date de fin
            
        Returns:
            tuple: (Liste des commandes, nombre total)
        """
        query = self.db.query(Order).filter(
            Order.tenant_id == tenant_id,
            Order.deleted_at.is_(None)
        )
        
        # Appliquer les filtres
        if status:
            query = query.filter(Order.status == status)
        
        if payment_status:
            query = query.filter(Order.payment_status == payment_status)
        
        if customer_id:
            query = query.filter(Order.customer_id == customer_id)
        
        if start_date:
            query = query.filter(Order.created_at >= start_date)
        
        if end_date:
            query = query.filter(Order.created_at <= end_date)
        
        # Compter le total
        total = query.count()
        
        # Récupérer les résultats
        orders = query.order_by(Order.created_at.desc()).offset(skip).limit(limit).all()
        
        return orders, total
    
    def update_order(
        self,
        tenant_id: str,
        order_id: str,
        order_data: OrderUpdate
    ) -> Optional[Order]:
        """
        Met à jour une commande.
        
        Args:
            tenant_id: Identifiant du tenant
            order_id: Identifiant de la commande
            order_data: Données de mise à jour
            
        Returns:
            Order: La commande mise à jour ou None
        """
        try:
            order = self.get_order(tenant_id, order_id)
            if not order:
                return None
            
            # Mettre à jour les champs
            update_data = order_data.dict(exclude_unset=True)
            for field, value in update_data.items():
                if hasattr(order, field):
                    setattr(order, field, value)
            
            order.updated_at = datetime.utcnow()
            
            self.db.commit()
            self.db.refresh(order)
            
            logger.info(f"Commande mise à jour: {order.order_number}")
            return order
            
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Erreur lors de la mise à jour de la commande: {str(e)}")
            raise
    
    def update_order_status(
        self,
        tenant_id: str,
        order_id: str,
        status: OrderStatus,
        notes: Optional[str] = None
    ) -> Optional[Order]:
        """
        Met à jour le statut d'une commande.
        
        Args:
            tenant_id: Identifiant du tenant
            order_id: Identifiant de la commande
            status: Nouveau statut
            notes: Notes optionnelles
            
        Returns:
            Order: La commande mise à jour ou None
        """
        try:
            order = self.get_order(tenant_id, order_id)
            if not order:
                return None
            
            old_status = order.status
            order.status = status
            order.updated_at = datetime.utcnow()
            
            # Ajouter une note
            if notes:
                if order.notes is None:
                    order.notes = []
                order.notes.append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "type": "status_change",
                    "old_status": old_status.value if old_status else None,
                    "new_status": status.value,
                    "notes": notes
                })
            
            # Si la commande est livrée, enregistrer la date
            if status == OrderStatus.DELIVERED and not order.delivered_at:
                order.delivered_at = datetime.utcnow()
            
            self.db.commit()
            self.db.refresh(order)
            
            logger.info(f"Statut de la commande {order.order_number} changé: {old_status} -> {status}")
            return order
            
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Erreur lors de la mise à jour du statut: {str(e)}")
            raise
    
    def update_payment_status(
        self,
        tenant_id: str,
        order_id: str,
        payment_status: PaymentStatus,
        payment_id: Optional[str] = None,
        notes: Optional[str] = None
    ) -> Optional[Order]:
        """
        Met à jour le statut de paiement d'une commande.
        
        Args:
            tenant_id: Identifiant du tenant
            order_id: Identifiant de la commande
            payment_status: Nouveau statut de paiement
            payment_id: ID de transaction
            notes: Notes optionnelles
            
        Returns:
            Order: La commande mise à jour ou None
        """
        try:
            order = self.get_order(tenant_id, order_id)
            if not order:
                return None
            
            old_status = order.payment_status
            order.payment_status = payment_status
            order.updated_at = datetime.utcnow()
            
            if payment_id:
                order.payment_id = payment_id
            
            # Si le paiement est effectué, enregistrer la date
            if payment_status == PaymentStatus.PAID and not order.paid_at:
                order.paid_at = datetime.utcnow()
            
            # Ajouter une note
            if notes:
                if order.notes is None:
                    order.notes = []
                order.notes.append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "type": "payment_status_change",
                    "old_status": old_status.value if old_status else None,
                    "new_status": payment_status.value,
                    "payment_id": payment_id,
                    "notes": notes
                })
            
            self.db.commit()
            self.db.refresh(order)
            
            logger.info(f"Statut de paiement de la commande {order.order_number} changé: {old_status} -> {payment_status}")
            return order
            
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Erreur lors de la mise à jour du statut de paiement: {str(e)}")
            raise
    
    def delete_order(self, tenant_id: str, order_id: str, soft_delete: bool = True) -> bool:
        """
        Supprime une commande.
        
        Args:
            tenant_id: Identifiant du tenant
            order_id: Identifiant de la commande
            soft_delete: Si True, soft delete; sinon, suppression définitive
            
        Returns:
            bool: True si supprimé, False sinon
        """
        try:
            order = self.get_order(tenant_id, order_id)
            if not order:
                return False
            
            if soft_delete:
                order.deleted_at = datetime.utcnow()
                self.db.commit()
                logger.info(f"Commande soft deleted: {order.order_number}")
            else:
                self.db.delete(order)
                self.db.commit()
                logger.info(f"Commande définitivement supprimée: {order.order_number}")
            
            return True
            
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Erreur lors de la suppression de la commande: {str(e)}")
            raise
    
    def get_order_stats(self, tenant_id: str) -> Dict[str, Any]:
        """
        Récupère les statistiques des commandes.
        
        Args:
            tenant_id: Identifiant du tenant
            
        Returns:
            Dict: Statistiques
        """
        from sqlalchemy import func
        
        stats = {
            "total_orders": 0,
            "total_revenue": 0,
            "average_order_value": 0,
            "by_status": {},
            "by_payment_status": {}
        }
        
        # Total des commandes et revenus
        result = self.db.query(
            func.count(Order.id).label('total_orders'),
            func.sum(Order.total_amount).label('total_revenue')
        ).filter(
            Order.tenant_id == tenant_id,
            Order.deleted_at.is_(None)
        ).first()
        
        stats["total_orders"] = result.total_orders or 0
        stats["total_revenue"] = result.total_revenue or 0.0
        
        if stats["total_orders"] > 0:
            stats["average_order_value"] = stats["total_revenue"] / stats["total_orders"]
        
        # Commandes par statut
        status_counts = self.db.query(
            Order.status,
            func.count(Order.id).label('count')
        ).filter(
            Order.tenant_id == tenant_id,
            Order.deleted_at.is_(None)
        ).group_by(Order.status).all()
        
        for status, count in status_counts:
            stats["by_status"][status.value] = count
        
        # Commandes par statut de paiement
        payment_counts = self.db.query(
            Order.payment_status,
            func.count(Order.id).label('count')
        ).filter(
            Order.tenant_id == tenant_id,
            Order.deleted_at.is_(None)
        ).group_by(Order.payment_status).all()
        
        for status, count in payment_counts:
            stats["by_payment_status"][status.value] = count
        
        return stats
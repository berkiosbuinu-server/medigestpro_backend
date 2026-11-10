# app/models/transfer.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey, Numeric, Enum, Boolean, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base import Base
import enum


class TransferStatus(enum.Enum):
    """Statuts possibles pour un transfert"""
    PENDING = "pending"          # En attente d'approbation
    APPROVED = "approved"        # Approuvé, en préparation
    IN_TRANSIT = "in_transit"    # En cours de livraison
    COMPLETED = "completed"      # Terminé
    REJECTED = "rejected"        # Rejeté
    CANCELLED = "cancelled"      # Annulé
    PARTIALLY_RECEIVED = "partially_received"  # Partiellement reçu


class TransferType(enum.Enum):
    """Types de transfert"""
    INTERNAL = "internal"        # Entre pharmacies du même tenant
    EXTERNAL = "external"        # Vers une autre pharmacie (différent tenant)
    RETOUR = "retour"            # Retour de produit


class TransferPriority(enum.Enum):
    """Priorité du transfert"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class ProductTransfer(Base):
    """
    Modèle pour gérer les transferts de produits entre pharmacies
    Version complète avec toutes les fonctionnalités
    """
    __tablename__ = "product_transfers"
    
    # =====================================
    # IDENTIFIANT UNIQUE
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # =====================================
    # PHARMACIES SOURCE ET DESTINATION
    # =====================================
    source_pharmacy_id = Column(UUID(as_uuid=True), ForeignKey('pharmacies.id'), nullable=False, index=True)
    destination_pharmacy_id = Column(UUID(as_uuid=True), ForeignKey('pharmacies.id'), nullable=False, index=True)
    
    # =====================================
    # INFORMATION DU TRANSFERT
    # =====================================
    transfer_number = Column(String(50), unique=True, nullable=False, index=True)
    transfer_type = Column(Enum(TransferType), default=TransferType.INTERNAL)
    status = Column(Enum(TransferStatus), default=TransferStatus.PENDING)
    priority = Column(Enum(TransferPriority), default=TransferPriority.MEDIUM)
    
    # =====================================
    # DATES
    # =====================================
    requested_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    approved_date = Column(DateTime, nullable=True)
    prepared_date = Column(DateTime, nullable=True)           # Date de préparation
    shipped_date = Column(DateTime, nullable=True)            # Date d'expédition
    completed_date = Column(DateTime, nullable=True)          # Date de réception complète
    cancelled_date = Column(DateTime, nullable=True)          # Date d'annulation
    expected_delivery_date = Column(DateTime, nullable=True)  # Date de livraison prévue
    actual_delivery_date = Column(DateTime, nullable=True)    # Date de livraison réelle
    
    # =====================================
    # INFORMATIONS SUPPLÉMENTAIRES
    # =====================================
    reason = Column(Text, nullable=True)                      # Raison du transfert
    notes = Column(Text, nullable=True)                       # Notes générales
    tracking_number = Column(String(100), nullable=True)      # Numéro de suivi (si livraison externe)
    shipping_cost = Column(Numeric(12, 2), default=0.0)       # Frais de livraison
    
    # =====================================
    # UTILISATEURS
    # =====================================
    requested_by_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    prepared_by_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    shipped_by_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    received_by_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    cancelled_by_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    
    # =====================================
    # STATISTIQUES
    # =====================================
    total_items = Column(Integer, default=0)                   # Nombre de produits distincts
    total_quantity_requested = Column(Integer, default=0)      # Quantité totale demandée
    total_quantity_transferred = Column(Integer, default=0)    # Quantité totale transférée
    total_quantity_received = Column(Integer, default=0)       # Quantité totale reçue
    total_value = Column(Numeric(12, 2), default=0.0)          # Valeur totale du transfert
    
    # =====================================
    # INDICATEURS
    # =====================================
    is_urgent = Column(Boolean, default=False)                 # Transfert urgent
    is_completed = Column(Boolean, default=False)              # Transfert complété
    has_discrepancy = Column(Boolean, default=False)           # Différence entre quantité envoyée/reçue
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", back_populates="transfers")
    source_pharmacy = relationship("Pharmacy", foreign_keys=[source_pharmacy_id])
    destination_pharmacy = relationship("Pharmacy", foreign_keys=[destination_pharmacy_id])
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])
    prepared_by = relationship("User", foreign_keys=[prepared_by_id])
    shipped_by = relationship("User", foreign_keys=[shipped_by_id])
    received_by = relationship("User", foreign_keys=[received_by_id])
    cancelled_by = relationship("User", foreign_keys=[cancelled_by_id])
    
    # Relation avec les items du transfert
    items = relationship("TransferItem", back_populates="transfer", cascade="all, delete-orphan")
    
    # =====================================
    # MÉTHODES UTILITAIRES
    # =====================================
    
    def update_statistics(self):
        """Met à jour les statistiques du transfert"""
        if not self.items:
            return
        
        self.total_items = len(self.items)
        self.total_quantity_requested = sum(item.requested_quantity for item in self.items)
        self.total_quantity_transferred = sum(item.transferred_quantity or 0 for item in self.items)
        self.total_quantity_received = sum(item.received_quantity or 0 for item in self.items)
        self.total_value = sum(item.total_price for item in self.items)
        
        # Vérifier les écarts
        self.has_discrepancy = self.total_quantity_transferred != self.total_quantity_received
        
        # Vérifier si complet
        all_received = all(
            item.received_quantity == item.requested_quantity 
            for item in self.items
        )
        
        if all_received and self.status not in [TransferStatus.COMPLETED, TransferStatus.CANCELLED]:
            self.status = TransferStatus.COMPLETED
            self.is_completed = True
            self.completed_date = datetime.utcnow()
    
    def can_approve(self):
        """Vérifie si le transfert peut être approuvé"""
        return self.status == TransferStatus.PENDING
    
    def can_prepare(self):
        """Vérifie si le transfert peut être préparé"""
        return self.status == TransferStatus.APPROVED
    
    def can_ship(self):
        """Vérifie si le transfert peut être expédié"""
        return self.status in [TransferStatus.APPROVED, TransferStatus.IN_TRANSIT]
    
    def can_receive(self):
        """Vérifie si le transfert peut être reçu"""
        return self.status in [TransferStatus.IN_TRANSIT, TransferStatus.PARTIALLY_RECEIVED]
    
    def approve(self, user_id, notes=None):
        """Approuver le transfert"""
        if not self.can_approve():
            raise ValueError(f"Impossible d'approuver un transfert avec le statut {self.status.value}")
        
        self.status = TransferStatus.APPROVED
        self.approved_by_id = user_id
        self.approved_date = datetime.utcnow()
        if notes:
            self.notes = notes
        
        self.updated_at = datetime.utcnow()
    
    def prepare(self, user_id):
        """Préparer le transfert"""
        if not self.can_prepare():
            raise ValueError(f"Impossible de préparer un transfert avec le statut {self.status.value}")
        
        self.prepared_by_id = user_id
        self.prepared_date = datetime.utcnow()
        self.updated_at = datetime.utcnow()
    
    def ship(self, user_id, tracking_number=None):
        """Expédier le transfert"""
        if not self.can_ship():
            raise ValueError(f"Impossible d'expédier un transfert avec le statut {self.status.value}")
        
        self.status = TransferStatus.IN_TRANSIT
        self.shipped_by_id = user_id
        self.shipped_date = datetime.utcnow()
        if tracking_number:
            self.tracking_number = tracking_number
        
        self.updated_at = datetime.utcnow()
    
    def cancel(self, user_id, reason=None):
        """Annuler le transfert"""
        if self.status in [TransferStatus.COMPLETED, TransferStatus.CANCELLED]:
            raise ValueError(f"Impossible d'annuler un transfert {self.status.value}")
        
        self.status = TransferStatus.CANCELLED
        self.cancelled_by_id = user_id
        self.cancelled_date = datetime.utcnow()
        if reason:
            self.notes = reason
        
        self.updated_at = datetime.utcnow()
    
    def __repr__(self):
        return f"<ProductTransfer {self.transfer_number} ({self.status.value})>"


class TransferItem(Base):
    """
    Modèle pour les items d'un transfert
    """
    __tablename__ = "transfer_items"
    
    # =====================================
    # IDENTIFIANT UNIQUE
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transfer_id = Column(UUID(as_uuid=True), ForeignKey('product_transfers.id', ondelete='CASCADE'), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey('products.id', ondelete='RESTRICT'), nullable=False, index=True)
    
    # =====================================
    # INFORMATIONS DU PRODUIT (dénormalisées)
    # =====================================
    product_code = Column(String(50), nullable=True)
    product_name = Column(String(200), nullable=False)
    batch_number = Column(String(100), nullable=True)
    expiry_date = Column(DateTime, nullable=True)
    
    # =====================================
    # QUANTITÉS
    # =====================================
    requested_quantity = Column(Integer, nullable=False)
    approved_quantity = Column(Integer, nullable=True)
    transferred_quantity = Column(Integer, default=0)
    received_quantity = Column(Integer, default=0)
    
    # =====================================
    # PRIX
    # =====================================
    unit_price = Column(Numeric(12, 2), nullable=False)        # Prix unitaire au moment du transfert
    total_price = Column(Numeric(12, 2), nullable=False)       # Prix total
    
    # =====================================
    # STATUT ET NOTES
    # =====================================
    status = Column(Enum(TransferStatus), default=TransferStatus.PENDING)
    notes = Column(Text, nullable=True)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # =====================================
    # RELATIONS
    # =====================================
    transfer = relationship("ProductTransfer", back_populates="items")
    product = relationship("Product")
    
    # =====================================
    # MÉTHODES UTILITAIRES
    # =====================================
    
    def get_remaining_quantity(self):
        """Retourne la quantité restante à recevoir"""
        return (self.approved_quantity or self.requested_quantity) - self.received_quantity
    
    def is_fully_received(self):
        """Vérifie si l'item a été entièrement reçu"""
        target_quantity = self.approved_quantity or self.requested_quantity
        return self.received_quantity >= target_quantity
    
    def receive(self, quantity, notes=None):
        """Enregistre la réception d'une quantité"""
        target_quantity = self.approved_quantity or self.requested_quantity
        new_received = self.received_quantity + quantity
        
        if new_received > target_quantity:
            raise ValueError(f"La quantité reçue ({new_received}) dépasse la quantité demandée ({target_quantity})")
        
        self.received_quantity = new_received
        
        if self.is_fully_received():
            self.status = TransferStatus.COMPLETED
        
        if notes:
            self.notes = notes
        
        self.updated_at = datetime.utcnow()
    
    def __repr__(self):
        return f"<TransferItem {self.product_name} x{self.requested_quantity}>"
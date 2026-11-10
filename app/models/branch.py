# app/models/branch.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Float
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base


class Branch(Base):
    """
    Modèle pour les succursales/branches d'une pharmacie
    """
    __tablename__ = "branches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    parent_pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    
    # Informations de base
    name = Column(String(255), nullable=False)
    code = Column(String(50), unique=True, nullable=True)
    
    # Localisation
    address = Column(Text, nullable=False)
    city = Column(String(100), nullable=False)
    country = Column(String(100), nullable=False, default="RDC")
    phone = Column(String(50))
    email = Column(String(255))
    
    # Coordonnées GPS
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    
    # Responsable
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    manager_name = Column(String(255), nullable=True)
    
    # Horaires d'ouverture
    opening_hours = Column(JSON, default=lambda: {
        "monday": "08:00-20:00",
        "tuesday": "08:00-20:00",
        "wednesday": "08:00-20:00",
        "thursday": "08:00-20:00",
        "friday": "08:00-20:00",
        "saturday": "09:00-18:00",
        "sunday": "closed"
    })
    
    # Configuration spécifique à la succursale
    config = Column(JSON, default=lambda: {
        "lowStockThreshold": 10,
        "expiryWarningDays": 90,
        "allowNegativeStock": False,
        "enableBatchTracking": True,
        "workingHours": {
            "enabled": True,
            "startTime": "08:00",
            "endTime": "20:00"
        }
    })
    
    # =========================
    # Configuration d'abonnement spécifique à la branche
    # =========================
    subscription_config = Column(JSON, default=lambda: {
        "plan": "essential",  # essential, professional, enterprise
        "max_users": 5,
        "max_products": 2000,
        "max_transactions_per_month": 1000,
        "features": {
            "inventory_management": True,
            "sales": True,
            "reports": True,
            "multi_currency": False,
            "pos_integration": False,
            "api_access": False
        },
        "start_date": None,
        "end_date": None,
        "is_trial": True,
        "trial_ends_at": None
    })
    
    # Configuration opérationnelle (surcharge de la pharmacie)
    operational_config = Column(JSON, default=lambda: {
        "workingHours": None,  # Si None, utilise celui de la pharmacie
        "lowStockThreshold": None,  # Si None, utilise celui de la pharmacie
        "expiryWarningDays": None,
        "allowNegativeStock": None,
        "currencies": None,  # Si None, utilise ceux de la pharmacie
        "taxRate": None,
        "salesType": None
    })
    
    # Métadonnées d'abonnement
    subscription_status = Column(String(50), default="trial")  # trial, active, expired, suspended
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("branch_subscriptions.id", ondelete="SET NULL"), nullable=True)
    
    # Statut
    is_active = Column(Boolean, default=True)
    is_main_branch = Column(Boolean, default=False)
    
    # Métadonnées
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # =========================
    # Relations
    # =========================
    tenant = relationship("Tenant")
    parent_pharmacy = relationship("Pharmacy", back_populates="branches")
    manager = relationship("User", foreign_keys=[manager_id])
    creator = relationship("User", foreign_keys=[created_by])
    users = relationship("User", foreign_keys="User.branch_id", back_populates="branch")
    
    # Relations avec les autres modèles
    products = relationship("Product", back_populates="branch")
    sales = relationship("Sale", back_populates="branch")
    customers = relationship("Customer", back_populates="branch")
    capitals = relationship("Capital", back_populates="branch")
    expenses = relationship("Expense", back_populates="branch", cascade="all, delete-orphan")
    
    # SUPPRIMEZ la relation 'subscription' et gardez uniquement 'branch_subscription'
    # subscription = relationship("BranchSubscription", back_populates="branch", uselist=False)  # À SUPPRIMER
    branch_subscription = relationship("BranchSubscription", back_populates="branch", foreign_keys=[subscription_id], uselist=False)
    
    returns = relationship("Return", back_populates="branch", cascade="all, delete-orphan")
    user_associations = relationship(
        "UserBranch", 
        back_populates="branch", 
        cascade="all, delete-orphan",
        foreign_keys="UserBranch.branch_id"
    )
    
    # =========================
    # Méthodes
    # =========================
    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "code": self.code,
            "address": self.address,
            "city": self.city,
            "phone": self.phone,
            "email": self.email,
            "manager_name": self.manager_name,
            "is_active": self.is_active,
            "is_main_branch": self.is_main_branch,
            "subscription_status": self.subscription_status,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
    
    def get_resolved_config(self, pharmacy_config: dict = None) -> dict:
        """
        Résout la configuration complète en fusionnant :
        - Configuration opérationnelle de la branche (priorité)
        - Configuration de la pharmacie
        - Configuration par défaut de la branche
        """
        resolved = self.config.copy() if self.config else {}
        
        # Appliquer les surcharges opérationnelles
        if self.operational_config:
            for key, value in self.operational_config.items():
                if value is not None:
                    resolved[key] = value
        
        # Si pharmacy_config est fourni, l'utiliser pour les valeurs manquantes
        if pharmacy_config:
            for key, value in pharmacy_config.items():
                if key not in resolved or resolved.get(key) is None:
                    resolved[key] = value
        
        return resolved
    
    def get_working_hours(self, pharmacy_working_hours: dict = None) -> dict:
        """
        Récupère les heures de travail résolues
        """
        # Priorité à la branche
        if self.operational_config and self.operational_config.get("workingHours"):
            return self.operational_config["workingHours"]
        
        if self.config and self.config.get("workingHours"):
            return self.config["workingHours"]
        
        if pharmacy_working_hours:
            return pharmacy_working_hours
        
        return {
            "enabled": True,
            "startTime": "08:00",
            "endTime": "20:00",
            "timezone": "Africa/Kinshasa"
        }
    
    def get_subscription_features(self) -> dict:
        """
        Récupère les fonctionnalités disponibles selon l'abonnement
        """
        if self.branch_subscription and self.branch_subscription.is_active():
            return self.branch_subscription.get_features()
        
        # Utiliser la configuration d'abonnement par défaut
        return self.subscription_config or {
            "plan": "essential",
            "max_users": 5,
            "max_products": 3000,
            "max_transactions_per_month": 1000,
            "features": {
                "inventory_management": True,
                "sales": True,
                "reports": True,
                "multi_currency": False,
                "pos_integration": False,
                "api_access": False
            }
        }
    
    def can_perform_action(self, action: str, current_count: int = 0) -> bool:
        """
        Vérifie si la branche peut effectuer une action selon son abonnement
        """
        features = self.get_subscription_features()
        
        limits = {
            "create_user": ("max_users", current_count),
            "create_product": ("max_products", current_count),
            "create_sale": ("max_transactions_per_month", current_count)
        }
        
        if action in limits:
            limit_key, count = limits[action]
            max_limit = features.get(limit_key, float('inf'))
            return count < max_limit
        
        # Vérifier les fonctionnalités
        feature_map = {
            "use_multi_currency": "multi_currency",
            "use_pos": "pos_integration",
            "use_api": "api_access"
        }
        
        if action in feature_map:
            return features.get("features", {}).get(feature_map[action], False)
        
        return True
    
    def is_subscription_active(self) -> bool:
        """
        Vérifie si l'abonnement de la branche est actif
        """
        if self.branch_subscription:
            return self.branch_subscription.is_active()
        
        # Vérifier par statut et date
        if self.subscription_status == "expired":
            return False
        
        if self.subscription_config and self.subscription_config.get("end_date"):
            end_date = self.subscription_config.get("end_date")
            if isinstance(end_date, str):
                from dateutil import parser
                end_date = parser.parse(end_date)
            if end_date and end_date < datetime.utcnow():
                return False
        
        return self.subscription_status in ["trial", "active"]
    
    def update_subscription_status(self) -> None:
        """
        Met à jour le statut de l'abonnement en fonction des dates
        """
        if self.branch_subscription:
            self.subscription_status = self.branch_subscription.get_status()
        elif self.subscription_config:
            end_date = self.subscription_config.get("end_date")
            if end_date:
                if isinstance(end_date, str):
                    from dateutil import parser
                    end_date = parser.parse(end_date)
                if end_date < datetime.utcnow():
                    self.subscription_status = "expired"
                else:
                    self.subscription_status = "active"
    
    def __repr__(self):
        return f"<Branch {self.name} - {self.city}>"
    
    # Pour accéder directement aux utilisateurs
    @property
    def assigned_users(self):
        """Retourne la liste des utilisateurs assignés à cette branche"""
        return [assoc.user for assoc in self.user_associations if assoc.is_active]
    
    def get_users_with_role(self, role: str):
        """Récupère les utilisateurs ayant un rôle spécifique dans la branche"""
        return [
            assoc.user for assoc in self.user_associations 
            if assoc.is_active and assoc.role_in_branch == role
        ]
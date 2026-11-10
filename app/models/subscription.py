# app/models/subscription.py
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from sqlalchemy import Column, String, DateTime, ForeignKey, DECIMAL, Integer, Boolean, Text, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class SubscriptionPlan(str, Enum):
    """Enumération des plans d'abonnement disponibles"""
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"
    ESSAI = "essai"


class BillingPeriod(str, Enum):
    """Enumération des périodes de facturation"""
    MENSUEL = "mensuel"
    ANNUEL = "annuel"
    TRIMESTRIEL = "trimestriel"


class SubscriptionStatus(str, Enum):
    """Enumération des statuts d'abonnement"""
    ACTIVE = "active"
    PENDING = "pending"  # En attente de paiement
    TRIAL = "trial"  # Période d'essai
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class PaymentStatus(str, Enum):
    """Enumération des statuts de paiement"""
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class PaymentMethod(str, Enum):
    """Enumération des méthodes de paiement"""
    CASH = "cash"
    MOBILE_MONEY = "mobile_money"
    BANK_TRANSFER = "bank_transfer"
    CARD = "card"
    OTHER = "other"


class Subscription(Base):
    """
    Modèle d'abonnement SaaS pour les pharmacies
    Gère les souscriptions, renouvellements et paiements
    """
    __tablename__ = "subscriptions"

    # Identifiants
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True) 
    subscription_code = Column(String(50), unique=True, nullable=False, index=True)
    
    # Informations de l'abonnement
    plan = Column(SQLEnum(SubscriptionPlan), nullable=False, default=SubscriptionPlan.STARTER)
    plan_name = Column(String(100), nullable=False, default="Starter")
    billing_period = Column(SQLEnum(BillingPeriod), nullable=False, default=BillingPeriod.MENSUEL)
    status = Column(SQLEnum(SubscriptionStatus), nullable=False, default=SubscriptionStatus.TRIAL)
    
    # Prix et facturation
    monthly_price = Column(DECIMAL(10, 2), nullable=False, default=Decimal('0.00'))
    annual_price = Column(DECIMAL(10, 2), nullable=False, default=Decimal('0.00'))
    current_price = Column(DECIMAL(10, 2), nullable=False, default=Decimal('0.00'))
    tax_rate = Column(DECIMAL(5, 2), default=Decimal('0.00'))  # Taux de TVA
    discount_percent = Column(DECIMAL(5, 2), default=Decimal('0.00'))  # Remise en pourcentage
    discount_amount = Column(DECIMAL(10, 2), default=Decimal('0.00'))  # Remise fixe
    
    # Périodes
    start_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    end_date = Column(DateTime, nullable=False)
    trial_end_date = Column(DateTime, nullable=True)
    next_billing_date = Column(DateTime, nullable=True)
    cancellation_date = Column(DateTime, nullable=True)
    
    # Limites d'utilisation
    max_users = Column(Integer, nullable=False, default=3)
    max_products = Column(Integer, nullable=True)  # None = illimité
    max_storage_mb = Column(Integer, nullable=False, default=1024)  # 1GB par défaut
    
    # Fonctionnalités incluses
    features = Column(Text, nullable=True)  # JSON string des features incluses
    
    # Métadonnées
    auto_renew = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    meta_data = Column(Text, nullable=True)  # JSON string pour données additionnelles
    
    # Audit
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # Relations
    tenant = relationship("Tenant", back_populates="subscriptions")
    user = relationship("User", foreign_keys=[user_id])
    creator = relationship(
        "User", 
        foreign_keys=[created_by],
        lazy="noload"
    )
    subscription_payments = relationship("SubscriptionPayment", back_populates="subscription", cascade="all, delete-orphan")
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Générer un code d'abonnement unique
        if not self.subscription_code:
            self.subscription_code = f"SUB-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        
        # Calculer la date de fin
        if not self.end_date:
            if self.billing_period == BillingPeriod.MENSUEL:
                self.end_date = self.start_date + timedelta(days=30)
            elif self.billing_period == BillingPeriod.TRIMESTRIEL:
                self.end_date = self.start_date + timedelta(days=90)
            elif self.billing_period == BillingPeriod.ANNUEL:
                self.end_date = self.start_date + timedelta(days=365)
            else:
                self.end_date = self.start_date + timedelta(days=30)  # Par défaut
        
        # Définir la date de fin d'essai (14 jours par défaut)
        if self.status == SubscriptionStatus.TRIAL and not self.trial_end_date:
            self.trial_end_date = self.start_date + timedelta(days=14)
        
        # Définir la prochaine date de facturation
        if not self.next_billing_date:
            self.next_billing_date = self.end_date
        
        # Définir le prix actuel selon la période
        if not self.current_price:
            if self.billing_period == BillingPeriod.MENSUEL:
                self.current_price = self.monthly_price
            elif self.billing_period == BillingPeriod.ANNUEL:
                self.current_price = self.annual_price
            else:
                self.current_price = self.monthly_price
    
    def calculate_total_amount(self) -> Decimal:
        """Calcule le montant total après remise et taxes"""
        base_amount = self.current_price
        
        # Appliquer la remise en pourcentage
        if self.discount_percent > 0:
            discount = (base_amount * self.discount_percent) / Decimal('100')
            base_amount -= discount
        
        # Appliquer la remise fixe
        base_amount -= self.discount_amount
        
        # S'assurer que le montant n'est pas négatif
        if base_amount < 0:
            base_amount = Decimal('0.00')
        
        # Ajouter les taxes
        if self.tax_rate > 0:
            tax_amount = (base_amount * self.tax_rate) / Decimal('100')
            base_amount += tax_amount
        
        return base_amount.quantize(Decimal('0.01'))
    
    def is_active(self) -> bool:
        """Vérifie si l'abonnement est actif"""
        now = datetime.utcnow()
        return (
            self.status == SubscriptionStatus.ACTIVE and
            self.end_date > now
        )
    
    def is_trial(self) -> bool:
        """Vérifie si l'abonnement est en période d'essai"""
        now = datetime.utcnow()
        return (
            self.status == SubscriptionStatus.TRIAL and
            self.trial_end_date and
            self.trial_end_date > now
        )
    
    def days_remaining(self) -> int:
        """Retourne le nombre de jours restants"""
        now = datetime.utcnow()
        if self.end_date < now:
            return 0
        return (self.end_date - now).days
    
    def trial_days_remaining(self) -> int:
        """Retourne le nombre de jours d'essai restants"""
        if not self.trial_end_date:
            return 0
        
        now = datetime.utcnow()
        if self.trial_end_date < now:
            return 0
        return (self.trial_end_date - now).days
    
    def renew(self, period: BillingPeriod = None):
        """Renouvelle l'abonnement"""
        if period:
            self.billing_period = period
        
        # Calculer la nouvelle date de fin
        if self.billing_period == BillingPeriod.MENSUEL:
            self.end_date = datetime.utcnow() + timedelta(days=30)
        elif self.billing_period == BillingPeriod.TRIMESTRIEL:
            self.end_date = datetime.utcnow() + timedelta(days=90)
        elif self.billing_period == BillingPeriod.ANNUEL:
            self.end_date = datetime.utcnow() + timedelta(days=365)
        
        # Définir la prochaine date de facturation
        self.next_billing_date = self.end_date
        
        # Mettre à jour le statut
        self.status = SubscriptionStatus.ACTIVE
        self.updated_at = datetime.utcnow()
    
    def cancel(self):
        """Annule l'abonnement"""
        self.status = SubscriptionStatus.CANCELLED
        self.cancellation_date = datetime.utcnow()
        self.auto_renew = False
        self.updated_at = datetime.utcnow()
    
    def to_dict(self) -> dict:
        """Convertit l'objet en dictionnaire pour l'API"""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "subscription_code": self.subscription_code,
            "plan": self.plan.value,
            "plan_name": self.plan_name,
            "billing_period": self.billing_period.value,
            "status": self.status.value,
            
            "monthly_price": float(self.monthly_price),
            "annual_price": float(self.annual_price),
            "current_price": float(self.current_price),
            "tax_rate": float(self.tax_rate),
            "discount_percent": float(self.discount_percent),
            "discount_amount": float(self.discount_amount),
            "total_amount": float(self.calculate_total_amount()),
            
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "trial_end_date": self.trial_end_date.isoformat() if self.trial_end_date else None,
            "next_billing_date": self.next_billing_date.isoformat() if self.next_billing_date else None,
            "cancellation_date": self.cancellation_date.isoformat() if self.cancellation_date else None,
            
            "max_users": self.max_users,
            "max_products": self.max_products,
            "max_storage_mb": self.max_storage_mb,
            
            "auto_renew": self.auto_renew,
            "days_remaining": self.days_remaining(),
            "trial_days_remaining": self.trial_days_remaining(),
            "is_active": self.is_active(),
            "is_trial": self.is_trial(),
            
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "created_by": str(self.created_by) if self.created_by else None
        }


class SubscriptionPayment(Base):
    """
    Modèle de paiement pour les abonnements
    """
    __tablename__ = "subscription_payments"
    __table_args__ = {"extend_existing": True}

    # Identifiants
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id"), nullable=False, index=True)
    payment_code = Column(String(50), unique=True, nullable=False, index=True)
    
    # Informations de paiement
    amount = Column(DECIMAL(10, 2), nullable=False)
    amount_paid = Column(DECIMAL(10, 2), nullable=False, default=Decimal('0.00'))
    status = Column(SQLEnum(PaymentStatus), nullable=False, default=PaymentStatus.PENDING)
    payment_method = Column(SQLEnum(PaymentMethod), nullable=False)
    payment_reference = Column(String(100), nullable=True)  # Référence bancaire, mobile money, etc.
    
    # Période couverte
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    
    # Métadonnées
    description = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    meta_data = Column(Text, nullable=True)  # JSON string pour données additionnelles
    
    # Audit
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    paid_at = Column(DateTime, nullable=True)
    
    # Relations
    subscription = relationship("Subscription", back_populates="subscription_payments")  # CORRIGÉ : "subscription_payments" (pas "subcription_payments")
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Générer un code de paiement unique
        if not self.payment_code:
            self.payment_code = f"PAY-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    
    def is_complete(self) -> bool:
        """Vérifie si le paiement est complet"""
        return (
            self.status == PaymentStatus.COMPLETED and
            self.amount_paid >= self.amount
        )
    
    def to_dict(self) -> dict:
        """Convertit l'objet en dictionnaire pour l'API"""
        return {
            "id": str(self.id),
            "subscription_id": str(self.subscription_id),
            "payment_code": self.payment_code,
            "amount": float(self.amount),
            "amount_paid": float(self.amount_paid),
            "status": self.status.value,
            "payment_method": self.payment_method.value,
            "payment_reference": self.payment_reference,
            
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            
            "is_complete": self.is_complete(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None
        }


# Fonction utilitaire pour créer des plans prédéfinis
def create_default_plans() -> dict:
    """Retourne la configuration des plans par défaut"""
    return {
        SubscriptionPlan.STARTER: {
            "name": "Starter",
            "monthly_price": Decimal('49.99'),
            "annual_price": Decimal('479.99'),  # ~20% de réduction
            "max_users": 3,
            "max_products": 3000,
            "max_storage_mb": 1024,  # 1GB
            "features": [
                "Gestion des stocks",
                "Ventes & achats",
                "Rapports basiques",
                "Support email",
                "Backup quotidien"
            ]
        },
        SubscriptionPlan.PROFESSIONAL: {
            "name": "Professional",
            "monthly_price": Decimal('89.99'),
            "annual_price": Decimal('899.99'),
            "max_users": 10,
            "max_products": None,  # Illimité
            "max_storage_mb": 5120,  # 5GB
            "features": [
                "Toutes les fonctionnalités Starter",
                "Utilisateurs illimités",
                "Rapports avancés",
                "Inventaire automatique",
                "Support prioritaire",
                "Export des données",
                "Formation en ligne"
            ]
        },
        SubscriptionPlan.ENTERPRISE: {
            "name": "Enterprise",
            "monthly_price": Decimal('149.99'),
            "annual_price": Decimal('1499.99'),
            "max_users": None,  # Illimité
            "max_products": None,  # Illimité
            "max_storage_mb": 10240,  # 10GB
            "features": [
                "Toutes les fonctionnalités Professional",
                "Multi-pharmacies",
                "API d'intégration",
                "Support 24/7",
                "Formation sur site",
                "Migration des données",
                "Analytics avancées",
                "Personnalisation"
            ]
        },
        SubscriptionPlan.ESSAI: {
            "name": "Essai Gratuit",
            "monthly_price": Decimal('0.00'),
            "annual_price": Decimal('0.00'),
            "max_users": 2,
            "max_products": 100,
            "max_storage_mb": 512,  # 0.5GB
            "features": [
                "Toutes les fonctionnalités Starter",
                "Limité à 14 jours",
                "Support email basique"
            ]
        }
    }
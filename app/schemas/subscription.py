# app/schemas/subscription.py
"""
Schémas Pydantic pour la gestion des abonnements.
Définit les structures de données pour les API de souscription.
"""

from pydantic import BaseModel, Field, validator, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from uuid import UUID
from decimal import Decimal
from enum import Enum


# =======================
# ENUMS POUR VALIDATION
# =======================

class SubscriptionPlanEnum(str, Enum):
    """Plans d'abonnement disponibles"""
    TRIAL = "trial"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"
    
    @classmethod
    def _missing_(cls, value):
        """Gestion des valeurs manquantes (compatibilité)"""
        if value == "essai":
            return cls.TRIAL
        return None


class BillingPeriodEnum(str, Enum):
    """Périodes de facturation"""
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    
    @classmethod
    def _missing_(cls, value):
        """Gestion des valeurs manquantes (compatibilité)"""
        if value in ["annual", "annuel", "year"]:
            return cls.YEARLY
        if value in ["mensuel", "mois"]:
            return cls.MONTHLY
        if value in ["trimestriel", "trimestre"]:
            return cls.QUARTERLY
        return None


class SubscriptionStatusEnum(str, Enum):
    """Statuts d'abonnement"""
    ACTIVE = "active"
    PENDING = "pending"
    TRIAL = "trial"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    
    @classmethod
    def _missing_(cls, value):
        """Gestion des valeurs manquantes (compatibilité)"""
        if value == "inactive":
            return cls.EXPIRED
        return None


class PaymentStatusEnum(str, Enum):
    """Statuts de paiement"""
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class PaymentMethodEnum(str, Enum):
    """Méthodes de paiement"""
    CASH = "cash"
    MOBILE_MONEY = "mobile_money"
    BANK_TRANSFER = "bank_transfer"
    CARD = "card"
    OTHER = "other"


# =======================
# SCHÉMAS DE BASE (UTILISÉS DANS subscriptions.py)
# =======================

class PlanResponseSchema(BaseModel):
    """
    Schéma de réponse pour un plan d'abonnement.
    Utilisé dans l'endpoint GET /subscriptions/plans
    """
    id: str
    name: str
    price_monthly: float
    price_yearly: float
    max_users: str
    max_products: str
    max_pharmacies: str
    features: List[str]
    is_trial: bool = False
    
    model_config = ConfigDict(from_attributes=True)


class UpgradeSubscriptionSchema(BaseModel):
    """
    Schéma pour la mise à niveau d'un abonnement.
    Utilisé dans l'endpoint POST /subscriptions/upgrade
    """
    plan: str = Field(..., pattern="^(starter|professional|enterprise)$")
    billing_cycle: str = Field("monthly", pattern="^(monthly|yearly)$")
    payment_id: Optional[str] = None
    payment_method: Optional[str] = None
    reference: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class ManualActivationSchema(BaseModel):
    """
    Schéma pour l'activation manuelle d'un abonnement (super admin).
    Utilisé dans l'endpoint POST /super-admin/subscriptions/manual-activation
    """
    user_id: UUID
    plan: str = Field(..., pattern="^(starter|professional|enterprise)$")
    billing_cycle: str = Field("monthly", pattern="^(monthly|yearly)$")
    payment_method: str = Field(..., pattern="^(cash|bank_transfer|mobile_money)$")
    amount: float = Field(..., gt=0)
    payment_id: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    
    @validator('amount')
    def validate_amount(cls, v, values):
        """
        Vérifie que le montant correspond au plan.
        Cette validation est optionnelle et peut être désactivée.
        """
        try:
            from app.services.subscription_service import PLAN_CONFIG
            plan = values.get('plan')
            if plan and plan in PLAN_CONFIG:
                expected = float(PLAN_CONFIG[plan]['price_monthly'])
                if abs(v - expected) > 0.01:
                    import logging
                    logging.warning(f"Montant {v} différent du prix standard {expected} pour le plan {plan}")
        except ImportError:
            pass
        return v
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionFilterSchema(BaseModel):
    """
    Schéma pour filtrer les abonnements.
    Utilisé dans l'endpoint GET /super-admin/subscriptions
    """
    status: Optional[str] = Field(None, pattern="^(active|expired|trial|all)$")
    plan: Optional[str] = Field(None, pattern="^(trial|starter|professional|enterprise)$")
    tenant_id: Optional[UUID] = None
    search: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionResponseSchema(BaseModel):
    """
    Schéma de réponse pour un abonnement.
    Utilisé dans l'endpoint GET /subscriptions/my-status
    """
    id: UUID
    user_id: UUID
    tenant_id: UUID
    plan_type: str
    plan_name: str
    status: str
    is_active: bool
    days_remaining: int
    start_date: datetime
    end_date: Optional[datetime]
    trial_end_date: Optional[datetime]
    price: float
    currency: str
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionDetailSchema(SubscriptionResponseSchema):
    """
    Schéma détaillé d'un abonnement avec informations utilisateur.
    """
    user_email: Optional[str] = None
    user_name: Optional[str] = None
    tenant_name: Optional[str] = None
    billing_cycle: Optional[str] = None
    auto_renew: Optional[bool] = True
    config: Optional[Dict[str, Any]] = None
    
    model_config = ConfigDict(from_attributes=True)


# =======================
# SCHÉMAS POUR CODES D'ABONNEMENT
# =======================
class SubscriptionCodeCreate(BaseModel):
    """
    Schéma pour la création d'un code d'abonnement.
    Le code est lié à une BRANCHE spécifique.
    """
    plan_type: str = Field(..., description="Type de plan (trial, starter, professional, enterprise)")
    branch_id: UUID = Field(..., description="ID de la branche concernée")
    duration_days: Optional[int] = Field(None, description="Durée en jours (défaut: 30)")
    price: Optional[float] = Field(None, description="Prix personnalisé (en euros)")
    currency: Optional[str] = Field("EUR", description="Devise")
    valid_until: Optional[datetime] = Field(None, description="Date d'expiration du code")
    expiry_days: Optional[int] = Field(90, description="Durée de validité du code en jours")
    notes: Optional[str] = Field(None, description="Notes")
    
    model_config = ConfigDict(from_attributes=True)

class ActivateSubscriptionCode(BaseModel):
    """
    Schéma pour l'activation d'un code d'abonnement.
    """
    code: str = Field(..., description="Code à activer")
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionCodeResponse(BaseModel):
    """
    Schéma de réponse pour un code d'abonnement.
    """
    success: bool
    code: str
    plan_type: str
    plan_name: str
    price: float
    currency: str
    duration_days: int
    valid_until: Optional[datetime]
    created_at: datetime
    status: str
    branch_id: Optional[str] = None
    branch_name: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class ValidateCodeResponse(BaseModel):
    """
    Schéma de réponse pour la validation d'un code.
    """
    valid: bool
    message: str
    status: Optional[str] = None
    valid_until: Optional[str] = None
    plan: Optional[Dict[str, Any]] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    code: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


# =======================
# SCHÉMAS DE BASE (MODÈLES COMPLETS)
# =======================

class SubscriptionBase(BaseModel):
    """Schéma de base pour un abonnement"""
    plan: SubscriptionPlanEnum = Field(default=SubscriptionPlanEnum.TRIAL, description="Plan d'abonnement")
    plan_name: Optional[str] = Field(None, max_length=100, description="Nom du plan affiché")
    billing_period: BillingPeriodEnum = Field(default=BillingPeriodEnum.MONTHLY, description="Période de facturation")
    status: SubscriptionStatusEnum = Field(default=SubscriptionStatusEnum.TRIAL, description="Statut de l'abonnement")
    
    # Prix
    monthly_price: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), description="Prix mensuel")
    yearly_price: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), description="Prix annuel")
    current_price: Optional[Decimal] = Field(None, ge=Decimal('0.00'), description="Prix actuel selon période")
    
    # Taxes et remises
    tax_rate: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), le=Decimal('100.00'), description="Taux de TVA (%)")
    discount_percent: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), le=Decimal('100.00'), description="Remise en pourcentage (%)")
    discount_amount: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), description="Montant de remise fixe")
    
    # Dates
    start_date: Optional[datetime] = Field(None, description="Date de début")
    end_date: Optional[datetime] = Field(None, description="Date de fin")
    trial_end_date: Optional[datetime] = Field(None, description="Fin de la période d'essai")
    next_billing_date: Optional[datetime] = Field(None, description="Prochaine date de facturation")
    cancellation_date: Optional[datetime] = Field(None, description="Date d'annulation")
    
    # Limites
    max_users: int = Field(default=1, ge=1, description="Nombre maximum d'utilisateurs")
    max_products: int = Field(default=100, ge=0, description="Nombre maximum de produits (0 = illimité)")
    max_pharmacies: int = Field(default=1, ge=1, description="Nombre maximum de pharmacies")
    
    # Fonctionnalités
    features: Optional[List[str]] = Field(None, description="Liste des fonctionnalités incluses")
    
    # Configuration
    auto_renew: bool = Field(default=True, description="Renouvellement automatique")
    notes: Optional[str] = Field(None, description="Notes internes")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Métadonnées additionnelles")
    
    model_config = ConfigDict(from_attributes=True)

    @validator('plan_name', pre=True, always=True)
    def set_plan_name(cls, v, values):
        """Définit automatiquement le nom du plan si non fourni"""
        if v is None and 'plan' in values:
            plan = values['plan']
            plan_names = {
                SubscriptionPlanEnum.TRIAL: "Essai gratuit",
                SubscriptionPlanEnum.STARTER: "Starter",
                SubscriptionPlanEnum.PROFESSIONAL: "Professional",
                SubscriptionPlanEnum.ENTERPRISE: "Enterprise"
            }
            return plan_names.get(plan, str(plan.value).title())
        return v
    
    @validator('current_price', pre=True, always=True)
    def set_current_price(cls, v, values):
        """Définit automatiquement le prix actuel selon la période"""
        if v is None:
            billing_period = values.get('billing_period')
            monthly_price = values.get('monthly_price')
            yearly_price = values.get('yearly_price')
            
            if billing_period == BillingPeriodEnum.MONTHLY and monthly_price:
                return monthly_price
            elif billing_period == BillingPeriodEnum.YEARLY and yearly_price:
                return yearly_price
            elif billing_period == BillingPeriodEnum.QUARTERLY and monthly_price:
                return monthly_price * 3
            elif monthly_price:
                return monthly_price
        return v
    
    @validator('end_date')
    def validate_end_date(cls, v, values):
        """Validation de la date de fin"""
        start_date = values.get('start_date')
        if start_date and v and v <= start_date:
            raise ValueError('La date de fin doit être après la date de début')
        return v
    
    @validator('trial_end_date')
    def validate_trial_end_date(cls, v, values):
        """Validation de la date de fin d'essai"""
        start_date = values.get('start_date')
        if start_date and v and v <= start_date:
            raise ValueError('La date de fin d\'essai doit être après la date de début')
        
        if start_date and v:
            trial_days = (v - start_date).days
            if trial_days > 90:
                raise ValueError('La période d\'essai ne peut pas dépasser 90 jours')
        return v


# =======================
# SCHÉMAS DE CRÉATION
# =======================

class SubscriptionCreate(SubscriptionBase):
    """Schéma pour la création d'un nouvel abonnement"""
    tenant_id: UUID = Field(..., description="ID du tenant (pharmacie)")
    user_id: UUID = Field(..., description="ID de l'utilisateur")
    subscription_code: Optional[str] = Field(None, max_length=50, description="Code d'abonnement unique")
    
    @validator('subscription_code', pre=True, always=True)
    def generate_subscription_code(cls, v):
        """Génère un code d'abonnement si non fourni"""
        if v is None:
            import uuid
            date_str = datetime.now().strftime('%Y%m%d')
            unique_part = uuid.uuid4().hex[:8].upper()
            return f"SUB-{date_str}-{unique_part}"
        return v
    
    @validator('start_date', pre=True, always=True)
    def set_start_date(cls, v):
        """Définit la date de début par défaut"""
        if v is None:
            return datetime.now()
        return v


class SubscriptionTrialCreate(BaseModel):
    """Schéma pour créer un abonnement d'essai"""
    user_id: UUID = Field(..., description="ID de l'utilisateur")
    tenant_id: UUID = Field(..., description="ID du tenant")
    trial_days: int = Field(default=14, ge=1, le=90, description="Durée de l'essai en jours")
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionUpgradeRequest(BaseModel):
    """Demande de mise à niveau d'abonnement"""
    new_plan: SubscriptionPlanEnum = Field(..., description="Nouveau plan")
    new_billing_period: Optional[BillingPeriodEnum] = Field(None, description="Nouvelle période de facturation")
    immediate: bool = Field(default=False, description="Appliquer immédiatement")
    pro_rated: bool = Field(default=True, description="Ajustement pro-rata")
    reason: Optional[str] = Field(None, max_length=500, description="Raison de la mise à niveau")
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionRenewalRequest(BaseModel):
    """Demande de renouvellement d'abonnement"""
    billing_period: Optional[BillingPeriodEnum] = Field(None, description="Période de facturation pour le renouvellement")
    auto_renew: Optional[bool] = Field(None, description="Activer/désactiver le renouvellement automatique")
    payment_method: Optional[PaymentMethodEnum] = Field(None, description="Méthode de paiement")
    
    model_config = ConfigDict(from_attributes=True)


# =======================
# SCHÉMAS DE MISE À JOUR
# =======================

class SubscriptionUpdate(BaseModel):
    """Schéma pour mettre à jour un abonnement existant"""
    plan: Optional[SubscriptionPlanEnum] = None
    plan_name: Optional[str] = Field(None, max_length=100)
    billing_period: Optional[BillingPeriodEnum] = None
    status: Optional[SubscriptionStatusEnum] = None
    
    # Prix
    monthly_price: Optional[Decimal] = Field(None, ge=Decimal('0.00'))
    yearly_price: Optional[Decimal] = Field(None, ge=Decimal('0.00'))
    current_price: Optional[Decimal] = Field(None, ge=Decimal('0.00'))
    
    # Taxes et remises
    tax_rate: Optional[Decimal] = Field(None, ge=Decimal('0.00'), le=Decimal('100.00'))
    discount_percent: Optional[Decimal] = Field(None, ge=Decimal('0.00'), le=Decimal('100.00'))
    discount_amount: Optional[Decimal] = Field(None, ge=Decimal('0.00'))
    
    # Dates
    end_date: Optional[datetime] = None
    trial_end_date: Optional[datetime] = None
    next_billing_date: Optional[datetime] = None
    cancellation_date: Optional[datetime] = None
    
    # Limites
    max_users: Optional[int] = Field(None, ge=1)
    max_products: Optional[int] = Field(None, ge=0)
    max_pharmacies: Optional[int] = Field(None, ge=1)
    
    # Configuration
    auto_renew: Optional[bool] = None
    notes: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionStatusUpdate(BaseModel):
    """Mise à jour manuelle du statut"""
    status: SubscriptionStatusEnum = Field(..., description="Nouveau statut")
    reason: str = Field(..., min_length=5, max_length=1000, description="Raison du changement")
    effective_date: Optional[datetime] = Field(None, description="Date d'effet")
    notes: Optional[str] = Field(None, description="Notes additionnelles")
    
    @validator('effective_date')
    def validate_effective_date(cls, v):
        if v and v < datetime.now():
            raise ValueError('La date d\'effet ne peut pas être dans le passé')
        return v
    
    model_config = ConfigDict(from_attributes=True)

# =======================
# SCHÉMAS POUR ABONNEMENT DE BRANCHE
# =======================

class BranchSubscriptionBase(BaseModel):
    """Base pour l'abonnement d'une branche"""
    plan: str = Field(..., description="Plan d'abonnement")
    billing_cycle: str = Field("monthly", description="Cycle de facturation (monthly/yearly)")
    auto_renew: bool = Field(True, description="Renouvellement automatique")
    
    model_config = ConfigDict(from_attributes=True)


class BranchSubscriptionCreate(BranchSubscriptionBase):
    """Création d'un abonnement pour une branche"""
    branch_id: UUID = Field(..., description="ID de la branche")
    duration_days: int = Field(30, ge=1, le=3650, description="Durée en jours")
    payment_method: Optional[str] = Field(None, description="Méthode de paiement")
    payment_reference: Optional[str] = Field(None, description="Référence de paiement")


class BranchSubscriptionResponse(BranchSubscriptionBase):
    """Réponse pour l'abonnement d'une branche"""
    id: UUID
    branch_id: UUID
    branch_name: Optional[str] = None
    tenant_id: UUID
    plan_name: str
    start_date: datetime
    end_date: datetime
    trial_end_date: Optional[datetime] = None
    status: str
    price: float
    currency: str
    max_products: int
    max_users: int
    max_storage_mb: int
    is_active: bool
    is_trial: bool
    days_remaining: int
    trial_days_remaining: int
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class BranchSubscriptionStatusResponse(BaseModel):
    """Statut de l'abonnement pour une branche"""
    branch_id: UUID
    branch_name: str
    has_active_subscription: bool
    plan: Optional[str] = None
    plan_name: Optional[str] = None
    status: Optional[str] = None
    days_remaining: int
    is_trial: bool
    trial_days_remaining: int
    max_users: int
    current_users: int
    max_products: int
    current_products: int
    can_add_users: bool
    can_add_products: bool
    
    model_config = ConfigDict(from_attributes=True)
# =======================
# SCHÉMAS DE RÉPONSE
# =======================

class SubscriptionResponse(SubscriptionBase):
    """Schéma de réponse complet pour un abonnement"""
    id: UUID
    tenant_id: UUID
    user_id: UUID
    subscription_code: str
    
    # Calculs
    total_amount: Decimal = Field(..., ge=Decimal('0.00'), description="Montant total après taxes et remises")
    days_remaining: int = Field(..., ge=0, description="Jours restants avant expiration")
    trial_days_remaining: Optional[int] = Field(None, ge=0, description="Jours d'essai restants")
    is_active: bool = Field(..., description="L'abonnement est-il actif ?")
    is_trial: bool = Field(..., description="Est en période d'essai ?")
    
    # Audit
    created_at: datetime
    updated_at: datetime
    created_by: Optional[UUID] = None
    
    model_config = ConfigDict(from_attributes=True)
    
    @validator('total_amount', pre=True, always=True)
    def calculate_total_amount(cls, v, values):
        """Calcule le montant total automatiquement"""
        if v is None:
            current_price = values.get('current_price', Decimal('0.00'))
            discount_percent = values.get('discount_percent', Decimal('0.00'))
            discount_amount = values.get('discount_amount', Decimal('0.00'))
            tax_rate = values.get('tax_rate', Decimal('0.00'))
            
            if discount_percent > 0:
                discount = (current_price * discount_percent) / Decimal('100')
                current_price -= discount
            
            current_price -= discount_amount
            
            if current_price < 0:
                current_price = Decimal('0.00')
            
            if tax_rate > 0:
                tax_amount = (current_price * tax_rate) / Decimal('100')
                current_price += tax_amount
            
            return current_price.quantize(Decimal('0.01'))
        return v
    
    @validator('days_remaining', pre=True, always=True)
    def calculate_days_remaining(cls, v, values):
        """Calcule les jours restants"""
        if v is None:
            end_date = values.get('end_date')
            if end_date:
                now = datetime.now()
                if end_date > now:
                    return (end_date - now).days
            return 0
        return v
    
    @validator('trial_days_remaining', pre=True, always=True)
    def calculate_trial_days_remaining(cls, v, values):
        """Calcule les jours d'essai restants"""
        if v is None:
            trial_end_date = values.get('trial_end_date')
            status = values.get('status')
            if trial_end_date and status == SubscriptionStatusEnum.TRIAL:
                now = datetime.now()
                if trial_end_date > now:
                    return (trial_end_date - now).days
            return 0
        return v
    
    @validator('is_active', pre=True, always=True)
    def calculate_is_active(cls, v, values):
        """Détermine si l'abonnement est actif"""
        if v is None:
            status = values.get('status')
            end_date = values.get('end_date')
            if end_date:
                return status == SubscriptionStatusEnum.ACTIVE and end_date > datetime.now()
            return status == SubscriptionStatusEnum.ACTIVE
        return v
    
    @validator('is_trial', pre=True, always=True)
    def calculate_is_trial(cls, v, values):
        """Détermine si l'abonnement est en essai"""
        if v is None:
            status = values.get('status')
            trial_end_date = values.get('trial_end_date')
            if trial_end_date:
                return status == SubscriptionStatusEnum.TRIAL and trial_end_date > datetime.now()
            return status == SubscriptionStatusEnum.TRIAL
        return v


class SubscriptionSummaryResponse(BaseModel):
    """Version allégée pour les listes"""
    id: UUID
    subscription_code: str
    tenant_id: UUID
    user_id: UUID
    user_email: Optional[str] = None
    plan: str
    plan_name: str
    billing_period: str
    status: str
    current_price: Decimal
    start_date: datetime
    end_date: datetime
    days_remaining: int
    is_active: bool
    is_trial: bool
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionCreationResponse(BaseModel):
    """Réponse après création d'un abonnement"""
    message: str
    subscription: SubscriptionResponse
    next_steps: List[str] = Field(
        default_factory=lambda: [
            "Abonnement créé avec succès",
            "Période d'essai activée",
            "Limites configurées",
            "Prêt à utiliser"
        ]
    )
    
    model_config = ConfigDict(from_attributes=True)


# =======================
# SCHÉMAS DE PAIEMENT
# =======================

class PaymentBase(BaseModel):
    """Schéma de base pour un paiement"""
    subscription_id: UUID = Field(..., description="ID de l'abonnement")
    amount: Decimal = Field(..., gt=Decimal('0.00'), description="Montant dû")
    amount_paid: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), description="Montant payé")
    status: PaymentStatusEnum = Field(default=PaymentStatusEnum.PENDING, description="Statut du paiement")
    payment_method: PaymentMethodEnum = Field(..., description="Méthode de paiement")
    payment_reference: Optional[str] = Field(None, max_length=100, description="Référence du paiement")
    
    # Période couverte
    period_start: datetime = Field(..., description="Début de la période couverte")
    period_end: datetime = Field(..., description="Fin de la période couverte")
    
    # Métadonnées
    description: Optional[str] = Field(None, description="Description")
    notes: Optional[str] = Field(None, description="Notes")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Métadonnées additionnelles")
    
    model_config = ConfigDict(from_attributes=True)

    @validator('period_end')
    def validate_period_end(cls, v, values):
        """Validation de la période"""
        period_start = values.get('period_start')
        if period_start and v <= period_start:
            raise ValueError('period_end doit être après period_start')
        return v
    
    @validator('amount_paid')
    def validate_amount_paid(cls, v, values):
        """Validation du montant payé"""
        amount = values.get('amount')
        if amount and v > amount:
            raise ValueError('Le montant payé ne peut pas dépasser le montant dû')
        return v


class PaymentCreate(PaymentBase):
    """Schéma pour créer un nouveau paiement"""
    payment_code: Optional[str] = Field(None, max_length=50, description="Code de paiement unique")
    
    @validator('payment_code', pre=True, always=True)
    def generate_payment_code(cls, v):
        """Génère un code de paiement si non fourni"""
        if v is None:
            import uuid
            date_str = datetime.now().strftime('%Y%m%d')
            unique_part = uuid.uuid4().hex[:8].upper()
            return f"PAY-{date_str}-{unique_part}"
        return v


class PaymentUpdate(BaseModel):
    """Schéma pour mettre à jour un paiement"""
    amount_paid: Optional[Decimal] = Field(None, ge=Decimal('0.00'))
    status: Optional[PaymentStatusEnum] = None
    payment_method: Optional[PaymentMethodEnum] = None
    payment_reference: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    notes: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    paid_at: Optional[datetime] = Field(None, description="Date de paiement effectif")
    
    model_config = ConfigDict(from_attributes=True)


class PaymentResponse(PaymentBase):
    """Schéma de réponse complet pour un paiement"""
    id: UUID
    payment_code: str
    
    # Audit
    created_at: datetime
    updated_at: datetime
    paid_at: Optional[datetime] = None
    
    # Calculs
    is_complete: bool = Field(..., description="Le paiement est-il complet ?")
    amount_due: Decimal = Field(..., description="Montant restant dû")
    
    model_config = ConfigDict(from_attributes=True)
    
    @validator('is_complete', pre=True, always=True)
    def calculate_is_complete(cls, v, values):
        """Détermine si le paiement est complet"""
        if v is None:
            status = values.get('status')
            amount = values.get('amount')
            amount_paid = values.get('amount_paid')
            
            return (
                status == PaymentStatusEnum.COMPLETED and
                amount_paid >= amount
            )
        return v
    
    @validator('amount_due', pre=True, always=True)
    def calculate_amount_due(cls, v, values):
        """Calcule le montant restant dû"""
        if v is None:
            amount = values.get('amount', Decimal('0.00'))
            amount_paid = values.get('amount_paid', Decimal('0.00'))
            return (amount - amount_paid).quantize(Decimal('0.01'))
        return v


# =======================
# SCHÉMAS DE RAPPORT
# =======================

class SubscriptionAnalytics(BaseModel):
    """Analytics d'abonnement"""
    total_subscriptions: int = 0
    active_subscriptions: int = 0
    trial_subscriptions: int = 0
    expired_subscriptions: int = 0
    cancelled_subscriptions: int = 0
    
    # Par plan
    by_plan: Dict[str, int] = Field(default_factory=dict)
    
    # Chiffre d'affaires
    monthly_revenue: Decimal = Decimal('0.00')
    annual_revenue: Decimal = Decimal('0.00')
    total_revenue: Decimal = Decimal('0.00')
    
    # Taux de rétention
    renewal_rate: float = 0.0
    churn_rate: float = 0.0
    trial_conversion_rate: float = 0.0
    
    # Période
    period_start: date
    period_end: date
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionUsage(BaseModel):
    """Utilisation des ressources d'abonnement"""
    subscription_id: UUID
    tenant_id: UUID
    
    # Utilisateurs
    user_count: int = 0
    user_limit: int = 0
    user_usage_percent: float = 0.0
    
    # Produits
    product_count: int = 0
    product_limit: Optional[int] = None
    product_usage_percent: Optional[float] = None
    
    # Pharmacies
    pharmacy_count: int = 0
    pharmacy_limit: int = 0
    pharmacy_usage_percent: float = 0.0
    
    # Période
    period_start: date
    period_end: date
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionInDB(SubscriptionResponse):
    """Schéma pour les données stockées en base de données"""
    pass


# =======================
# EXPORTS
# =======================

__all__ = [
    # Enums
    "SubscriptionPlanEnum",
    "BillingPeriodEnum",
    "SubscriptionStatusEnum",
    "PaymentStatusEnum",
    "PaymentMethodEnum",
    
    # Schémas de base (utilisés dans subscriptions.py)
    "PlanResponseSchema",
    "UpgradeSubscriptionSchema",
    "ManualActivationSchema",
    "SubscriptionFilterSchema",
    "SubscriptionResponseSchema",
    "SubscriptionDetailSchema",
    
    # Schémas pour codes d'abonnement
    "SubscriptionCodeCreate",
    "ActivateSubscriptionCode",
    "SubscriptionCodeResponse",
    "ValidateCodeResponse",
    
    # Schémas complets
    "SubscriptionBase",
    "SubscriptionCreate",
    "SubscriptionTrialCreate",
    "SubscriptionUpgradeRequest",
    "SubscriptionRenewalRequest",
    "SubscriptionUpdate",
    "SubscriptionStatusUpdate",
    "SubscriptionResponse",
    "SubscriptionSummaryResponse",
    "SubscriptionCreationResponse",
    
    # Paiements
    "PaymentBase",
    "PaymentCreate",
    "PaymentUpdate",
    "PaymentResponse",
    
    # Rapports
    "SubscriptionAnalytics",
    "SubscriptionUsage",
    
    # Compatibilité DB
    "SubscriptionInDB"
]
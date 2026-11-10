# app/schemas/superadmin.py
"""
Schémas Pydantic pour les endpoints Super Admin
"""
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID


class TenantResponse(BaseModel):
    """Schéma de réponse pour un tenant"""
    id: UUID
    tenant_code: str
    nom_pharmacie: str
    nom_commercial: Optional[str] = None
    ville: str
    pays: str
    email_admin: EmailStr
    telephone_principal: Optional[str] = None
    type_pharmacie: Optional[str] = None
    status: str
    current_plan: str
    max_users: int
    max_products: int
    max_pharmacies: int
    trial_start_date: Optional[datetime] = None
    trial_end_date: Optional[datetime] = None
    activated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    config: Dict[str, Any] = {}
    
    # Données calculées
    active_users: int = 0
    active_pharmacies: int = 0
    last_payment: Optional[datetime] = None
    last_payment_amount: float = 0
    
    # Données détaillées (optionnelles)
    recent_payments: Optional[List[Dict[str, Any]]] = None
    recent_users: Optional[List[Dict[str, Any]]] = None
    pharmacies: Optional[List[Dict[str, Any]]] = None
    
    class Config:
        from_attributes = True


class TenantUpdate(BaseModel):
    """Schéma pour la mise à jour d'un tenant"""
    nom_pharmacie: Optional[str] = None
    nom_commercial: Optional[str] = None
    ville: Optional[str] = None
    pays: Optional[str] = None
    email_admin: Optional[EmailStr] = None
    telephone_principal: Optional[str] = None
    type_pharmacie: Optional[str] = None
    max_users: Optional[int] = Field(None, ge=0)
    max_products: Optional[int] = Field(None, ge=0)
    max_pharmacies: Optional[int] = Field(None, ge=0)
    trial_start_date: Optional[datetime] = None
    trial_end_date: Optional[datetime] = None
    config: Optional[Dict[str, Any]] = None


class TenantStats(BaseModel):
    """Statistiques d'un tenant"""
    tenant_id: UUID
    tenant_code: str
    nom_pharmacie: str
    active_users: int
    total_users: int
    active_pharmacies: int
    total_pharmacies: int
    current_plan: str
    plan_name: Optional[str] = None
    subscription_active: bool
    trial_days_remaining: Optional[int] = None
    last_payment_date: Optional[datetime] = None
    total_payments: int = 0
    total_revenue: float = 0
    monthly_revenue: float = 0
    yearly_revenue: float = 0
    created_at: datetime
    last_active: Optional[datetime] = None


class SubscriptionPlanChange(BaseModel):
    """Schéma pour changer le plan d'abonnement"""
    new_plan: str = Field(..., pattern="^(starter|professional|enterprise)$")
    plan_name: Optional[str] = None
    billing_period: Optional[str] = Field("monthly", pattern="^(monthly|yearly)$")
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    notes: Optional[str] = None


class PaymentHistoryResponse(BaseModel):
    """Historique des paiements"""
    id: UUID
    tenant_id: UUID
    tenant_code: str
    nom_pharmacie: str
    amount: float
    payment_method: str
    status: str
    reference: Optional[str] = None
    paid_at: datetime
    created_at: datetime
    
    # Informations liées
    subscription_plan: Optional[str] = None
    billing_period: Optional[str] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class SystemAnalytics(BaseModel):
    """Analytics système"""
    period: str
    start_date: datetime
    end_date: datetime
    
    # Métriques
    total_tenants: int
    active_tenants: int
    trial_tenants: int
    suspended_tenants: int
    cancelled_tenants: int
    
    total_users: int
    active_users: int
    
    total_revenue: float
    monthly_revenue: float
    yearly_revenue: float
    
    churn_rate: float
    growth_rate: float
    
    # Distribution
    plan_distribution: Dict[str, int]
    status_distribution: Dict[str, int]
    payment_method_distribution: Dict[str, Dict[str, Any]]
    
    # Tendances
    new_tenants_trend: List[Dict[str, Any]]
    revenue_trend: List[Dict[str, Any]]
    user_growth_trend: List[Dict[str, Any]]


class UserManagementResponse(BaseModel):
    """Réponse pour la gestion des utilisateurs"""
    id: UUID
    email: EmailStr
    nom_complet: str
    role: str
    telephone: Optional[str] = None
    actif: bool
    date_creation: datetime
    last_login: Optional[datetime] = None
    
    # Informations tenant
    tenant_id: UUID
    tenant_code: str
    nom_pharmacie: str
    
    # Permissions
    permissions: Optional[Dict[str, bool]] = None
    
    class Config:
        from_attributes = True


class SystemHealthCheck(BaseModel):
    """État de santé du système"""
    status: str
    timestamp: datetime
    services: Dict[str, Dict[str, Any]]
    database_status: str
    cache_status: Optional[str] = None
    storage_status: Optional[str] = None
    api_status: Dict[str, str]
    metrics: Dict[str, Any]
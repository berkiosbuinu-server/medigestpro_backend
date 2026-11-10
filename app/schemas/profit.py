# app/schemas/profit.py
"""
Schémas Pydantic pour les bénéfices et analyses financières
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import date, datetime
from decimal import Decimal


class ProfitStatsResponse(BaseModel):
    """Statistiques globales des bénéfices"""
    gross_profit: float = Field(..., description="Bénéfice brut")
    net_profit: float = Field(..., description="Bénéfice net")
    total_revenue: float = Field(..., description="Chiffre d'affaires total")
    total_cost: float = Field(..., description="Coût total des ventes")
    expected_profit: float = Field(..., description="Profit attendu")
    actual_profit: float = Field(..., description="Profit réalisé")
    remaining_profit: float = Field(..., description="Profit restant")
    margin_rate: float = Field(..., description="Taux de marge (%)")
    purchase_value: float = Field(..., description="Valeur d'achat du stock")
    selling_value: float = Field(..., description="Valeur de vente du stock")
    period_start: Optional[str] = None
    period_end: Optional[str] = None


class ProfitBreakdownResponse(BaseModel):
    """Détail de la répartition des bénéfices"""
    total_profit: float = Field(..., description="Bénéfice total")
    by_category: Dict[str, float] = Field(default_factory=dict, description="Par catégorie de produit")
    by_product: List[Dict[str, Any]] = Field(default_factory=list, description="Par produit")
    by_branch: List[Dict[str, Any]] = Field(default_factory=list, description="Par succursale")
    by_user: List[Dict[str, Any]] = Field(default_factory=list, description="Par utilisateur")
    by_payment_method: Dict[str, float] = Field(default_factory=dict, description="Par méthode de paiement")
    by_hour: Dict[int, float] = Field(default_factory=dict, description="Par heure de la journée")
    by_weekday: Dict[str, float] = Field(default_factory=dict, description="Par jour de la semaine")


class DailyProfitResponse(BaseModel):
    """Bénéfices journaliers"""
    date: str
    revenue: float
    cost: float
    profit: float
    margin_rate: float
    sales_count: int


class PeriodProfitResponse(BaseModel):
    """Bénéfices par période"""
    period: str
    data: List[Dict[str, Any]]
    total_profit: float
    total_revenue: float
    average_profit: float
    best_day: Optional[Dict[str, Any]] = None
    worst_day: Optional[Dict[str, Any]] = None


class UserProfitResponse(BaseModel):
    """Bénéfices par utilisateur"""
    user_id: str
    user_name: str
    user_role: str
    total_revenue: float
    total_profit: float
    sale_count: int
    margin_rate: float


class BranchProfitResponse(BaseModel):
    """Bénéfices par succursale"""
    branch_id: str
    branch_name: str
    total_revenue: float
    total_profit: float
    sale_count: int
    margin_rate: float
    city: Optional[str] = None


class SessionProfitResponse(BaseModel):
    """Bénéfices par session"""
    session_id: str
    user_id: str
    user_name: str
    pharmacy_id: Optional[str] = None
    session_start: str
    session_end: Optional[str] = None
    total_revenue: float
    total_profit: float
    sale_count: int
    margin_rate: float


class ProfitComparisonResponse(BaseModel):
    """Comparaison de bénéfices"""
    period1: Dict[str, Any]
    period2: Dict[str, Any]
    absolute_change: float
    percentage_change: float
    trend: str  # up, down, stable
    analysis: str


class ProfitTrendResponse(BaseModel):
    """Tendance des bénéfices"""
    monthly_data: List[Dict[str, Any]]
    trend_percentage: float
    trend_direction: str  # up, down, stable
    forecast: List[Dict[str, Any]]


class SWOTAnalysisResponse(BaseModel):
    """Analyse SWOT"""
    strengths: List[Dict[str, Any]]
    weaknesses: List[Dict[str, Any]]
    opportunities: List[Dict[str, Any]]
    threats: List[Dict[str, Any]]
    recommendations: List[str]
    summary: str
    last_updated: str


class ProfitForecastResponse(BaseModel):
    """Prévisions de bénéfices"""
    forecast: List[Dict[str, Any]]
    confidence_level: float
    methodology: str
    historical_average: Optional[float] = None
    historical_trend: Optional[float] = None


class BestPerformersResponse(BaseModel):
    """Meilleurs performers"""
    top_products: List[Dict[str, Any]]
    top_sellers: List[Dict[str, Any]]
    top_categories: List[Dict[str, Any]]
    top_periods: List[Dict[str, Any]]


class FinancialAnalysisResponse(BaseModel):
    """Analyse financière complète"""
    profitability_ratios: Dict[str, Any]
    cost_structure: Dict[str, Any]
    margin_analysis: Dict[str, Any]
    performance_indicators: Dict[str, Any]
    recommendations: List[str]
    payment_methods_breakdown: Optional[Dict[str, float]] = None
    top_products: Optional[List[Dict[str, Any]]] = None
# app/schemas/capital.py
"""
Schémas Pydantic pour la gestion du capital et du chiffre d'affaires
"""

from pydantic import BaseModel, Field, validator
from datetime import date, datetime
from typing import Optional, List, Dict, Any
from decimal import Decimal
from uuid import UUID


# =======================
# Capital
# =======================

class CapitalBase(BaseModel):
    initial_capital: float = Field(0, description="Capital initial")
    current_capital: float = Field(0, description="Capital actuel")
    cash_capital: float = Field(0, description="Capital en caisse")
    stock_capital: float = Field(0, description="Capital en stock")
    equipment_capital: float = Field(0, description="Capital en équipement")
    other_capital: float = Field(0, description="Autres capitaux")
    start_date: Optional[date] = Field(None, description="Date de début")
    notes: Optional[str] = Field(None, description="Notes")


class CapitalCreate(CapitalBase):
    pass


class CapitalUpdate(BaseModel):
    amount: float = Field(..., description="Montant à ajouter (positif) ou retirer (négatif)")
    category: str = Field(..., description="Catégorie: cash, stock, equipment, other")
    description: Optional[str] = Field(None, description="Description")


class CapitalResponse(CapitalBase):
    id: UUID
    tenant_id: Optional[UUID] = None
    pharmacy_id: Optional[UUID] = None
    branch_id: Optional[UUID] = None
    last_update_date: Optional[date] = None
    created_at: datetime
    updated_at: datetime


# =======================
# Transactions de capital
# =======================

class CapitalTransactionBase(BaseModel):
    transaction_type: str = Field(..., description="initial, increase, decrease, profit_added, loss_deducted")
    transaction_category: str = Field(..., description="cash, stock, equipment, other, turnover, expense")
    amount: float
    description: Optional[str] = None
    transaction_date: date


class CapitalTransactionCreate(CapitalTransactionBase):
    capital_id: UUID
    reference_id: Optional[UUID] = None
    reference_type: Optional[str] = None


class CapitalTransactionResponse(CapitalTransactionBase):
    id: UUID
    capital_id: UUID
    previous_capital: float
    new_capital: float
    created_at: datetime
    created_by: Optional[UUID] = None


# =======================
# Chiffre d'affaires
# =======================

class TurnoverResponse(BaseModel):
    pharmacy_id: UUID
    pharmacy_name: str
    branch_id: Optional[UUID] = None
    period_type: str
    period_start: date
    period_end: date
    total_turnover: float = Field(0, description="Chiffre d'affaires total TTC")
    net_turnover: float = Field(0, description="Chiffre d'affaires net HT")
    tax_amount: float = Field(0, description="Montant de la TVA")
    discount_amount: float = Field(0, description="Montant des remises")
    total_expenses: float = Field(0, description="Total des dépenses")
    net_profit: float = Field(0, description="Bénéfice net")
    sales_count: int = Field(0, description="Nombre de ventes")
    items_sold: int = Field(0, description="Nombre d'articles vendus")


class TurnoverDetailResponse(BaseModel):
    pharmacy_id: UUID
    pharmacy_name: str
    branch_id: Optional[UUID] = None
    period_start: date
    period_end: date
    total_revenue: float
    net_revenue: float
    total_tax: float
    total_items_sold: int
    total_sales_count: int
    by_product: List[Dict[str, Any]]
    by_category: List[Dict[str, Any]]


class TurnoverByPeriodResponse(BaseModel):
    pharmacy_id: UUID
    pharmacy_name: str
    branch_id: Optional[UUID] = None
    period_type: str
    periods: List[Dict[str, Any]]
    data: List[Dict[str, Any]]
    total_turnover: float
    total_net_turnover: float
    total_expenses: float
    total_net_profit: float
    total_sales: int
    growth_percentage: float


# =======================
# Évolution du capital
# =======================

class CapitalEvolutionResponse(BaseModel):
    pharmacy_id: UUID
    pharmacy_name: str
    branch_id: Optional[UUID] = None
    start_date: date
    end_date: date
    evolution: List[Dict[str, Any]]
    initial_capital: float
    final_capital: float
    total_turnover: float
    total_expenses: float
    net_profit: float
    capital_variation: float


# =======================
# Résumé financier
# =======================

class CapitalSummaryResponse(BaseModel):
    pharmacy_id: UUID
    pharmacy_name: str
    branch_id: Optional[UUID] = None
    capital: Dict[str, float]
    turnover_today: Dict[str, Any]
    turnover_month: Dict[str, Any]
    turnover_year: Dict[str, Any]
    last_update: datetime


# =======================
# Rapport SYSCOHADA
# =======================

class CapitalBalanceResponse(BaseModel):
    account_code: str
    account_name: str
    account_type: str
    balance: float
    debit: float
    credit: float
    period_year: int
    period_month: Optional[int] = None


class CapitalReportResponse(BaseModel):
    pharmacy_id: UUID
    pharmacy_name: str
    branch_id: Optional[UUID] = None
    period_start: date
    period_end: date
    balance: Dict[str, Any]
    income_statement: Dict[str, Any]
    cash_flow: Dict[str, Any]
    equity_changes: Dict[str, Any]
    ratios: Dict[str, float]
    generated_at: datetime
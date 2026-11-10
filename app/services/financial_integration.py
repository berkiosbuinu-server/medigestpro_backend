# app/services/financial_integration.py

from uuid import UUID
from sqlalchemy.orm import Session

from app.models.cost import Cost


class FinancialIntegration:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id
    
    def reconcile_with_accounting(self, cost_id: UUID) -> dict:
        """
        Réconcilie un coût avec le système comptable
        """
        cost = (
            self.db.query(Cost)
            .filter(
                Cost.id == cost_id,
                Cost.tenant_id == self.tenant_id
            )
            .first()
        )
        
        if not cost:
            raise ValueError("Coût non trouvé")
        
        accounting_entry = {
            "date": cost.payment_date,
            "account_code": self.get_account_code(cost.category),
            "description": cost.description,
            "debit": cost.total_amount if self.is_debit_account(cost.category) else 0,
            "credit": cost.total_amount if not self.is_debit_account(cost.category) else 0,
            "reference": cost.invoice_number,
            "metadata": {
                "cost_id": str(cost.id),
                "supplier_id": str(cost.supplier_id) if cost.supplier_id else None,
                "tenant_id": str(self.tenant_id)
            }
        }
        
        return accounting_entry
    
    def get_account_code(self, category: str) -> str:
        """
        Retourne le code comptable pour une catégorie de coût
        """
        mapping = {
            "salary": "6221",
            "rent": "6132",
            "utilities": "6152",
            "maintenance": "6155",
            "supplies": "6022",
            "marketing": "6232",
            "software": "6061",
            "insurance": "6161",
            "transport": "6241",
            "training": "6311",
            "consulting": "6226",
            "taxes": "6313",
            "other": "6068",
        }
        return mapping.get(category, "6068")
    
    def is_debit_account(self, category: str) -> bool:
        """
        Détermine si le compte est un débit
        """
        return True  # Les charges sont généralement en débit

from pydantic import BaseModel

class PaymentCreate(BaseModel):
    montant: float
    moyen_paiement: str  # mobile_money | visa
    reference: str | None = None
    provider: str  # "orange" ou "mpesa"

#api/v1/payments_saas.py
from fastapi import APIRouter
from app.payments.payment_processor import process_subscription_payment

router = APIRouter(prefix="/saas/payments", tags=["SaaS Payments"])

@router.post("/subscribe")
def subscribe(reference: str, amount: float, phone: str):
    return process_subscription_payment(reference, amount, phone)

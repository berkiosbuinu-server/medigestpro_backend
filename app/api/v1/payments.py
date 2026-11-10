# app/api/v1/payments.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user
from app.schemas.payment import PaymentCreate
from app.services.payment_service import process_payment

# Import des providers
from app.payments.providers.orangemoney import OrangeMoneyProvider
from app.payments.providers.mpesa import MpesaProvider

router = APIRouter(prefix="/payments", tags=["Payments"])

providers = {
    "orange": OrangeMoneyProvider(),
    "mpesa": MpesaProvider()
}

@router.post("/")
def pay_subscription(
    data: PaymentCreate,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    # Choix du provider
    provider_name = data.provider.lower()
    if provider_name not in providers:
        raise HTTPException(status_code=400, detail="Provider inconnu")

    provider = providers[provider_name]

    # Initier le paiement
    payment_result = provider.initiate(
        phone=data.phone,
        amount=data.amount,
        reference=data.reference
    )

    # Traitement interne SaaS (abonnement)
    subscription = process_payment(db, user.tenant_id, data)

    return {
        "message": "Paiement initié, abonnement activé après confirmation",
        "provider_result": payment_result,
        "date_fin": subscription.date_fin
    }

@router.post("/subscription")
def pay_subscription(
    data: PaymentCreate,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    # Valider que c'est bien un paiement d'abonnement
    if not data.plan:
        raise HTTPException(400, "Plan is required for subscription payment")
    
    # Choix du provider si mobile money
    if data.payment_method == "mobile_money" and data.provider:
        provider_name = data.provider.lower()
        if provider_name not in providers:
            raise HTTPException(400, "Provider inconnu")
        
        provider = providers[provider_name]
        payment_result = provider.initiate(
            phone=data.phone,
            amount=data.amount,
            reference=data.reference
        )
    else:
        # Pour cash, visa, etc.
        payment_result = {"status": "manual", "method": data.payment_method}
    
    # Traitement interne
    payment = process_payment(db, user.tenant_id, data, payment_type="subscription")
    
    return {
        "message": "Paiement d'abonnement traité avec succès",
        "provider_result": payment_result,
        "payment": {
            "id": str(payment.id),
            "amount": payment.amount,
            "plan": payment.subscription_plan,
            "billing_period": payment.billing_period,
            "period_end": payment.period_end.isoformat() if payment.period_end else None
        }
    }

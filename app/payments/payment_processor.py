from app.payments.mobile_money import MobileMoneyGateway
from app.payments.payment_validator import validate_payment

gateway = MobileMoneyGateway()

def process_subscription_payment(reference: str, amount: float, phone: str):
    validate_payment(amount)
    return gateway.initiate_payment(reference, amount, phone)

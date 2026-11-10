from app.billing.invoice_generator import generate_invoice

def finalize_subscription_payment(tenant_id: str, amount: float):
    return generate_invoice(
        invoice_id=tenant_id,
        tenant_name=tenant_id,
        amount=amount,
    )

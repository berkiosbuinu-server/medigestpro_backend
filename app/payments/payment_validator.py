def validate_payment(amount: float):
    if amount <= 0:
        raise ValueError("Montant invalide")

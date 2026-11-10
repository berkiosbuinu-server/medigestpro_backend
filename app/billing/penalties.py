from datetime import date

def is_late(payment_due: date) -> bool:
    return date.today() > payment_due

def calculate_penalty(amount: float, days_late: int) -> float:
    return amount * 0.02 * days_late

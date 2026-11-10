from enum import Enum
from datetime import date, timedelta

class BillingCycle(str, Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"

def next_billing_date(start_date: date, cycle: BillingCycle) -> date:
    if cycle == BillingCycle.MONTHLY:
        return start_date + timedelta(days=30)
    if cycle == BillingCycle.YEARLY:
        return start_date + timedelta(days=365)

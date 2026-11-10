PLAN_LIMITS = {
    "free": {
        "users": 1,
        "products": 50,
        "sales_per_month": 100,
    },
    "basic": {
        "users": 3,
        "products": 500,
        "sales_per_month": 2000,
    },
    "pro": {
        "users": 10,
        "products": 5000,
        "sales_per_month": 10000,
    },
}

def check_limit(plan: str, key: str, value: int):
    limit = PLAN_LIMITS.get(plan, {}).get(key)
    if limit is not None and value > limit:
        raise PermissionError(f"Limite atteinte pour {key}")

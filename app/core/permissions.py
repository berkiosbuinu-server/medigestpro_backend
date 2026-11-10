#app/core/permissions.py
from app.core.roles import Role

ROLE_PERMISSIONS = {
    Role.SUPER_ADMIN: {"*"},
    Role.TENANT_ADMIN: {
        "users:manage",
        "inventory:manage",
        "sales:manage",
        "finance:manage",
        "reports:view",
    },
    Role.MANAGER: {
        "inventory:manage",
        "sales:manage",
        "reports:view",
    },
    Role.CASHIER: {
        "sales:create",
        "payments:create",
    },
    Role.READ_ONLY: {
        "reports:view",
    },
}

def has_permission(role: Role, permission: str) -> bool:
    perms = ROLE_PERMISSIONS.get(role, set())
    return "*" in perms or permission in perms

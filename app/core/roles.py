#app/core/roles.py
from enum import Enum

class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    TENANT_ADMIN = "tenant_admin"
    MANAGER = "manager"
    CASHIER = "cashier"
    READ_ONLY = "read_only"

# app/core/constants.py
from uuid import UUID
from typing import List

SYSTEM_TENANT_ID = UUID('00000000-0000-0000-0000-000000000001')



# Liste complète des rôles valides
VALID_ROLES: List[str] = [
    "admin",
    "gerant",
    "superviseur", 
    "pharmacien",
    "vendeur",
    "caissier",
    "gestionnaire",
    "comptable",
    "preparateur",
    "stockiste",
    "technicien"
]

# Pattern regex pour validation
ROLE_PATTERN = "^(admin|gerant|superviseur|pharmacien|vendeur|caissier|gestionnaire|comptable|preparateur|stockiste|technicien)$"
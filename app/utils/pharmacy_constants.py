from enum import Enum

class PharmacyStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING = "pending"

class PharmacyTypes(str, Enum):
    INDEPENDENT = "independent"
    CHAIN = "chain"
    HOSPITAL = "hospital"
    COMMUNITY = "community"

class LicenseTypes(str, Enum):
    TYPE_A = "type_a"  # Pharmacie d'officine
    TYPE_B = "type_b"  # Pharmacie hospitalière
    TYPE_C = "type_c"  # Dépôt pharmaceutique

# Codes pays pour l'Afrique de l'Ouest
WEST_AFRICAN_COUNTRIES = {
    "SN": "Sénégal",
    "CI": "Côte d'Ivoire",
    "ML": "Mali",
    "GN": "Guinée",
    "BF": "Burkina Faso",
    "NE": "Niger",
    "TG": "Togo",
    "BJ": "Bénin",
}

# Codes de produits pharmaceutiques
PHARMACY_PRODUCT_CATEGORIES = [
    "Médicament",
    "Parapharmacie",
    "Matériel médical",
    "Produit cosmétique",
    "Produit diététique",
    "Hygiène",
]

# Unités de mesure pharmaceutiques
PHARMACY_UNITS = [
    "boîte",
    "flacon",
    "tube",
    "sachet",
    "comprimé",
    "gélule",
    "ampoule",
    "ml",
    "g",
]

# Alertes d'expiration
EXPIRY_ALERT_DAYS = [7, 30, 60, 90]
# app/config/plans.py
"""
Configuration des plans d'abonnement pour les pharmacies.
"""

from typing import Dict, Any, Optional

# Configuration complète des plans
PLAN_CONFIG = {
    "trial": {
        "name": "Essai Gratuit",
        "price_monthly": 0.0,
        "price_yearly": 0.0,
        "max_products": 2000,
        "max_users": 5,
        "max_branches": None,
        "max_storage_mb": 512,
        "description": "Période d'essai gratuite",
        "features": [
            "Jusqu'à 2000 produits",
            "Jusqu'à 5 utilisateurs",
            " branche/succursale illimitée",
            "Support email"
        ]
    },
    "starter": {
        "name": "Starter",
        "price_monthly": 5,
        "price_yearly": 50,
        "max_products": 3000,
        "max_users": 5,
        "max_branches": None,
        "max_storage_mb": 1024,
        "description": "Pour les petites pharmacies",
        "features": [
            "Jusqu'à 500 produits",
            "Jusqu'à 5 utilisateurs",
            "1 branche/succursale",
            "Gestion des stocks",
            "Gestion des ventes",
            "Rapports de base",
            "Support email"
        ]
    },
    "professional": {
        "name": "Professional",
        "price_monthly": 8,
        "price_yearly": 72,
        "max_products": 4000,
        "max_users": 20,
        "max_branches": None,
        "max_storage_mb": 5120,
        "description": "Pour les pharmacies en croissance",
        "features": [
            "Jusqu'à 3 000 produits",
            "Jusqu'à 20 utilisateurs",
            "Jusqu'à branches/succursales iilimitéés",
            "Gestion des stocks avancée",
            "Gestion des ventes",
            "Rapports avancés",
            "Gestion des achats",
            "Gestion des clients",
            "Support prioritaire",
            "API d'intégration"
        ]
    },
    "enterprise": {
        "name": "Enterprise",
        "price_monthly": 15,
        "price_yearly": 120,
        "max_products": 15000,  
        "max_users": None,  # Illimité
        "max_branches": None,  # Illimité
        "max_storage_mb": 10240,
        "description": "Pour les grandes chaînes de pharmacies",
        "features": [
            "jusqu'à 10 000 Produits",
            "Utilisateurs illimités",
            "Branches illimitées",
            "Toutes les fonctionnalités",
            "Support dédié 24/7",
            "Formation personnalisée",
            "Hébergement dédié optionnel",
            "API complète",
            "Rapports personnalisés"
        ]
    },
    "infinite": {
        "name": "Infinite",
        "price_monthly": 30,
        "price_yearly": 280,
        "max_products": None,  # Illimité
        "max_users": None,  # Illimité
        "max_branches": None,  # Illimité
        "max_storage_mb": 20480,
        "description": "Solution complète pour grands groupes",
        "features": [
            "Tout illimité",
            "Support VIP",
            "Consulting dédié",
            "Développement sur mesure",
            "SLA garanti"
        ]
    }
}


def get_plan_config(plan_type: str) -> Dict[str, Any]:
    """
    Récupère la configuration d'un plan.
    
    Args:
        plan_type: Type de plan (trial, starter, professional, enterprise, infinite)
        
    Returns:
        Configuration du plan
        
    Raises:
        ValueError: Si le plan n'existe pas
    """
    if plan_type not in PLAN_CONFIG:
        raise ValueError(f"Plan '{plan_type}' non trouvé. Plans disponibles: {list(PLAN_CONFIG.keys())}")
    
    return PLAN_CONFIG[plan_type].copy()


def get_plan_features(plan_type: str) -> list:
    """Récupère la liste des fonctionnalités d'un plan"""
    config = get_plan_config(plan_type)
    return config.get("features", [])


def get_plan_price(plan_type: str, cycle: str = "monthly") -> float:
    """
    Récupère le prix d'un plan.
    
    Args:
        plan_type: Type de plan
        cycle: 'monthly' ou 'yearly'
        
    Returns:
        Prix du plan
    """
    config = get_plan_config(plan_type)
    price_key = f"price_{cycle}"
    return config.get(price_key, 0.0)


def get_plan_limits(plan_type: str) -> Dict[str, Optional[int]]:
    """
    Récupère les limites d'un plan.
    
    Returns:
        Dictionnaire avec les limites (None = illimité)
    """
    config = get_plan_config(plan_type)
    return {
        "max_products": config.get("max_products"),
        "max_users": config.get("max_users"),
        "max_branches": config.get("max_branches", 0),
        "max_storage_mb": config.get("max_storage_mb", 1024)
    }


def get_default_plan() -> str:
    """Retourne le plan par défaut (trial)"""
    return "trial"


def is_valid_plan(plan_type: str) -> bool:
    """Vérifie si un plan existe"""
    return plan_type in PLAN_CONFIG


def get_all_plans() -> Dict[str, Dict[str, Any]]:
    """Retourne tous les plans disponibles"""
    return PLAN_CONFIG.copy()
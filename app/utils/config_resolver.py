# app/utils/config_resolver.py
from typing import Any, Dict, Optional
from app.models.pharmacy import Pharmacy
from app.models.branch import Branch

class ConfigResolver:
    """
    Résout la configuration en donnant priorité à la branche,
    puis à la pharmacie, puis aux valeurs par défaut.
    """
    
    @staticmethod
    def resolve_config(branch: Branch, pharmacy: Pharmacy, key: str, default: Any = None) -> Any:
        """
        Résout une configuration spécifique
        Priorité: branche.operational_config > pharmacy.config > default
        """
        # Vérifier d'abord dans la configuration opérationnelle de la branche
        if branch.operational_config and key in branch.operational_config:
            value = branch.operational_config.get(key)
            if value is not None:
                return value
        
        # Sinon dans la configuration de la pharmacie
        if pharmacy.config and key in pharmacy.config:
            value = pharmacy.config.get(key)
            if value is not None:
                return value
        
        return default
    
    @staticmethod
    def resolve_working_hours(branch: Branch, pharmacy: Pharmacy) -> Dict[str, Any]:
        """Résout les heures de travail avec priorité à la branche"""
        # Vérifier si la branche a ses propres horaires
        if branch.operational_config and branch.operational_config.get("workingHours"):
            return branch.operational_config["workingHours"]
        
        # Sinon utiliser ceux de la pharmacie
        if pharmacy.config and pharmacy.config.get("workingHours"):
            return pharmacy.config["workingHours"]
        
        # Valeurs par défaut
        return {
            "enabled": True,
            "startTime": "08:00",
            "endTime": "20:00",
            "timezone": "Africa/Kinshasa",
            "daysOff": {
                "monday": True, "tuesday": True, "wednesday": True,
                "thursday": True, "friday": True, "saturday": True, "sunday": False
            }
        }
    
    @staticmethod
    def resolve_currencies(branch: Branch, pharmacy: Pharmacy) -> list:
        """Résout la configuration des devises"""
        # Vérifier si la branche a ses propres devises
        if branch.operational_config and branch.operational_config.get("currencies"):
            return branch.operational_config["currencies"]
        
        # Sinon utiliser celles de la pharmacie
        if pharmacy.config and pharmacy.config.get("currencies"):
            return pharmacy.config["currencies"]
        
        # Valeurs par défaut
        return [
            {"code": "CDF", "symbol": "FC", "isActive": True, "exchangeRate": 2500},
            {"code": "USD", "symbol": "$", "isActive": True, "exchangeRate": 1}
        ]
    
    @staticmethod
    def get_active_subscription_features(branch: Branch) -> Dict[str, Any]:
        """Récupère les fonctionnalités disponibles selon l'abonnement de la branche"""
        if branch.branch_subscription and branch.branch_subscription.is_active():
            return branch.branch_subscription.get_features()
        
        # Abonnement par défaut (trial)
        return {
            "max_users": 5,
            "max_products": 500,
            "max_transactions_per_month": 1000,
            "features": {
                "inventory_management": True,
                "sales": True,
                "reports": True,
                "multi_currency": False,
                "pos_integration": False,
                "api_access": False
            }
        }
    
    @staticmethod
    def can_perform_action(branch: Branch, action: str, current_count: int = 0) -> bool:
        """
        Vérifie si la branche peut effectuer une action selon son abonnement
        """
        features = ConfigResolver.get_active_subscription_features(branch)
        
        limits = {
            "create_user": ("max_users", current_count),
            "create_product": ("max_products", current_count),
            "create_sale": ("max_transactions_per_month", current_count)
        }
        
        if action in limits:
            limit_key, count = limits[action]
            max_limit = features.get(limit_key, float('inf'))
            return count < max_limit
        
        # Vérifier les fonctionnalités
        feature_map = {
            "use_multi_currency": "multi_currency",
            "use_pos": "pos_integration",
            "use_api": "api_access"
        }
        
        if action in feature_map:
            return features.get("features", {}).get(feature_map[action], False)
        
        return True
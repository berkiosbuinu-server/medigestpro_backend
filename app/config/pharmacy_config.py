from typing import Dict, Any
from app.db.session import get_db
from app.models.pharmacy import Pharmacy

class PharmacyConfigManager:
    """Gestionnaire de configuration spécifique à chaque pharmacie"""
    
    DEFAULT_CONFIG = {
        "pharmacy": {
            "require_prescription": True,
            "enable_expiry_alerts": True,
            "expiry_alert_days": 30,
            "low_stock_threshold": 10,
            "critical_stock_threshold": 5,
        },
        "inventory": {
            "enable_barcode": True,
            "auto_reorder": False,
            "reorder_point": 20,
            "batch_tracking": True,
        },
        "sales": {
            "tax_rate": 18.0,
            "enable_discount": True,
            "max_discount_percentage": 20,
            "require_customer_info": False,
        },
        "system": {
            "currency": "XOF",
            "language": "fr",
            "date_format": "dd/MM/yyyy",
            "time_format": "24h",
            "decimal_precision": 2,
        },
        "notifications": {
            "email_alerts": True,
            "sms_alerts": False,
            "low_stock_alert": True,
            "expiry_alert": True,
        }
    }
    
    @classmethod
    def get_config(cls, pharmacy_id: int) -> Dict[str, Any]:
        """Récupère la configuration d'une pharmacie"""
        db = next(get_db())
        pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
        
        if not pharmacy:
            return cls.DEFAULT_CONFIG
        
        # Fusionner avec la configuration par défaut
        config = cls.DEFAULT_CONFIG.copy()
        if pharmacy.config:
            cls._deep_update(config, pharmacy.config)
        
        return config
    
    @classmethod
    def update_config(cls, pharmacy_id: int, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Met à jour la configuration d'une pharmacie"""
        db = next(get_db())
        pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
        
        if not pharmacy:
            raise ValueError("Pharmacie non trouvée")
        
        # Fusionner les mises à jour
        current_config = pharmacy.config or {}
        cls._deep_update(current_config, updates)
        
        pharmacy.config = current_config
        db.commit()
        db.refresh(pharmacy)
        
        return cls.get_config(pharmacy_id)
    
    @staticmethod
    def _deep_update(original: Dict, updates: Dict) -> Dict:
        """Mise à jour récursive de dictionnaire"""
        for key, value in updates.items():
            if isinstance(value, dict) and key in original and isinstance(original[key], dict):
                PharmacyConfigManager._deep_update(original[key], value)
            else:
                original[key] = value
        return original
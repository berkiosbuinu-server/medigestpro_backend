import re
from typing import Optional, Dict, Any
from datetime import datetime, date
from app.utils.pharmacy_constants import WEST_AFRICAN_COUNTRIES

class PharmacyValidator:
    """Validation des données pharmaceutiques"""
    
    @staticmethod
    def validate_license_number(license_number: str, country: str = "SN") -> bool:
        """Valide le numéro de licence pharmaceutique"""
        patterns = {
            "SN": r'^PH-\d{4}-\d{4}$',  # Format: PH-1234-5678
            "CI": r'^CI-PH-\d{6}$',
            "ML": r'^ML-PHARM-\d{5}$',
        }
        
        pattern = patterns.get(country, patterns["SN"])
        return bool(re.match(pattern, license_number))
    
    @staticmethod
    def validate_phone_number(phone: str, country: str = "SN") -> bool:
        """Valide le numéro de téléphone"""
        patterns = {
            "SN": r'^(77|76|70|78)\d{7}$',
            "CI": r'^(07|05|01)\d{8}$',
            "ML": r'^(6|7)\d{7}$',
        }
        
        pattern = patterns.get(country, patterns["SN"])
        return bool(re.match(pattern, phone.replace(" ", "")))
    
    @staticmethod
    def validate_dosage(dosage: str) -> bool:
        """Valide un dosage pharmaceutique"""
        pattern = r'^\d+(\.\d+)?\s*(mg|g|ml|µg|UI|%|mcg)(\/\w+)?$'
        return bool(re.match(pattern, dosage, re.IGNORECASE))

class PharmacyCalculator:
    """Calculs spécifiques aux pharmacies"""
    
    @staticmethod
    def calculate_selling_price(
        purchase_price: float,
        margin_percentage: float,
        tax_rate: float = 18.0
    ) -> float:
        """Calcule le prix de vente TTC"""
        selling_price_ht = purchase_price * (1 + margin_percentage / 100)
        selling_price_ttc = selling_price_ht * (1 + tax_rate / 100)
        return round(selling_price_ttc, 2)
    
    @staticmethod
    def calculate_expiry_status(
        expiry_date: date,
        alert_days: int = 30
    ) -> Dict[str, Any]:
        """Détermine le statut d'expiration"""
        today = date.today()
        days_until_expiry = (expiry_date - today).days
        
        if days_until_expiry < 0:
            return {"status": "expired", "days": days_until_expiry}
        elif days_until_expiry <= alert_days:
            return {"status": "expiring_soon", "days": days_until_expiry}
        else:
            return {"status": "valid", "days": days_until_expiry}

class PharmacyFormatter:
    """Formatage des données pharmaceutiques"""
    
    @staticmethod
    def format_license_number(license_number: str) -> str:
        """Formate le numéro de licence"""
        return license_number.upper().strip()
    
    @staticmethod
    def format_phone_number(phone: str, country: str = "SN") -> str:
        """Formate le numéro de téléphone"""
        phone = phone.replace(" ", "").replace("-", "")
        
        if country == "SN" and phone.startswith("+"):
            phone = phone[3:]  # Supprimer +221
        elif phone.startswith("00"):
            phone = phone[4:]  # Supprimer 00221
        
        return phone
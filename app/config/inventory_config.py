# app/config/inventory_config.py
INVENTORY_CONFIG = {
    "max_variance_threshold": 5.0,  # Pourcentage maximum acceptable
    "counting_timeout_minutes": 480,  # 8 heures par inventaire
    "auto_complete_after_days": 7,  # Inventaires auto-terminés après 7 jours
    "alert_on_variance": True,
    "require_dual_counting": False,  # Comptage double pour valeurs élevées
    "min_count_cycle_days": 30,
    "default_schedule": "monthly",
    "debt_reminder_days": [3, 7, 14],  # Jours avant rappel
    "max_debt_age_days": 90,  # Âge maximum des dettes avant action
    "auto_write_off_days": 180  # Radiation automatique après 180 jours
}
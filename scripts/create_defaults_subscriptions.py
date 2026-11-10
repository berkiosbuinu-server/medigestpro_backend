# scripts/create_default_subscriptions.py
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionLocal
from app.models.pharmacy import Pharmacy
from app.services.pharmacy_subscription_service import create_pharmacy_subscription
from uuid import UUID

def create_default_subscriptions():
    db = SessionLocal()
    try:
        pharmacies = db.query(Pharmacy).filter(Pharmacy.subscription_id.is_(None)).all()
        
        for pharmacy in pharmacies:
            print(f"Création abonnement pour: {pharmacy.name}")
            create_pharmacy_subscription(
                db=db,
                pharmacy_id=pharmacy.id,
                plan="trial",
                billing_cycle="monthly"
            )
        
        print(f"✅ {len(pharmacies)} abonnements créés")
    finally:
        db.close()

if __name__ == "__main__":
    create_default_subscriptions()
# app/services/invoice_scheduler.py
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.services.invoice_service import create_subscription_invoice

def generate_monthly_invoices(db: Session):
    """Génère les factures mensuelles pour tous les abonnements actifs."""
    from app.models.pharmacy import Pharmacy
    
    # Récupérer toutes les pharmacies avec abonnement actif
    pharmacies = db.query(Pharmacy).filter(
        Pharmacy.subscription_id.isnot(None)
    ).all()
    
    invoices_created = []
    
    for pharmacy in pharmacies:
        if not pharmacy.subscription or not pharmacy.subscription.is_active():
            continue
        
        # Vérifier si une facture a déjà été générée ce mois-ci
        now = datetime.utcnow()
        existing_invoice = db.query(Invoice).filter(
            Invoice.pharmacy_id == pharmacy.id,
            Invoice.issue_date >= now.replace(day=1)
        ).first()
        
        if not existing_invoice:
            invoice = create_subscription_invoice(db, pharmacy, pharmacy.subscription)
            invoices_created.append(invoice)
    
    return invoices_created
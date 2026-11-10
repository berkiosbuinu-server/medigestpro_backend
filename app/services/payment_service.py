from sqlalchemy.orm import Session
from app.models.payment import Payment
from app.models.tenant import Tenant
from datetime import datetime, timedelta

def process_payment(db: Session, tenant_id, data, payment_type="subscription"):
    # 1️⃣ Déterminer la période selon le plan
    period_start = datetime.utcnow()
    
    if payment_type == "subscription":
        # Déterminer la durée selon le billing_period
        if data.billing_period == "yearly":
            period_end = period_start + timedelta(days=365)
        else:  # monthly par défaut
            period_end = period_start + timedelta(days=30)
        
        # Vérifier que le montant correspond au plan
        plan_prices = {
            "starter": {"monthly": 10, "yearly": 100},
            "professional": {"monthly": 30, "yearly": 300},
            "enterprise": {"monthly": 100, "yearly": 1000}
        }
        
        expected_amount = plan_prices.get(data.plan, {}).get(data.billing_period)
        if expected_amount and abs(data.amount - expected_amount) > 0.01:
            raise ValueError(f"Montant incorrect pour le plan {data.plan}")
    
    # 2️⃣ Enregistrer paiement avec infos d'abonnement
    payment = Payment(
        tenant_id=tenant_id,
        amount=data.amount,
        payment_method=data.payment_method or data.provider,
        reference=data.reference,
        status="success",
        
        # Champs d'abonnement
        subscription_plan=data.plan,
        billing_period=data.billing_period if payment_type == "subscription" else None,
        period_start=period_start if payment_type == "subscription" else None,
        period_end=period_end if payment_type == "subscription" else None,
        subscription_type="paid" if payment_type == "subscription" else None,
        
        # Champs mobile money
        mobile_operator=data.provider if data.provider else None,
        mobile_number=data.phone if data.phone else None
    )
    
    db.add(payment)
    db.commit()
    
    # 3️⃣ Mettre à jour le tenant avec le nouveau plan
    if payment_type == "subscription" and data.plan:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant:
            tenant.current_plan = data.plan
            
            # Définir les limites selon le plan
            plan_limits = {
                "starter": {"max_users": 2, "max_products": 500, "max_pharmacies": 1},
                "professional": {"max_users": 10, "max_products": 0, "max_pharmacies": 3},
                "enterprise": {"max_users": 0, "max_products": 0, "max_pharmacies": 0}
            }
            
            limits = plan_limits.get(data.plan, {})
            tenant.max_users = limits.get("max_users", 0)
            tenant.max_products = limits.get("max_products", 0)
            tenant.max_pharmacies = limits.get("max_pharmacies", 0)
            
            if tenant.status == "trial":
                tenant.status = "active"
                tenant.activated_at = datetime.utcnow()
            
            # Mettre à jour la config
            if not tenant.config:
                tenant.config = {}
            
            tenant.config["subscription"] = {
                "plan": data.plan,
                "plan_name": data.plan.capitalize(),
                "billing_period": data.billing_period,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "last_payment_id": str(payment.id)
            }
            
            db.commit()
    
    return payment
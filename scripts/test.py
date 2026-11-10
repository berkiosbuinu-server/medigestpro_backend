# Testez directement dans Python
from app.db.session import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    result = db.execute(text("SELECT subscription_plan FROM payments LIMIT 1"))
    print("✅ La colonne existe")
except Exception as e:
    print(f"❌ Erreur: {e}")
finally:
    db.close()
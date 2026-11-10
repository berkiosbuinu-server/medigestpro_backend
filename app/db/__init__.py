# app/db/__init__.py
"""
Initialisation de la base de données
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from app.core.config import settings
from app.db.base import Base


# Créez l'engine avec les options
engine = create_engine(
    settings.DATABASE_URL,
    **settings.SQLALCHEMY_ENGINE_OPTIONS,
    echo=settings.SQLALCHEMY_ECHO
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Session thread-safe
db_session = scoped_session(SessionLocal)


def get_db():
    """Fournit une session de base de données pour les dépendances FastAPI"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialise la base de données - crée toutes les tables"""
    # Importez TOUS les modèles ici pour que SQLAlchemy les connaisse
    from app.models.tenant import Tenant
    from app.models.user import User
    from app.models.cost import Cost, Budget, Supplier
    from app.models.inventory import InventoryItem, InventorySchedule, PhysicalInventory
    from app.models.sale import Sale
    from app.models.payment import Payment
    from app.models.refund import Refund
    from app.models.invoice import Invoice
    from app.models.customer import Customer
    from app.models.product import Product
    from app.models.subscription import Subscription
    from app.models.sync_log import SyncLog
    from app.models.debt import Debt
    from app.models.debt_payment import DebtPayment
    from app.models.finance import FinancialPeriod, FinancialTransaction
    from app.models.audit_log import AuditLog
    from app.models.pharmacy import Pharmacy
    from app.models.user_pharmacy import UserPharmacy
    
    
    print("Création des tables de la base de données...")
    Base.metadata.create_all(bind=engine)
    print("Tables créées avec succès!")


def drop_db():
    """Supprime toutes les tables (à utiliser avec précaution!)"""
    Base.metadata.drop_all(bind=engine)
    print("Toutes les tables ont été supprimées!")
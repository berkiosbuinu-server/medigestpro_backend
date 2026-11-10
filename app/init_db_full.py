from datetime import datetime
from werkzeug.security import generate_password_hash
from app.db.base import Base
from app.db.session import engine, SessionLocal
from app.models.tenant import Tenant
from app.models.user import User
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    try:
        logger.info("Création des tables...")
        # Crée toutes les tables définies dans Base
        Base.metadata.create_all(bind=engine)
        logger.info("Tables créées avec succès !")
        
    except Exception as e:
        logger.error(f"Erreur lors de la création des tables: {e}")
        return

    db = SessionLocal()
    try:
        # Vérifie si un tenant existe déjà
        tenant_exist = db.query(Tenant).first()
        
        if not tenant_exist:
            logger.info("Création du tenant et de l'utilisateur admin...")
            
            # Crée le tenant
            tenant = Tenant(
                nom_pharmacie="Pharma Centrale",
                email="admin@pharma.cd",
                telephone="0999999999",
                ville="Kinshasa",
                date_creation=datetime.utcnow(),
                date_modification=datetime.utcnow()
            )
            db.add(tenant)
            db.flush()  # Pour obtenir l'ID sans commit
            
            # Crée l'utilisateur admin
            admin_user = User(
                tenant_id=tenant.id,
                nom="Admin",
                email="kiongasplane@pharma.com",
                password_hash=generate_password_hash("123456"),
                role="admin",
                actif=True,
                date_creation=datetime.utcnow()
            )
            db.add(admin_user)
            db.commit()  # Commit final
            
            logger.info(f"Tenant créé avec l'ID: {tenant.id}")
            logger.info(f"Utilisateur admin créé avec l'ID: {admin_user.id}")
        else:
            logger.info("Tenant existant trouvé, pas de création nécessaire.")
            
    except Exception as e:
        logger.error(f"Erreur lors de l'insertion initiale: {e}")
        db.rollback()
        raise  # Re-lève l'exception pour debug
    finally:
        db.close()

if __name__ == "__main__":
    init_db()
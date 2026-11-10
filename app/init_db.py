# app/init_db.py
import logging
from app.db.base import Base
from app.db.session import engine

# Import de tous tes modèles ici pour que SQLAlchemy puisse les créer
from app.models.user import User
# from app.models.tenant import Tenant  # décommente si tu as un modèle Tenant

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    logger.info("Création des tables dans la base de données...")
    Base.metadata.create_all(bind=engine)
    logger.info("Toutes les tables ont été créées avec succès !")

if __name__ == "__main__":
    init_db()

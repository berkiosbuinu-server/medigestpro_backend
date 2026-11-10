# app/create_tables.py
"""
Crée les tables manquantes dans la base de données (SQLAlchemy create_all).

Exécuté automatiquement au démarrage du conteneur (voir docker-entrypoint.sh).
Idempotent : les tables existantes ne sont pas modifiées.
Pour ajouter une colonne à une table existante, voir scripts/add_column.py.
"""
import logging
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_all_tables() -> None:
    from app.db.base import Base
    from app.db.session import engine

    # Importer tous les modèles pour les enregistrer dans Base.metadata
    import app.models  # noqa: F401

    logger.info("Création des tables manquantes...")
    Base.metadata.create_all(bind=engine)
    logger.info("✅ Schéma de base de données à jour")


if __name__ == "__main__":
    try:
        create_all_tables()
    except Exception:
        logger.exception("❌ Erreur lors de la création des tables")
        sys.exit(1)

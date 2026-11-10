# app/db/session.py
from __future__ import annotations
from dotenv import load_dotenv
load_dotenv()

import logging
import os
from typing import Generator
from sqlalchemy import text

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError
import time

logger = logging.getLogger(__name__)


def _get_database_url() -> str:
    """
    Récupère DATABASE_URL depuis l'environnement.
    Supporte l'URL Render: postgresql://... (convertie en postgresql+psycopg2://...).
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL est manquante. Définis-la dans les variables d'environnement "
            "(Render > Service > Environment) ou dans un fichier .env en local."
        )

    # Render fournit souvent: postgresql://...
    # SQLAlchemy (sync) marche très bien avec: postgresql+psycopg2://...
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    return url


DATABASE_URL: str = _get_database_url()

engine: Engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=int(os.getenv("DB_POOL_SIZE", "20")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "40")),
    pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "60")),
    echo=os.getenv("SQL_ECHO", "0") == "1",
    future=True,
    connect_args={
        "connect_timeout": 30,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
        "sslmode": "require" if "render.com" in DATABASE_URL else "prefer"
    }
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """
    Dépendance DB (1 session / requête)
    - commit auto si succès
    - rollback garanti
    - Tentative de reconnexion automatique
    """
    max_retries = 3
    retry_delay = 1  # secondes
    
    for attempt in range(max_retries):
        db: Session = None
        try:
            db = SessionLocal()
            
            # Tester la connexion
            db.execute(text("SELECT 1"))
            
            yield db
            db.commit()
            break  # Succès, sortir de la boucle
            
        except (OperationalError, SQLAlchemyTimeoutError) as e:
            if db:
                db.rollback()
            logger.warning(f"Erreur de connexion DB (tentative {attempt + 1}/{max_retries}): {e}")
            
            # Réinitialiser le pool
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                engine.dispose()  # Force la recréation des connexions
                retry_delay *= 2  # Backoff exponentiel
                continue
            else:
                logger.error("Échec de connexion après toutes les tentatives")
                raise HTTPException(
                    status_code=503,
                    detail="Service de base de données temporairement indisponible"
                ) from e
                
        except HTTPException:
            if db:
                db.rollback()
            raise
            
        except SQLAlchemyError as e:
            if db:
                db.rollback()
            logger.exception("Erreur SQLAlchemy")
            raise HTTPException(status_code=500, detail="Erreur interne de base de données") from e
            
        except Exception as e:
            if db:
                db.rollback()
            logger.exception("Erreur inattendue")
            raise
            
        finally:
            if db:
                db.close()

def check_db_connection() -> bool:
    """Vérifie si la connexion à la base de données est active"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Échec de connexion à la base de données: {e}")
        return False

def reset_db_pool():
    """Réinitialise le pool de connexions"""
    global engine
    try:
        engine.dispose()
        logger.info("Pool de connexions DB réinitialisé")
    except Exception as e:
        logger.error(f"Erreur lors de la réinitialisation du pool: {e}")
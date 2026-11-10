# app/core/config.py
import os
from datetime import timedelta
from typing import Optional, List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration de l'application avec validation Pydantic"""
    
    # =====================================
    # APPLICATION
    # =====================================
    APP_NAME: str = "MEDIGEST PRO"
    APP_VERSION: str = "1.0.0"
    DEBUG=False
    API_V1_STR: str = "/api/v1"
    
    INITIAL_SETUP_KEY: str = "MaCleSuperSecrete123!"
    
    # =====================================
    # SÉCURITÉ JWT
    # =====================================
    SECRET_KEY: str = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 43200  # 30 jours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 60       # 60 jours
    
    # =====================================
    # BASE DE DONNÉES - Configuration directe
    # =====================================
    # Support pour DATABASE_URL direct (Render.com)
    DATABASE_URL_DIRECT: Optional[str] = os.getenv("DATABASE_URL", None)
    
    # Configuration PostgreSQL individuelle (fallback)
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "postgres")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "pharma_saas")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: str = os.getenv("POSTGRES_PORT", "5432")
    
    @property
    def DATABASE_URL(self) -> str:
        """
        Retourne l'URL de la base de données avec SSL requis pour Render.com
        """
        # Priorité à DATABASE_URL si fourni (Render.com)
        if self.DATABASE_URL_DIRECT:
            url = self.DATABASE_URL_DIRECT
            
            # Pour Render.com, ajouter sslmode=require
            if "render.com" in url:
                # Vérifier si sslmode est déjà présent
                if "sslmode" not in url.lower():
                    separator = "?" if "?" not in url else "&"
                    url = f"{url}{separator}sslmode=require"
                logger.info(f"🔒 Connexion SSL activée pour Render.com")
            
            return url
        
        # Fallback sur la configuration individuelle
        url = (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )
        
        # Ajouter SSL pour Render.com même en configuration individuelle
        if "render.com" in self.POSTGRES_HOST:
            url = f"{url}?sslmode=require"
        
        return url
    
    @property
    def ASYNC_DATABASE_URL(self) -> str:
        """Version async de l'URL pour SQLAlchemy async"""
        return self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
    
    # =====================================
    # SQLALCHEMY CONFIGURATION
    # =====================================
    SQLALCHEMY_ECHO: bool = False
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    
    @property
    def SQLALCHEMY_ENGINE_OPTIONS(self) -> dict:
        """Options pour le moteur SQLAlchemy avec support SSL"""
        options = {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_timeout": 30,
            "pool_recycle": 1800,
            "pool_pre_ping": True,
            "connect_args": {
                "client_encoding": "utf8",
                "connect_timeout": 10
            }
        }
        
        # Ajouter les paramètres SSL si nécessaire
        if "render.com" in self.DATABASE_URL:
            options["connect_args"]["sslmode"] = "require"
        
        return options
    
    # =====================================
    # CORS
    # =====================================
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://*.render.com"
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: List[str] = ["*"]
    CORS_ALLOW_HEADERS: List[str] = ["*"]
    
    # =====================================
    # SAAS CONFIGURATION
    # =====================================
    DEFAULT_CURRENCY: str = "CDF"
    DEFAULT_LANGUAGE: str = "fr"
    DEFAULT_TIMEZONE: str = "Africa/Kinshasa"
    
    # =====================================
    # FILES & UPLOADS 
    # =====================================
    MEDIA_ROOT: str = os.getenv("MEDIA_ROOT", "/app/media")
    MEDIA_URL: str = os.getenv("MEDIA_URL", "/media/")
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024
    ALLOWED_EXTENSIONS: List[str] = [".jpg", ".jpeg", ".png", ".pdf", ".doc", ".docx"]
    
    # =====================================
    # EMAIL
    # =====================================
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: Optional[int] = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    EMAIL_FROM: str = "noreply@medigest.com"

    # =====================================
    # SMS & WHATSAPP CONFIGURATION
    # =====================================
    SMS_ENABLED: bool = True
    WHATSAPP_ENABLED: bool = True
    SMS_CODE_EXPIRATION_MINUTES: int = 5
    MAX_SMS_ATTEMPTS: int = 3

    # =====================================
    # TWILIO
    # =====================================
    TWILIO_SID: str = os.getenv("TWILIO_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER: str = os.getenv("TWILIO_PHONE_NUMBER", "")
    TWILIO_WHATSAPP_NUMBER: str = os.getenv("TWILIO_WHATSAPP_NUMBER", "")
    TWILIO_LOOKUP_ENABLED: bool = False 

    # =====================================
    # LOGGING
    # =====================================
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # =====================================
    # RECEIPT CONFIGURATION
    # =====================================
    GENERATE_RECEIPTS: bool = True
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore"
    }


# Instance globale des paramètres
settings = Settings()

# Configuration du logging (après settings pour utiliser LOG_LEVEL)
import logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format=settings.LOG_FORMAT
)
logger = logging.getLogger(__name__)

# Log de la configuration DB au démarrage (masquer les secrets)
logger.info(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} démarré")
logger.info(f"📊 Mode debug: {settings.DEBUG}")
# Masquer le mot de passe dans les logs
db_url_masked = settings.DATABASE_URL.replace(settings.POSTGRES_PASSWORD, "***") if settings.POSTGRES_PASSWORD in settings.DATABASE_URL else settings.DATABASE_URL
logger.info(f"🗄️ Base de données: {db_url_masked[:100]}...")
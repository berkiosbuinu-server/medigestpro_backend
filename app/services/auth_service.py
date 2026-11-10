# services/auth_service.py
import requests
import json
from typing import Optional, Dict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AuthService:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.token: Optional[str] = None
        self.current_user: Optional[Dict] = None
    
    def login(self, email: str, password: str) -> bool:
        """Authentifie l'utilisateur via l'API"""
        try:
            # Préparation des données
            login_data = {
                "email": email.strip(),
                "password": password
            }
            
            # Appel à l'API
            response = requests.post(
                f"{self.base_url}/auth/login",
                json=login_data,
                headers={"Content-Type": "application/json"}
            )
            
            logger.info(f"Login response status: {response.status_code}")
            logger.info(f"Login response: {response.text}")
            
            if response.status_code == 200:
                data = response.json()
                self.token = data.get("access_token")
                self.current_user = data.get("user")
                return True
            else:
                logger.error(f"Login failed: {response.status_code} - {response.text}")
                return False
                
        except requests.exceptions.ConnectionError:
            logger.error("Impossible de se connecter au serveur. Vérifiez que le backend est démarré.")
            return False
        except json.JSONDecodeError:
            logger.error("Réponse invalide du serveur.")
            return False
        except Exception as e:
            logger.error(f"Erreur lors de la connexion: {str(e)}")
            return False
    
    def is_authenticated(self) -> bool:
        """Vérifie si l'utilisateur est authentifié"""
        return self.token is not None
    
    def logout(self):
        """Déconnecte l'utilisateur"""
        self.token = None
        self.current_user = None
    
    def get_auth_headers(self) -> Dict[str, str]:
        """Retourne les headers d'authentification"""
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

# Instance globale
auth_service = AuthService()

# Fonction de compatibilité
def login(email: str, password: str) -> bool:
    return auth_service.login(email, password)
import requests
import time
import os

URL = os.getenv("RENDER_URL", "https://my-backend-api.onrender.com/health")

while True:
    try:
        response = requests.get(URL, timeout=10)
        print(f"Keep-alive ping: {response.status_code}")
    except Exception as e:
        print(f"Erreur: {e}")
    time.sleep(600)  # Toutes les 10 minutes
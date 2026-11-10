# app/payments/providers/mpesa.py
import requests, os

class MpesaProvider:
    BASE_URL = "https://api.vodacom.com/mpesa-rdc"

    def initiate(self, phone: str, amount: float, reference: str):
        headers = {"Authorization": f"Bearer {os.getenv('MPESA_API_TOKEN')}"}
        payload = {
            "amount": amount,
            "msisdn": phone,
            "reference": reference,
            "message": "Paiement abonnement SaaS"
        }
        resp = requests.post(f"{self.BASE_URL}/payments", json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def verify(self, reference: str):
        headers = {"Authorization": f"Bearer {os.getenv('MPESA_API_TOKEN')}"}
        resp = requests.get(f"{self.BASE_URL}/payments/{reference}", headers=headers)
        resp.raise_for_status()
        return resp.json()

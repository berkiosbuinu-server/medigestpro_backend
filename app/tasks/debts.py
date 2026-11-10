# app/tasks/debts.py
from typing import List
from app.models.debt import Debt

def send_debt_reminders_task(debts: List[Debt], tenant_id: str, user_id: str):
    # Ici tu mets le code pour envoyer les rappels (email, SMS, etc.)
    for debt in debts:
        print(f"Rappel envoyé pour la dette {debt.id}")

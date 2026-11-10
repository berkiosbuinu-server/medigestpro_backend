# app/payments/providers/base.py
from abc import ABC, abstractmethod

class PaymentProvider(ABC):
    @abstractmethod
    def initiate(self, phone: str, amount: float, reference: str):
        pass

    @abstractmethod
    def verify(self, reference: str):
        pass

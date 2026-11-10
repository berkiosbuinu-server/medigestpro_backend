from abc import ABC, abstractmethod

class PaymentGateway(ABC):

    @abstractmethod
    def initiate_payment(self, reference: str, amount: float, phone: str):
        pass

    @abstractmethod
    def check_status(self, reference: str):
        pass

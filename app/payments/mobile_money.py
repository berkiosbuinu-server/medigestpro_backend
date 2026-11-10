from app.payments.payment_gateway import PaymentGateway

class MobileMoneyGateway(PaymentGateway):

    def initiate_payment(self, reference: str, amount: float, phone: str):
        # Simulation (API réelle plus tard)
        return {
            "reference": reference,
            "amount": amount,
            "phone": phone,
            "status": "PENDING",
        }

    def check_status(self, reference: str):
        # Simulation
        return {
            "reference": reference,
            "status": "SUCCESS",
        }

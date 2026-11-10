#app/billing/invoice_generator.py
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from pathlib import Path

INVOICE_DIR = Path("pharmacie/invoices")
INVOICE_DIR.mkdir(parents=True, exist_ok=True)

def generate_invoice(invoice_id: str, tenant_name: str, amount: float):
    file_path = INVOICE_DIR / f"invoice_{invoice_id}.pdf"
    c = canvas.Canvas(str(file_path), pagesize=A4)
    c.drawString(50, 800, "FACTURE SAAS PHARMA")
    c.drawString(50, 760, f"Client : {tenant_name}")
    c.drawString(50, 720, f"Montant : {amount} USD")
    c.save()
    return file_path

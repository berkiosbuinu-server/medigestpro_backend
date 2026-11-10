# app/services/receipt.py

import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from decimal import Decimal
import jinja2
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.sale import Sale
from app.core.config import settings
from app.utils.pdf import PDFGenerator

logger = logging.getLogger(__name__)


class ReceiptService:
    """Service de génération de reçus"""
    
    def __init__(self, db: Session):
        self.db = db
        
        # Vérifier que MEDIA_ROOT est configuré
        if not hasattr(settings, 'MEDIA_ROOT') or not settings.MEDIA_ROOT:
            logger.warning("⚠️ MEDIA_ROOT non configuré dans les settings")
            self.output_dir = None
        else:
            self.output_dir = Path(settings.MEDIA_ROOT) / "receipts"
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"✅ Dossier des reçus créé: {self.output_dir}")
            except Exception as e:
                logger.error(f"❌ Erreur création dossier reçus: {e}")
                self.output_dir = None
        
        # Configuration du template Jinja2
        self.template_dir = Path(__file__).parent.parent / "templates" / "receipts"
        if self.template_dir.exists():
            self.template_env = jinja2.Environment(
                loader=jinja2.FileSystemLoader(str(self.template_dir)),
                autoescape=jinja2.select_autoescape(['html', 'xml'])
            )
        else:
            logger.warning(f"⚠️ Dossier des templates non trouvé: {self.template_dir}")
            self.template_env = None
    
    async def generate_sale_receipt(self, sale: Sale) -> Optional[str]:
        """
        Génère un reçu PDF pour une vente
        Retourne le chemin du fichier généré ou None en cas d'erreur
        """
        # Vérifier si la génération des reçus est activée
        if not getattr(settings, 'GENERATE_RECEIPTS', True):
            logger.info(f"📄 Génération de reçus désactivée pour la vente {sale.id}")
            return None
        
        # Vérifier que MEDIA_ROOT est configuré
        if not self.output_dir:
            logger.warning(f"⚠️ MEDIA_ROOT non configuré, reçu non généré pour la vente {sale.id}")
            return None
        
        # Vérifier si le reçu existe déjà
        if sale.receipt_path:
            full_path = Path(settings.MEDIA_ROOT) / sale.receipt_path
            if full_path.exists():
                logger.info(f"📄 Reçu existant: {sale.receipt_path}")
                return sale.receipt_path
        
        # Vérifier que le template est disponible
        if not self.template_env:
            logger.warning(f"⚠️ Templates non disponibles, reçu non généré")
            return None
        
        try:
            # Charger le template
            template = self.template_env.get_template("sale_receipt.html")
            
            # Préparer les données
            receipt_data = self._prepare_receipt_data(sale)
            
            # Rendre le template
            html_content = template.render(**receipt_data)
            
            # Générer le PDF
            filename = f"receipt_{sale.reference}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            output_path = self.output_dir / filename
            
            pdf_generator = PDFGenerator()
            pdf_generator.generate_from_html(
                html_content=html_content,
                output_path=str(output_path),
                options={
                    'page-size': 'A5',
                    'margin-top': '10mm',
                    'margin-right': '10mm',
                    'margin-bottom': '10mm',
                    'margin-left': '10mm',
                    'encoding': "UTF-8",
                    'no-outline': None
                }
            )
            
            # Retourner le chemin relatif
            relative_path = f"receipts/{filename}"
            logger.info(f"✅ Reçu généré: {relative_path}")
            
            return str(relative_path)
            
        except Exception as e:
            logger.error(f"❌ Erreur génération reçu pour vente {sale.id}: {str(e)}")
            # Ne pas lever d'exception, juste logger l'erreur
            return None
    
    def _prepare_receipt_data(self, sale: Sale) -> Dict[str, Any]:
        """Prépare les données pour le reçu"""
        company_info = {}
        
        # Récupérer les infos du tenant si disponible
        if hasattr(sale, 'tenant') and sale.tenant:
            company_info = {
                "name": sale.tenant.company_name or "Pharmacie SAAS",
                "address": sale.tenant.address or "Adresse non spécifiée",
                "phone": sale.tenant.phone or "N/A",
                "email": sale.tenant.email or "N/A",
                "rc": sale.tenant.registration_number or "N/A",
                "nif": sale.tenant.tax_id or "N/A",
                "stat": sale.tenant.stat_number or "N/A",
            }
        else:
            company_info = {
                "name": "Pharmacie SAAS",
                "address": "Adresse non spécifiée",
                "phone": "N/A",
                "email": "N/A",
                "rc": "N/A",
                "nif": "N/A",
                "stat": "N/A",
            }
        
        # Formatage des montants
        def format_amount(amount: Decimal) -> str:
            return f"{amount:,.2f}".replace(",", " ").replace(".", ",")
        
        receipt_data = {
            "receipt_number": sale.reference,
            "invoice_number": sale.invoice_number or sale.reference,
            "date": sale.created_at.strftime("%d/%m/%Y %H:%M"),
            "company": company_info,
            "customer": {
                "name": sale.client_name or "Client non renseigné",
                "phone": sale.client_phone or "N/A",
                "address": sale.client.address if hasattr(sale, 'client') and sale.client else "N/A"
            },
            "seller": sale.seller_name or "N/A",
            "payment_method": self._get_payment_method_label(sale.payment_method),
            "payment_reference": sale.reference_payment or "N/A",
            "items": [],
            "totals": {
                "subtotal": format_amount(sale.subtotal),
                "discount": format_amount(sale.total_discount),
                "tva": format_amount(sale.total_tva),
                "total": format_amount(sale.total_amount)
            },
            "amount_paid": format_amount(getattr(sale, 'amount_paid', 0)),
            "amount_due": format_amount(getattr(sale, 'amount_due', 0)),
            "is_credit": getattr(sale, 'is_credit', False),
            "credit_due_date": sale.credit_due_date.strftime("%d/%m/%Y") if getattr(sale, 'credit_due_date', None) else "N/A",
            "notes": sale.notes or "",
            "footer_text": "Merci de votre confiance !\nConservez ce reçu pour tout échange ou retour."
        }
        
        # Ajouter les articles
        if hasattr(sale, 'items') and sale.items:
            for item in sale.items:
                receipt_data["items"].append({
                    "code": item.product_code,
                    "name": item.product_name,
                    "quantity": item.quantity,
                    "unit_price": format_amount(item.unit_price),
                    "discount": f"{item.discount_percent}%" if item.discount_percent > 0 else "0%",
                    "total": format_amount(item.total)
                })
        
        return receipt_data
    
    def _get_payment_method_label(self, method: str) -> str:
        """Retourne le libellé du mode de paiement"""
        labels = {
            "cash": "Espèces",
            "mobile_money": "Mobile Money",
            "card": "Carte Bancaire",
            "check": "Chèque",
            "bank_transfer": "Virement Bancaire",
            "credit": "Crédit",
            "multiple": "Multiple"
        }
        return labels.get(method, method)
    
    async def generate_refund_receipt(self, refund_id: UUID) -> Optional[str]:
        """Génère un reçu de remboursement"""
        # Implémentation similaire
        pass
    
    async def get_receipt_path(self, sale_id: UUID, tenant_id: UUID) -> Optional[str]:
        """Récupère le chemin du reçu"""
        sale = self.db.query(Sale).filter(
            Sale.id == sale_id,
            Sale.tenant_id == tenant_id
        ).first()
        
        if sale and sale.receipt_path and self.output_dir:
            full_path = Path(settings.MEDIA_ROOT) / sale.receipt_path
            if full_path.exists():
                return str(full_path)
        
        return None
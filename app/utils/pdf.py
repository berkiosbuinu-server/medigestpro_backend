# app/utils/pdf.py
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List, Union
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class PDFGenerator:
    """Générateur de PDF utilisant wkhtmltopdf ou WeasyPrint"""
    
    def __init__(self, wkhtmltopdf_path: Optional[str] = None):
        """
        Args:
            wkhtmltopdf_path: Chemin vers l'exécutable wkhtmltopdf
        """
        self.wkhtmltopdf_path = wkhtmltopdf_path or self._find_wkhtmltopdf()
        
        if not self.wkhtmltopdf_path:
            logger.warning("wkhtmltopdf non trouvé. Essayez WeasyPrint.")
    
    def _find_wkhtmltopdf(self) -> Optional[str]:
        """Trouve le chemin de wkhtmltopdf"""
        possible_paths = [
            '/usr/bin/wkhtmltopdf',
            '/usr/local/bin/wkhtmltopdf',
            'C:/Program Files/wkhtmltopdf/bin/wkhtmltopdf.exe',
            'C:/Program Files/wkhtmltox/bin/wkhtmltopdf.exe',
            os.environ.get('WKHTMLTOPDF_PATH', '')
        ]
        
        for path in possible_paths:
            if path and os.path.exists(path):
                logger.info(f"wkhtmltopdf trouvé à: {path}")
                return path
        
        # Essayer avec which/where
        try:
            import shutil
            found = shutil.which('wkhtmltopdf')
            if found:
                logger.info(f"wkhtmltopdf trouvé via which: {found}")
            return found
        except Exception as e:
            logger.debug(f"Erreur recherche wkhtmltopdf: {e}")
            return None
    
    def generate_from_html(
        self,
        html_content: str,
        output_path: str,
        options: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Génère un PDF à partir de HTML
        
        Args:
            html_content: Contenu HTML
            output_path: Chemin de sortie
            options: Options wkhtmltopdf
        
        Returns:
            True si succès, False sinon
        """
        # Essayer wkhtmltopdf d'abord
        if self.wkhtmltopdf_path:
            success = self._generate_with_wkhtmltopdf(html_content, output_path, options)
            if success:
                return True
        
        # Fallback: WeasyPrint
        success = self._generate_with_weasyprint(html_content, output_path)
        if success:
            return True
        
        # Fallback: ReportLab
        success = self._generate_with_reportlab(html_content, output_path)
        return success
    
    def _generate_with_wkhtmltopdf(
        self,
        html_content: str,
        output_path: str,
        options: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Génère avec wkhtmltopdf"""
        if not self.wkhtmltopdf_path:
            return False
        
        default_options = {
            'quiet': True,
            'page-size': 'A4',
            'margin-top': '10mm',
            'margin-right': '10mm',
            'margin-bottom': '10mm',
            'margin-left': '10mm',
            'encoding': "UTF-8",
            'no-outline': None,
            'enable-local-file-access': None,
            'disable-smart-shrinking': None
        }
        
        if options:
            default_options.update(options)
        
        # Créer un fichier HTML temporaire
        with tempfile.NamedTemporaryFile(
            mode='w', 
            suffix='.html', 
            delete=False, 
            encoding='utf-8'
        ) as f:
            f.write(html_content)
            html_file = f.name
        
        try:
            # Construire la commande
            cmd = [self.wkhtmltopdf_path]
            
            for key, value in default_options.items():
                if value is None:
                    cmd.append(f'--{key}')
                else:
                    cmd.append(f'--{key}')
                    cmd.append(str(value))
            
            cmd.extend([html_file, output_path])
            
            # Exécuter la commande
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                logger.error(f"Erreur wkhtmltopdf: {result.stderr[:500]}")
                return False
            
            logger.info(f"PDF généré avec wkhtmltopdf: {output_path}")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("Timeout génération PDF avec wkhtmltopdf")
            return False
        except Exception as e:
            logger.error(f"Erreur génération PDF wkhtmltopdf: {str(e)}")
            return False
        finally:
            # Nettoyer le fichier temporaire
            try:
                os.unlink(html_file)
            except:
                pass
    
    def _generate_with_weasyprint(
        self,
        html_content: str,
        output_path: str
    ) -> bool:
        """Génère avec WeasyPrint (alternative moderne)"""
        try:
            # Import conditionnel
            from weasyprint import HTML, CSS
            from weasyprint.text.fonts import FontConfiguration
            
            font_config = FontConfiguration()
            
            # Créer un fichier HTML temporaire
            with tempfile.NamedTemporaryFile(
                mode='w', 
                suffix='.html', 
                delete=False, 
                encoding='utf-8'
            ) as f:
                f.write(html_content)
                html_file = f.name
            
            try:
                # Générer le PDF
                html = HTML(filename=html_file)
                
                # CSS pour améliorer l'impression
                css = CSS(string='''
                    @page {
                        size: A4;
                        margin: 10mm;
                    }
                    body {
                        font-family: Arial, sans-serif;
                        font-size: 12px;
                    }
                ''', font_config=font_config)
                
                html.write_pdf(output_path, stylesheets=[css])
                logger.info(f"PDF généré avec WeasyPrint: {output_path}")
                return True
                
            finally:
                try:
                    os.unlink(html_file)
                except:
                    pass
                    
        except ImportError:
            logger.warning("WeasyPrint non installé. Installez avec: pip install weasyprint")
            return False
        except Exception as e:
            logger.error(f"Erreur génération PDF WeasyPrint: {str(e)}")
            return False
    
    def _generate_with_reportlab(
        self,
        html_content: str,
        output_path: str
    ) -> bool:
        """Génère avec ReportLab (fallback basique)"""
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import cm
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            
            # Créer le document
            doc = SimpleDocTemplate(
                output_path,
                pagesize=A4,
                rightMargin=2*cm,
                leftMargin=2*cm,
                topMargin=2*cm,
                bottomMargin=2*cm
            )
            
            # Styles
            styles = getSampleStyleSheet()
            story = []
            
            # Convertir HTML simple en paragraphes
            # Note: Cette conversion est basique, pour HTML complexe utilisez xhtml2pdf
            for line in html_content.split('\n'):
                if line.strip():
                    p = Paragraph(line.strip(), styles['Normal'])
                    story.append(p)
                    story.append(Spacer(1, 12))
            
            # Construire le PDF
            doc.build(story)
            logger.info(f"PDF généré avec ReportLab: {output_path}")
            return True
            
        except ImportError:
            logger.warning("ReportLab non installé. Installez avec: pip install reportlab")
            return False
        except Exception as e:
            logger.error(f"Erreur génération PDF ReportLab: {str(e)}")
            return False
    
    def generate_from_url(
        self,
        url: str,
        output_path: str,
        options: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Génère un PDF à partir d'une URL"""
        if not self.wkhtmltopdf_path:
            logger.error("wkhtmltopdf requis pour générer depuis URL")
            return False
        
        default_options = {
            'quiet': True,
            'page-size': 'A4'
        }
        
        if options:
            default_options.update(options)
        
        try:
            cmd = [self.wkhtmltopdf_path]
            
            for key, value in default_options.items():
                if value is None:
                    cmd.append(f'--{key}')
                else:
                    cmd.append(f'--{key}')
                    cmd.append(str(value))
            
            cmd.extend([url, output_path])
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                logger.error(f"Erreur wkhtmltopdf URL: {result.stderr[:500]}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Erreur génération PDF depuis URL: {str(e)}")
            return False
    
    def generate_multiple_pdfs(
        self,
        html_contents: List[str],
        output_dir: str,
        filename_prefix: str = "document"
    ) -> List[str]:
        """
        Génère plusieurs PDFs
        
        Returns:
            Liste des chemins des fichiers générés
        """
        generated_files = []
        
        for i, html_content in enumerate(html_contents):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{filename_prefix}_{i+1}_{timestamp}.pdf"
            output_path = os.path.join(output_dir, filename)
            
            success = self.generate_from_html(html_content, output_path)
            if success:
                generated_files.append(output_path)
        
        return generated_files


# Classe utilitaire pour générer des reçus spécifiques
class ReceiptPDFGenerator(PDFGenerator):
    """Générateur spécialisé pour les reçus"""
    
    def generate_sale_receipt(
        self,
        receipt_data: Dict[str, Any],
        output_path: str,
        template_path: Optional[str] = None
    ) -> bool:
        """Génère un reçu de vente"""
        # Charger le template
        if template_path and os.path.exists(template_path):
            with open(template_path, 'r', encoding='utf-8') as f:
                template = f.read()
        else:
            # Template par défaut
            template = self._get_default_receipt_template()
        
        # Remplir le template
        html_content = self._fill_receipt_template(template, receipt_data)
        
        # Options spécifiques pour les reçus
        options = {
            'page-size': 'A5',
            'margin-top': '5mm',
            'margin-right': '5mm',
            'margin-bottom': '5mm',
            'margin-left': '5mm',
            'orientation': 'Portrait'
        }
        
        return self.generate_from_html(html_content, output_path, options)
    
    def _get_default_receipt_template(self) -> str:
        """Retourne un template HTML par défaut pour reçu"""
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body { font-family: Arial, sans-serif; font-size: 12px; }
                .header { text-align: center; margin-bottom: 20px; }
                .company { font-weight: bold; font-size: 14px; }
                .receipt-info { margin: 15px 0; }
                .items { width: 100%; border-collapse: collapse; }
                .items th, .items td { padding: 5px; border-bottom: 1px solid #ddd; }
                .total { font-weight: bold; font-size: 13px; }
                .footer { margin-top: 30px; text-align: center; font-size: 10px; }
            </style>
        </head>
        <body>
            <div class="header">
                <div class="company">{{company_name}}</div>
                <div>{{company_address}}</div>
                <div>Tél: {{company_phone}}</div>
            </div>
            
            <div class="receipt-info">
                <div><strong>Reçu N°:</strong> {{receipt_number}}</div>
                <div><strong>Date:</strong> {{date}}</div>
                <div><strong>Client:</strong> {{customer_name}}</div>
            </div>
            
            <table class="items">
                <thead>
                    <tr>
                        <th>Article</th>
                        <th>Qté</th>
                        <th>Prix</th>
                        <th>Total</th>
                    </tr>
                </thead>
                <tbody>
                    {{#items}}
                    <tr>
                        <td>{{name}}</td>
                        <td>{{quantity}}</td>
                        <td>{{unit_price}}</td>
                        <td>{{total}}</td>
                    </tr>
                    {{/items}}
                </tbody>
            </table>
            
            <div style="margin-top: 20px;">
                <div class="total">Total: {{total_amount}}</div>
                <div>Mode paiement: {{payment_method}}</div>
            </div>
            
            <div class="footer">
                Merci de votre visite !<br>
                Reçu généré le {{generated_date}}
            </div>
        </body>
        </html>
        """
    
    def _fill_receipt_template(self, template: str, data: Dict[str, Any]) -> str:
        """Remplit le template avec les données"""
        # Simple remplacement pour l'exemple
        # Dans une vraie implémentation, utilisez un moteur de template comme Jinja2
        html = template
        for key, value in data.items():
            placeholder = "{{" + key + "}}"
            html = html.replace(placeholder, str(value))
        return html


# Fonction utilitaire simple
def generate_pdf_from_html(
    html_content: str,
    output_path: str,
    use_wkhtmltopdf: bool = True
) -> bool:
    """
    Fonction utilitaire simple pour générer un PDF
    
    Args:
        html_content: Contenu HTML
        output_path: Chemin de sortie
        use_wkhtmltopdf: Essayer wkhtmltopdf en premier
    
    Returns:
        True si succès
    """
    generator = PDFGenerator()
    return generator.generate_from_html(html_content, output_path)


def check_pdf_dependencies() -> Dict[str, bool]:
    """Vérifie les dépendances PDF disponibles"""
    dependencies = {
        'wkhtmltopdf': False,
        'weasyprint': False,
        'reportlab': False
    }
    
    # Vérifier wkhtmltopdf
    try:
        generator = PDFGenerator()
        dependencies['wkhtmltopdf'] = generator.wkhtmltopdf_path is not None
    except:
        pass
    
    # Vérifier WeasyPrint
    try:
        import weasyprint
        dependencies['weasyprint'] = True
    except ImportError:
        pass
    
    # Vérifier ReportLab
    try:
        import reportlab
        dependencies['reportlab'] = True
    except ImportError:
        pass
    
    return dependencies
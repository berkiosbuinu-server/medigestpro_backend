# app/services/notification_service.py
"""Service de notification avec Twilio pour SMS et WhatsApp"""

import logging
import os
import re
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.core.config import settings
from app.models.sale import Sale
from twilio.rest import Client as TwilioClient
from app.models.customer import Customer as CustomerModel
from app.models.user import User

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Service de notifications pour l'application MEDIGEST
    Gère les SMS, WhatsApp, emails et notifications internes
    """
    
    def __init__(self, db: Session = None):
        self.db = db
        self._twilio_client = None
        self._smtp_client = None
        self._initialize_clients()
    
    def _initialize_clients(self):
        """Initialise les clients de notification"""
        try:
            # Initialisation Twilio avec fallback
            twilio_sid = os.getenv("TWILIO_ACCOUNT_SID") or os.getenv("TWILIO_SID")
            twilio_auth = os.getenv("TWILIO_AUTH_TOKEN")
            
            if twilio_sid and twilio_auth:
                self._twilio_client = TwilioClient(twilio_sid, twilio_auth)
                logger.info("✅ Client Twilio initialisé avec succès")
                logger.info(f"📱 Numéro SMS: {self._get_twilio_phone_number()}")
                logger.info(f"💬 Numéro WhatsApp: {self._get_twilio_whatsapp_number()}")
            else:
                logger.warning("❌ Credentials Twilio manquants")
                self._twilio_client = None
            
            # Initialisation SMTP pour emails
            if (hasattr(settings, "SMTP_HOST") and settings.SMTP_HOST and
                hasattr(settings, "SMTP_PORT") and settings.SMTP_PORT and
                hasattr(settings, "SMTP_USER") and settings.SMTP_USER and
                hasattr(settings, "SMTP_PASSWORD") and settings.SMTP_PASSWORD):
                
                self._smtp_client = {
                    "host": settings.SMTP_HOST,
                    "port": settings.SMTP_PORT,
                    "user": settings.SMTP_USER,
                    "password": settings.SMTP_PASSWORD
                }
                logger.info("✅ Client SMTP configuré avec succès")
            else:
                logger.warning("❌ SMTP non configuré - les emails ne seront pas envoyés")
                
        except Exception as e:
            logger.error(f"❌ Erreur initialisation clients de notification: {e}")
            self._twilio_client = None
            self._smtp_client = None
    
    def _get_twilio_phone_number(self) -> str:
        """Retourne le numéro Twilio SMS"""
        return os.getenv("TWILIO_PHONE_NUMBER") or getattr(settings, "TWILIO_PHONE_NUMBER", "")
    
    def _get_twilio_whatsapp_number(self) -> str:
        """Retourne le numéro Twilio WhatsApp"""
        return os.getenv("TWILIO_WHATSAPP_NUMBER", 
                        getattr(settings, "TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886"))
    
    def send_sms_with_fallback(self, to: str, body: str, fallback_whatsapp: bool = True) -> Dict[str, Any]:
        """
        Envoie un SMS avec fallback WhatsApp
        Retourne: {'success': bool, 'method': 'sms'|'whatsapp'|None, 'error': str, 'sid': str}
        """
        result = {
            'success': False,
            'method': None,
            'error': None,
            'sid': None
        }
        
        if not self._twilio_client:
            result['error'] = "Twilio non initialisé"
            logger.error(result['error'])
            return result
        
        # Formater le numéro
        formatted_phone = self._format_phone_for_sms(to)
        if not formatted_phone:
            result['error'] = f"Numéro invalide: {to}"
            logger.error(result['error'])
            return result
        
        from_phone = self._get_twilio_phone_number()
        if not from_phone:
            result["error"] = "TWILIO_PHONE_NUMBER non configuré"
            logger.error(result["error"])
            return result
        
        # Essayer SMS d'abord
        try:
            logger.info(f"📤 Tentative d'envoi SMS à {formatted_phone}")
            
            sms_message = self._twilio_client.messages.create(
                body=body,
                from_=from_phone,
                to=formatted_phone
            )
            
            logger.info(f"✅ SMS envoyé avec succès: SID={sms_message.sid}")
            result['success'] = True
            result['method'] = 'sms'
            result['sid'] = sms_message.sid
            return result
            
        except Exception as sms_error:
            error_msg = str(sms_error)
            logger.error(f"❌ Erreur envoi SMS: {error_msg}")
            
            # Si fallback WhatsApp activé, essayer WhatsApp
            if fallback_whatsapp:
                logger.info("🔄 Tentative de fallback WhatsApp...")
                whatsapp_result = self.send_whatsapp(to, body)
                
                if whatsapp_result['success']:
                    logger.info("✅ Fallback WhatsApp réussi")
                    return whatsapp_result
                else:
                    result['error'] = f"SMS: {error_msg}, WhatsApp: {whatsapp_result['error']}"
            else:
                result['error'] = error_msg
            
            return result
    
    def send_whatsapp(self, to: str, body: str, from_: str = None) -> Dict[str, Any]:
        """
        Envoie un message WhatsApp via Twilio avec résultat détaillé
        Retourne: {'success': bool, 'method': 'whatsapp', 'error': str, 'sid': str}
        """
        result = {
            'success': False,
            'method': 'whatsapp',
            'error': None,
            'sid': None
        }
        
        if not self._twilio_client:
            result['error'] = "Twilio non initialisé"
            logger.error(result['error'])
            return result
        
        # Formater pour WhatsApp
        formatted_phone = self._format_phone_for_whatsapp(to)
        if not formatted_phone:
            result['error'] = f"Numéro invalide pour WhatsApp: {to}"
            logger.error(result['error'])
            return result
        
        from_whatsapp = from_ or self._get_twilio_whatsapp_number()
        
        try:
            logger.info(f"📤 Tentative d'envoi WhatsApp à {formatted_phone}")
            
            whatsapp_message = self._twilio_client.messages.create(
                body=body,
                from_=from_whatsapp,
                to=formatted_phone
            )
            
            logger.info(f"✅ WhatsApp envoyé avec succès: SID={whatsapp_message.sid}")
            result['success'] = True
            result['sid'] = whatsapp_message.sid
            return result
            
        except Exception as whatsapp_error:
            error_msg = str(whatsapp_error)
            logger.error(f"❌ Erreur envoi WhatsApp: {error_msg}")
            result['error'] = error_msg
            return result
    
    def _format_phone_for_sms(self, phone: str) -> Optional[str]:
        """Formate un numéro pour Twilio SMS"""
        if not phone:
            return None
        
        # Nettoyer le numéro
        phone = re.sub(r'\D', '', phone)
        
        if not phone:
            return None
        
        # Format Congo (RDC) - 9 chiffres
        if len(phone) == 9:
            return f"+243{phone}"
        elif len(phone) == 11 and phone.startswith('243'):
            return f"+{phone}"
        elif len(phone) == 12 and phone.startswith('243'):
            return f"+{phone}"
        elif phone.startswith('+'):
            return phone
        else:
            # Essayer de deviner
            if phone.startswith('0') and len(phone) == 10:
                return f"+243{phone[1:]}"
            elif len(phone) >= 9:
                return f"+243{phone[-9:]}"
        
        return None
    
    def _format_phone_for_whatsapp(self, phone: str) -> Optional[str]:
        """Formate un numéro pour WhatsApp"""
        formatted = self._format_phone_for_sms(phone)
        if formatted:
            return f"whatsapp:{formatted}"
        return None
    
    async def send_sale_confirmation(self, sale: Sale) -> Dict[str, Any]:
        """
        Envoie une confirmation de vente au vendeur et gestionnaires
        """
        try:
            result = {
                "to_seller": False,
                "to_managers": False,
                "messages": []
            }
            
            # Récupérer le vendeur
            seller = self.db.query(User).filter(User.id == sale.created_by).first()
            
            # Notification au vendeur
            if seller and seller.telephone:
                message_body = (
                    f"✅ Vente #{sale.reference} confirmée\n"
                    f"Montant: {sale.total_amount:.2f} {settings.DEFAULT_CURRENCY}\n"
                    f"Client: {sale.client_name}\n"
                    f"Méthode: {sale.payment_method}\n"
                    f"Date: {sale.created_at.strftime('%d/%m/%Y %H:%M')}"
                )
                
                # Utiliser la nouvelle méthode avec fallback
                sms_result = self.send_sms_with_fallback(seller.telephone, message_body)
                
                if sms_result['success']:
                    result["to_seller"] = True
                    result["messages"].append({
                        "recipient": seller.nom_complet,
                        "type": sms_result['method'],
                        "status": "sent",
                        "message_id": sms_result['sid']
                    })
            
            # Notification aux gestionnaires pour les grosses ventes
            if sale.total_amount > 100000:  # Seuil configurable
                managers = self.db.query(User).filter(
                    User.role.in_(["admin", "gerant"]),
                    User.is_active == True,
                    User.telephone.isnot(None)
                ).all()
                
                for manager in managers:
                    if manager.id != seller.id:  # Ne pas notifier le vendeur à nouveau
                        manager_message = (
                            f"💰 Vente importante #{sale.reference}\n"
                            f"Montant: {sale.total_amount:.2f} {settings.DEFAULT_CURRENCY}\n"
                            f"Vendeur: {seller.nom_complet if seller else 'N/A'}\n"
                            f"Client: {sale.client_name}"
                        )
                        
                        # Utiliser la nouvelle méthode avec fallback
                        sms_result = self.send_sms_with_fallback(manager.telephone, manager_message)
                        
                        if sms_result['success']:
                            result["to_managers"] = True
                            result["messages"].append({
                                "recipient": manager.nom_complet,
                                "type": sms_result['method'],
                                "status": "sent",
                                "message_id": sms_result['sid']
                            })
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Erreur envoi confirmation vente: {e}")
            return {"error": str(e)}
    
    async def send_customer_receipt(self, sale: Sale) -> Dict[str, Any]:
        """
        Envoie le reçu au client
        """
        try:
            result = {
                "sms_sent": False,
                "whatsapp_sent": False,
                "email_sent": False,
                "message_id": None,
                "method": None
            }
            
            # Préparer le message du reçu
            receipt_body = (
                f"📋 Reçu de votre achat chez {settings.APP_NAME}\n"
                f"--------------------------------\n"
                f"Référence: {sale.reference}\n"
                f"Date: {sale.created_at.strftime('%d/%m/%Y %H:%M')}\n"
                f"Montant: {sale.total_amount:.2f} {settings.DEFAULT_CURRENCY}\n"
                f"Méthode: {sale.payment_method}\n"
                f"Merci pour votre confiance !"
            )
            
            # Envoi SMS avec fallback WhatsApp
            if sale.client_phone:
                sms_result = self.send_sms_with_fallback(sale.client_phone, receipt_body)
                
                if sms_result['success']:
                    result["sms_sent"] = True
                    result["whatsapp_sent"] = (sms_result['method'] == 'whatsapp')
                    result["message_id"] = sms_result['sid']
                    result["method"] = sms_result['method']
            
            # Envoi email si disponible
            if hasattr(sale, 'client_email') and sale.client_email:
                email_subject = f"Reçu de votre achat #{sale.reference}"
                email_body = f"""
                <html>
                <body>
                    <h2>Reçu de votre achat</h2>
                    <p><strong>Référence:</strong> {sale.reference}</p>
                    <p><strong>Date:</strong> {sale.created_at.strftime('%d/%m/%Y %H:%M')}</p>
                    <p><strong>Montant:</strong> {sale.total_amount:.2f} {settings.DEFAULT_CURRENCY}</p>
                    <p><strong>Méthode de paiement:</strong> {sale.payment_method}</p>
                    <p>Merci pour votre confiance !</p>
                    <p>Cordialement,<br>L'équipe {settings.APP_NAME}</p>
                </body>
                </html>
                """
                if self.send_email(to=sale.client_email, subject=email_subject, body=email_body):
                    result["email_sent"] = True
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Erreur envoi reçu client: {e}")
            return {"error": str(e)}
    
    async def send_stock_alert(self, product_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Envoie une alerte de stock bas
        """
        try:
            managers = self.db.query(User).filter(
                User.role.in_(["admin", "gerant", "pharmacien"]),
                User.is_active == True,
                User.telephone.isnot(None)
            ).all()
            
            alert_body = (
                f"⚠️ ALERTE STOCK - {product_data['product_name']}\n"
                f"Code: {product_data['product_code']}\n"
                f"Stock restant: {product_data['current_stock']}\n"
                f"Seuil: {product_data['alert_threshold']}\n"
                f"Status: {product_data['status'].upper()}"
            )
            
            results = []
            for manager in managers:
                # Utiliser la nouvelle méthode avec fallback
                sms_result = self.send_sms_with_fallback(manager.telephone, alert_body)
                
                results.append({
                    "manager": manager.nom_complet,
                    "sent": sms_result['success'],
                    "method": sms_result['method'],
                    "error": sms_result['error']
                })
            
            return {
                "alert_type": "low_stock",
                "product": product_data['product_name'],
                "results": results,
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ Erreur envoi alerte stock: {e}")
            return {"error": str(e)}
    
    async def send_expiry_alert(self, expiry_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Envoie une alerte de péremption
        """
        try:
            if not expiry_data:
                return {"message": "Aucun produit en alerte"}
            
            # Grouper par jours restants
            critical = [p for p in expiry_data if p['days_remaining'] <= 7]
            warning = [p for p in expiry_data if 7 < p['days_remaining'] <= 30]
            
            managers = self.db.query(User).filter(
                User.role.in_(["admin", "gerant", "pharmacien"]),
                User.is_active == True,
                User.telephone.isnot(None)
            ).all()
            
            results = []
            for manager in managers:
                # Message pour produits critiques
                if critical:
                    critical_body = (
                        f"🚨 ALERTE CRITIQUE - Produits périmes bientôt\n"
                        f"--------------------------------\n"
                    )
                    for product in critical[:3]:  # Limiter à 3 produits
                        critical_body += (
                            f"- {product['product_name']}: "
                            f"{product['days_remaining']} jour(s)\n"
                        )
                    
                    sms_result = self.send_sms_with_fallback(manager.telephone, critical_body)
                    
                    results.append({
                        "manager": manager.nom_complet,
                        "type": "critical",
                        "sent": sms_result['success'],
                        "method": sms_result['method'],
                        "error": sms_result['error']
                    })
                
                # Message pour avertissements
                if warning and not critical:  # Envoyer seulement si pas d'alerte critique
                    warning_body = (
                        f"⚠️ AVERTISSEMENT - Produits approchant péremption\n"
                        f"--------------------------------\n"
                    )
                    for product in warning[:3]:
                        warning_body += (
                            f"- {product['product_name']}: "
                            f"{product['days_remaining']} jour(s)\n"
                        )
                    
                    sms_result = self.send_sms_with_fallback(manager.telephone, warning_body)
                    
                    results.append({
                        "manager": manager.nom_complet,
                        "type": "warning",
                        "sent": sms_result['success'],
                        "method": sms_result['method'],
                        "error": sms_result['error']
                    })
            
            return {
                "alert_type": "expiry",
                "critical_count": len(critical),
                "warning_count": len(warning),
                "results": results
            }
            
        except Exception as e:
            logger.error(f"❌ Erreur envoi alerte péremption: {e}")
            return {"error": str(e)}
    
    async def send_credit_payment_reminder(self, client: CustomerModel) -> Dict[str, Any]:
        """
        Envoie un rappel de paiement crédit
        """
        try:
            if not client.telephone:
                return {"error": "Client sans numéro de téléphone"}
            
            overdue_invoices = [
                sale for sale in client.sales 
                if sale.is_credit and sale.credit_due_date < datetime.utcnow().date()
                and sale.status != "paid"
            ]
            
            if not overdue_invoices:
                return {"message": "Aucune facture en retard"}
            
            total_overdue = sum(sale.total_amount for sale in overdue_invoices)
            
            reminder_body = (
                f"🔔 Rappel de paiement - {settings.APP_NAME}\n"
                f"--------------------------------\n"
                f"Cher(e) {client.nom_complet},\n"
                f"Vous avez {len(overdue_invoices)} facture(s) en retard.\n"
                f"Montant total dû: {total_overdue:.2f} {settings.DEFAULT_CURRENCY}\n"
                f"Veuillez régulariser votre situation.\n"
                f"Merci."
            )
            
            # Utiliser la nouvelle méthode avec fallback
            sms_result = self.send_sms_with_fallback(client.telephone, reminder_body)
            
            if sms_result['success']:
                return {
                    "sent": True,
                    "client": client.nom_complet,
                    "overdue_count": len(overdue_invoices),
                    "total_amount": float(total_overdue),
                    "method": sms_result['method'],
                    "message_id": sms_result['sid']
                }
            
            return {
                "sent": False,
                "error": sms_result['error']
            }
            
        except Exception as e:
            logger.error(f"❌ Erreur envoi rappel crédit: {e}")
            return {"error": str(e)}
    
    def send_sms(self, to: str, body: str, from_: str = None) -> bool:
        """
        Fonction de compatibilité pour l'envoi de SMS (sans fallback)
        """
        if not self._twilio_client:
            logger.warning("Twilio non configuré, SMS non envoyé")
            return False
        
        try:
            # Formater le numéro
            formatted_phone = self._format_phone_for_sms(to)
            if not formatted_phone:
                logger.error(f"Numéro invalide: {to}")
                return False
            
            # Utiliser le numéro Twilio par défaut si non spécifié
            from_number = from_ or self._get_twilio_phone_number()
            if not from_number:
                logger.error("Numéro Twilio d'envoi non configuré")
                return False
            
            message = self._twilio_client.messages.create(
                body=body,
                from_=from_number,
                to=formatted_phone
            )
            
            logger.info(f"✅ SMS envoyé à {formatted_phone}: {message.sid}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Erreur envoi SMS à {to}: {e}")
            return False
    
    def send_email(self, to: str, subject: str, body: str, 
                  from_: str = None, is_html: bool = True) -> bool:
        """
        Envoie un email via SMTP
        """
        if not self._smtp_client:
            logger.warning("SMTP non configuré, email non envoyé")
            return False
        
        try:
            # Préparer le message
            msg = MIMEMultipart()
            msg['From'] = from_ or getattr(settings, "EMAIL_FROM", "noreply@medigest.com")
            msg['To'] = to
            msg['Subject'] = subject
            
            # Ajouter le corps du message
            if is_html:
                msg.attach(MIMEText(body, 'html'))
            else:
                msg.attach(MIMEText(body, 'plain'))
            
            # Connexion SMTP et envoi
            with smtplib.SMTP(self._smtp_client["host"], self._smtp_client["port"]) as server:
                server.starttls()
                server.login(self._smtp_client["user"], self._smtp_client["password"])
                server.send_message(msg)
            
            logger.info(f"✅ Email envoyé à {to}: {subject}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Erreur envoi email à {to}: {e}")
            return False
    
    async def send_bulk_notifications(self, 
                                     notifications: List[Dict[str, Any]],
                                     notification_type: str = "generic") -> Dict[str, Any]:
        """
        Envoie des notifications en masse
        """
        try:
            results = {
                "total": len(notifications),
                "successful": 0,
                "failed": 0,
                "details": []
            }
            
            for notification in notifications:
                try:
                    if notification_type == "sms":
                        # Utiliser la nouvelle méthode avec fallback
                        sms_result = self.send_sms_with_fallback(
                            to=notification.get('to'),
                            body=notification.get('body')
                        )
                        sent = sms_result['success']
                        method = sms_result['method']
                    elif notification_type == "whatsapp":
                        whatsapp_result = self.send_whatsapp(
                            to=notification.get('to'),
                            body=notification.get('body')
                        )
                        sent = whatsapp_result['success']
                        method = whatsapp_result['method']
                    elif notification_type == "email":
                        sent = self.send_email(
                            to=notification.get('to'),
                            subject=notification.get('subject', 'Notification'),
                            body=notification.get('body'),
                            is_html=notification.get('is_html', True)
                        )
                        method = "email"
                    else:
                        sent = False
                        method = None
                    
                    if sent:
                        results["successful"] += 1
                    else:
                        results["failed"] += 1
                    
                    results["details"].append({
                        "recipient": notification.get('to'),
                        "sent": sent,
                        "method": method
                    })
                    
                except Exception as e:
                    results["failed"] += 1
                    results["details"].append({
                        "recipient": notification.get('to'),
                        "error": str(e)
                    })
            
            return results
            
        except Exception as e:
            logger.error(f"❌ Erreur notifications en masse: {e}")
            return {"error": str(e)}
    
    def get_notification_status(self) -> Dict[str, Any]:
        """
        Retourne le statut des services de notification
        """
        return {
            "twilio_configured": self._twilio_client is not None,
            "smtp_configured": self._smtp_client is not None,
            "sms_enabled": hasattr(settings, "SMS_ENABLED") and settings.SMS_ENABLED,
            "whatsapp_enabled": hasattr(settings, "WHATSAPP_ENABLED") and settings.WHATSAPP_ENABLED,
            "email_enabled": self._smtp_client is not None,
            "currency": settings.DEFAULT_CURRENCY if hasattr(settings, "DEFAULT_CURRENCY") else "USD",
            "app_name": settings.APP_NAME if hasattr(settings, "APP_NAME") else "MEDIGEST PRO",
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def test_twilio_connection(self) -> Dict[str, Any]:
        """
        Teste la connexion Twilio
        """
        if not self._twilio_client:
            return {"success": False, "error": "Twilio non initialisé"}
        
        try:
            account = self._twilio_client.api.accounts(self._twilio_client.account_sid).fetch()
            return {
                "success": True,
                "account": account.friendly_name,
                "status": account.status,
                "balance": float(account.balance) if account.balance else 0
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


# Instance singleton pour les fonctions d'export
_notification_service_instance: Optional[NotificationService] = None

def _get_notification_service() -> NotificationService:
    """Retourne l'instance singleton du service de notification"""
    global _notification_service_instance
    if _notification_service_instance is None:
        _notification_service_instance = NotificationService(db=None)
    return _notification_service_instance

# Fonctions de compatibilité (pour l'import existant)
def send_sms(to: str, body: str, from_: str = None) -> bool:
    """
    Fonction de compatibilité pour l'envoi de SMS (sans fallback)
    """
    service = _get_notification_service()
    return service.send_sms(to, body, from_)

def send_sms_with_fallback(to: str, body: str, fallback_whatsapp: bool = True) -> Dict[str, Any]:
    """
    Fonction de compatibilité pour l'envoi de SMS avec fallback WhatsApp
    """
    service = _get_notification_service()
    return service.send_sms_with_fallback(to, body, fallback_whatsapp)

def send_whatsapp(to: str, body: str, from_: str = None) -> Dict[str, Any]:
    """
    Fonction de compatibilité pour l'envoi WhatsApp avec résultat détaillé
    """
    service = _get_notification_service()
    return service.send_whatsapp(to, body, from_)

def send_email(to: str, subject: str, body: str, 
               from_: str = None, is_html: bool = True) -> bool:
    """
    Fonction de compatibilité pour l'envoi d'email
    """
    service = _get_notification_service()
    return service.send_email(to, subject, body, from_, is_html)

def test_twilio_connection() -> Dict[str, Any]:
    """
    Teste la connexion Twilio
    """
    service = _get_notification_service()
    return service.test_twilio_connection()
# app/tasks/subscription_tasks.py
"""
Tâches planifiées pour la gestion des abonnements.
Gère les vérifications d'expiration et les notifications.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
import importlib.util

from app.db.session import SessionLocal
from app.services.subscription_service import process_expired_subscriptions

logger = logging.getLogger(__name__)

# Vérifier si apscheduler est installé
SCHEDULER_AVAILABLE = importlib.util.find_spec("apscheduler") is not None

if SCHEDULER_AVAILABLE:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.executors.pool import ThreadPoolExecutor, ProcessPoolExecutor
        APSCHEDULER_OK = True
    except ImportError:
        APSCHEDULER_OK = False
        logger.warning("apscheduler partiellement disponible - vérification des imports")
else:
    APSCHEDULER_OK = False
    logger.warning("apscheduler non installé. Les tâches planifiées ne seront pas disponibles.")


# =============================================================================
# TÂCHES DE VÉRIFICATION DES EXPIRATIONS
# =============================================================================

def check_expired_subscriptions():
    """
    Vérifie les abonnements expirés et les marque comme tels.
    Exécution quotidienne recommandée.
    """
    logger.info("=== DÉBUT: Vérification des abonnements expirés ===")
    db = SessionLocal()
    try:
        expired_count = process_expired_subscriptions(db)
        logger.info(f"✓ {expired_count} abonnements expirés traités")
    except Exception as e:
        logger.error(f"✗ Erreur lors de la vérification des expirations: {e}", exc_info=True)
    finally:
        db.close()
        logger.info("=== FIN: Vérification des abonnements expirés ===")


def send_expiry_notifications():
    """
    Envoie des notifications aux utilisateurs dont l'abonnement expire bientôt.
    Exécution quotidienne recommandée (matin).
    """
    logger.info("=== DÉBUT: Envoi des notifications d'expiration ===")
    db = SessionLocal()
    
    try:
        # Imports conditionnels pour éviter les dépendances circulaires
        from app.models.user import User
        from app.models.user_subscription import UserSubscription
        from app.services.notification_service import send_email, send_sms
        
        now = datetime.utcnow()
        
        # Configuration des seuils de notification
        NOTIFICATION_DAYS = [3, 1]  # Jours avant expiration pour envoyer une notification
        
        for days_before in NOTIFICATION_DAYS:
            # Utilisateurs dont l'abonnement expire dans exactement 'days_before' jours
            target_date = now + timedelta(days=days_before)
            
            expiring_soon = db.query(User).join(UserSubscription).filter(
                UserSubscription.status == "active",
                UserSubscription.end_date >= target_date.replace(hour=0, minute=0, second=0),
                UserSubscription.end_date <= target_date.replace(hour=23, minute=59, second=59)
            ).all()
            
            for user in expiring_soon:
                days_left = (user.subscription.end_date - now).days
                logger.info(f"Notification J-{days_left} pour {user.email}")
                
                # Envoyer email
                try:
                    send_email(
                        to_email=user.email,
                        subject=f"Votre abonnement expire dans {days_left} jour{'s' if days_left > 1 else ''}",
                        template="subscription_expiring.html",
                        context={
                            "name": user.nom_complet,
                            "days_left": days_left,
                            "end_date": user.subscription.end_date.strftime("%d/%m/%Y"),
                            "plan_name": user.subscription.plan_name,
                            "renewal_link": "https://app.medigest.com/subscription",
                            "support_email": "support@medigest.com"
                        }
                    )
                    logger.info(f"  ✓ Email envoyé à {user.email}")
                except Exception as e:
                    logger.error(f"  ✗ Erreur envoi email à {user.email}: {e}")
                
                # Envoyer SMS si numéro disponible
                if user.telephone:
                    try:
                        message = (
                            f"MEDIGEST: Votre abonnement {user.subscription.plan_name} "
                            f"expire dans {days_left} jour{'s' if days_left > 1 else ''}. "
                            f"Renouvelez sur https://app.medigest.com/subscription"
                        )
                        send_sms(to=user.telephone, message=message)
                        logger.info(f"  ✓ SMS envoyé à {user.telephone}")
                    except Exception as e:
                        logger.error(f"  ✗ Erreur envoi SMS à {user.telephone}: {e}")
        
        logger.info(f"✓ Notifications d'expiration envoyées avec succès")
        
    except ImportError as e:
        logger.error(f"Erreur d'importation des modèles: {e}")
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi des notifications: {e}", exc_info=True)
    finally:
        db.close()
        logger.info("=== FIN: Envoi des notifications d'expiration ===")


# =============================================================================
# PLANIFICATEUR DE TÂCHES
# =============================================================================

_scheduler_instance = None


def get_scheduler():
    """
    Retourne l'instance unique du planificateur.
    """
    global _scheduler_instance
    
    if not APSCHEDULER_OK:
        logger.warning("apscheduler non disponible - retour d'un scheduler factice")
        return None
    
    if _scheduler_instance is None:
        try:
            # Configuration du scheduler
            executors = {
                'default': ThreadPoolExecutor(20),
                'processpool': ProcessPoolExecutor(5)
            }
            
            job_defaults = {
                'coalesce': True,  # Évite les exécutions multiples
                'max_instances': 1,  # Une seule instance à la fois
                'misfire_grace_time': 3600  # Tolérance de 1h pour les exécutions manquées
            }
            
            _scheduler_instance = BackgroundScheduler(
                executors=executors,
                job_defaults=job_defaults,
                timezone='UTC'
            )
            logger.info("Instance du planificateur créée")
        except Exception as e:
            logger.error(f"Erreur lors de la création du planificateur: {e}")
            return None
    
    return _scheduler_instance


def start_scheduler():
    """
    Démarre le planificateur de tâches avec les jobs configurés.
    À appeler au démarrage de l'application.
    """
    if not APSCHEDULER_OK:
        logger.warning("apscheduler non installé. Les tâches planifiées ne seront pas démarrées.")
        logger.info("Pour installer: pip install apscheduler")
        return False
    
    scheduler = get_scheduler()
    if not scheduler:
        logger.error("Impossible de démarrer le planificateur")
        return False
    
    try:
        # Vérifier si les jobs existent déjà
        existing_jobs = [job.id for job in scheduler.get_jobs()]
        
        # Job 1: Vérification des expirations (tous les jours à minuit)
        if "check_expired_subscriptions" not in existing_jobs:
            scheduler.add_job(
                check_expired_subscriptions,
                trigger=CronTrigger(hour=0, minute=0),
                id="check_expired_subscriptions",
                name="Vérification des abonnements expirés",
                replace_existing=True,
                misfire_grace_time=3600
            )
            logger.info("✓ Job programmé: Vérification des expirations (00:00 UTC)")
        
        # Job 2: Envoi des notifications (tous les jours à 8h)
        if "send_expiry_notifications" not in existing_jobs:
            scheduler.add_job(
                send_expiry_notifications,
                trigger=CronTrigger(hour=8, minute=0),
                id="send_expiry_notifications",
                name="Envoi des notifications d'expiration",
                replace_existing=True,
                misfire_grace_time=3600
            )
            logger.info("✓ Job programmé: Notifications d'expiration (08:00 UTC)")
        
        # Job 3: Nettoyage des données obsolètes (optionnel, tous les dimanches à 3h)
        if "cleanup_old_data" not in existing_jobs:
            # À implémenter si nécessaire
            pass
        
        # Démarrer le scheduler s'il ne l'est pas déjà
        if not scheduler.running:
            scheduler.start()
            logger.info("✓ Planificateur de tâches démarré avec succès")
            
            # Afficher les jobs programmés
            jobs = scheduler.get_jobs()
            logger.info(f"Jobs actifs: {len(jobs)}")
            for job in jobs:
                logger.info(f"  - {job.id}: {job.name} (prochaine exécution: {job.next_run_time})")
        
        return True
        
    except Exception as e:
        logger.error(f"Erreur lors du démarrage du planificateur: {e}", exc_info=True)
        return False


def stop_scheduler():
    """
    Arrête le planificateur de tâches.
    À appeler à l'arrêt de l'application.
    """
    global _scheduler_instance
    
    if _scheduler_instance and _scheduler_instance.running:
        try:
            _scheduler_instance.shutdown(wait=True)
            logger.info("✓ Planificateur de tâches arrêté")
        except Exception as e:
            logger.error(f"Erreur lors de l'arrêt du planificateur: {e}")
        finally:
            _scheduler_instance = None
    else:
        logger.info("Planificateur non démarré ou déjà arrêté")


def run_manually(task_name: Optional[str] = None):
    """
    Exécute manuellement une tâche spécifique (pour debugging).
    
    Args:
        task_name: Nom de la tâche ('expired' ou 'notifications')
                   Si None, exécute les deux.
    """
    logger.info(f"=== EXÉCUTION MANUELLE DES TÂCHES ===")
    
    if task_name in [None, 'expired']:
        check_expired_subscriptions()
    
    if task_name in [None, 'notifications']:
        send_expiry_notifications()
    
    logger.info(f"=== FIN EXÉCUTION MANUELLE ===")


# =============================================================================
# INITIALISATION
# =============================================================================

if __name__ == "__main__":
    # Permet d'exécuter le script directement pour tester
    import argparse
    
    parser = argparse.ArgumentParser(description="Gestion des tâches d'abonnement")
    parser.add_argument("--task", choices=["expired", "notifications", "all"], 
                        default="all", help="Tâche à exécuter")
    parser.add_argument("--scheduler", action="store_true", 
                        help="Démarrer le planificateur")
    
    args = parser.parse_args()
    
    if args.scheduler:
        start_scheduler()
        try:
            # Maintenir le script en vie
            import time
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            stop_scheduler()
    else:
        task = None if args.task == "all" else args.task
        run_manually(task)
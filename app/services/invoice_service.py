# app/services/invoice_service.py
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, and_, or_
from uuid import uuid4
from typing import Optional, List, Tuple, Dict, Any
from decimal import Decimal
import logging

from app.models.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.pharmacy import Pharmacy
from app.models.pharmacy_subscription import PharmacySubscription
from app.models.user import User
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)


def generate_invoice_number(pharmacy_code: str, year: int, month: int, sequence: int) -> str:
    """Génère un numéro de facture unique."""
    return f"INV-{pharmacy_code}-{year}{month:02d}-{sequence:04d}"


def create_subscription_invoice(
    db: Session,
    pharmacy: Pharmacy,
    subscription: PharmacySubscription,
    billing_cycle: str = "monthly"
) -> Invoice:
    """Crée une facture pour un abonnement."""
    from app.services.pharmacy_subscription_service import get_plan_limits
    
    now = datetime.utcnow()
    plan_limits = get_plan_limits(subscription.plan)
    
    # Calculer la période
    if billing_cycle == "yearly":
        period_start = now
        period_end = now + timedelta(days=365)
        price = plan_limits.get("yearly_price", 0)
    else:
        period_start = now
        period_end = now + timedelta(days=30)
        price = plan_limits.get("monthly_price", 0)
    
    # Compter les factures existantes pour le numéro
    year = now.year
    month = now.month
    count = db.query(Invoice).filter(
        Invoice.invoice_number.like(f"INV-{pharmacy.pharmacy_code}-{year}{month:02d}%")
    ).count()
    
    invoice_number = generate_invoice_number(
        pharmacy.pharmacy_code or pharmacy.id.hex[:6].upper(),
        year, month, count + 1
    )
    
    invoice = Invoice(
        id=uuid4(),
        pharmacy_id=pharmacy.id,
        tenant_id=pharmacy.tenant_id,
        invoice_number=invoice_number,
        invoice_type=InvoiceType.SUBSCRIPTION,
        period_start=period_start,
        period_end=period_end,
        subtotal=price,
        tax_rate=16.0,  # TVA par défaut
        tax_amount=price * 0.16,
        discount_amount=0,
        total_amount=price * 1.16,
        currency="EUR",
        status=InvoiceStatus.SENT,
        issue_date=now,
        due_date=now + timedelta(days=15),
        description=f"Abonnement {plan_limits['name']} - {billing_cycle}",
        subscription_plan=subscription.plan,
        billing_cycle=billing_cycle,
        invoice_metadata={
            "plan_name": plan_limits["name"],
            "max_products": subscription.max_products,
            "max_users": subscription.max_users,
            "billing_cycle": billing_cycle
        }
    )
    
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    
    return invoice


def get_pharmacy_invoices(
    db: Session,
    pharmacy_id: str,
    limit: int = 50,
    offset: int = 0,
    start_date: datetime = None,
    end_date: datetime = None
) -> tuple[list[Invoice], int]:
    """Récupère les factures d'une pharmacie."""
    query = db.query(Invoice).filter(Invoice.pharmacy_id == pharmacy_id)
    
    if start_date:
        query = query.filter(Invoice.issue_date >= start_date)
    if end_date:
        query = query.filter(Invoice.issue_date <= end_date)
    
    total = query.count()
    invoices = query.order_by(Invoice.issue_date.desc()).offset(offset).limit(limit).all()
    
    return invoices, total


# ========================
# NOUVELLES FONCTIONS AJOUTÉES
# ========================

def create_invoice(
    db: Session,
    pharmacy_id: str,
    tenant_id: str,
    amount: float,
    description: str,
    invoice_type: str = "one_time",
    due_days: int = 30,
    tax_rate: float = 16.0,
    discount_amount: float = 0.0,
    **kwargs
) -> Invoice:
    """
    Crée une nouvelle facture.
    
    Args:
        db: Session de base de données
        pharmacy_id: ID de la pharmacie
        tenant_id: ID du tenant
        amount: Montant HT
        description: Description de la facture
        invoice_type: Type de facture (subscription, one_time, renewal)
        due_days: Nombre de jours avant échéance
        tax_rate: Taux de TVA (%)
        discount_amount: Montant de la remise
        **kwargs: Autres champs optionnels
    
    Returns:
        Invoice: La facture créée
    """
    try:
        now = datetime.utcnow()
        
        # Récupérer la pharmacie
        pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
        if not pharmacy:
            raise ValueError(f"Pharmacie {pharmacy_id} non trouvée")
        
        # Générer le numéro de facture
        year = now.year
        month = now.month
        pharmacy_code = pharmacy.pharmacy_code or pharmacy.id.hex[:6].upper()
        
        count = db.query(Invoice).filter(
            Invoice.tenant_id == tenant_id,
            Invoice.invoice_number.like(f"INV-{pharmacy_code}-{year}{month:02d}%")
        ).count()
        
        invoice_number = generate_invoice_number(pharmacy_code, year, month, count + 1)
        
        # Calculer les montants
        subtotal = float(amount)
        tax_amount = subtotal * (tax_rate / 100)
        total_amount = subtotal + tax_amount - discount_amount
        
        # Déterminer la date d'échéance
        due_date = now + timedelta(days=due_days)
        
        # Créer la facture
        invoice = Invoice(
            id=uuid4(),
            pharmacy_id=pharmacy_id,
            tenant_id=tenant_id,
            invoice_number=invoice_number,
            invoice_type=InvoiceType(invoice_type) if invoice_type in [t.value for t in InvoiceType] else InvoiceType.ONE_TIME,
            period_start=kwargs.get('period_start', now),
            period_end=kwargs.get('period_end', now + timedelta(days=30)),
            subtotal=subtotal,
            tax_rate=tax_rate,
            tax_amount=tax_amount,
            discount_amount=discount_amount,
            total_amount=total_amount,
            currency=kwargs.get('currency', 'EUR'),
            status=InvoiceStatus.DRAFT,
            issue_date=now,
            due_date=due_date,
            paid_at=None,
            description=description,
            subscription_plan=kwargs.get('subscription_plan'),
            billing_cycle=kwargs.get('billing_cycle'),
            payment_method=kwargs.get('payment_method'),
            payment_reference=kwargs.get('payment_reference'),
            invoice_metadata=kwargs.get('invoice_metadata', {})
        )
        
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        
        logger.info(f"Facture créée: {invoice.invoice_number} pour pharmacie {pharmacy_id}")
        return invoice
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création facture: {str(e)}")
        raise


def update_invoice(
    db: Session,
    invoice_id: str,
    **kwargs
) -> Invoice:
    """
    Met à jour une facture existante.
    
    Args:
        db: Session de base de données
        invoice_id: ID de la facture
        **kwargs: Champs à mettre à jour
    
    Returns:
        Invoice: La facture mise à jour
    """
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        
        if not invoice:
            raise ValueError(f"Facture {invoice_id} non trouvée")
        
        # Champs modifiables
        updatable_fields = [
            'description', 'due_date', 'status', 'payment_method',
            'payment_reference', 'discount_amount', 'invoice_metadata',
            'period_start', 'period_end', 'payment_method'
        ]
        
        for field in updatable_fields:
            if field in kwargs and kwargs[field] is not None:
                setattr(invoice, field, kwargs[field])
        
        # Mise à jour spéciale du statut
        if 'status' in kwargs:
            new_status = kwargs['status']
            if isinstance(new_status, str):
                try:
                    new_status = InvoiceStatus(new_status)
                except ValueError:
                    pass
            setattr(invoice, 'status', new_status)
            
            # Si la facture est marquée comme payée
            if new_status == InvoiceStatus.PAID and not invoice.paid_at:
                invoice.paid_at = datetime.utcnow()
        
        # Recalculer le total si nécessaire
        if 'discount_amount' in kwargs:
            invoice.total_amount = invoice.subtotal + invoice.tax_amount - invoice.discount_amount
        
        db.commit()
        db.refresh(invoice)
        
        logger.info(f"Facture mise à jour: {invoice.invoice_number}")
        return invoice
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur mise à jour facture: {str(e)}")
        raise


def get_invoice(
    db: Session,
    invoice_id: str,
    tenant_id: Optional[str] = None
) -> Optional[Invoice]:
    """
    Récupère une facture par son ID.
    
    Args:
        db: Session de base de données
        invoice_id: ID de la facture
        tenant_id: ID du tenant (optionnel, pour filtrage)
    
    Returns:
        Invoice ou None
    """
    query = db.query(Invoice).filter(Invoice.id == invoice_id)
    
    if tenant_id:
        query = query.filter(Invoice.tenant_id == tenant_id)
    
    return query.first()


def get_invoices(
    db: Session,
    tenant_id: Optional[str] = None,
    pharmacy_id: Optional[str] = None,
    status: Optional[str] = None,
    invoice_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    sort_by: str = "issue_date",
    sort_order: str = "desc"
) -> Tuple[List[Invoice], int]:
    """
    Récupère une liste de factures avec filtres.
    
    Args:
        db: Session de base de données
        tenant_id: ID du tenant
        pharmacy_id: ID de la pharmacie
        status: Statut de la facture
        invoice_type: Type de facture
        start_date: Date de début
        end_date: Date de fin
        search: Terme de recherche
        limit: Nombre maximum de résultats
        offset: Décalage pour la pagination
        sort_by: Champ de tri
        sort_order: Ordre de tri (asc/desc)
    
    Returns:
        Tuple[List[Invoice], int]: (Liste des factures, Nombre total)
    """
    query = db.query(Invoice)
    
    # Filtres
    if tenant_id:
        query = query.filter(Invoice.tenant_id == tenant_id)
    
    if pharmacy_id:
        query = query.filter(Invoice.pharmacy_id == pharmacy_id)
    
    if status:
        try:
            query = query.filter(Invoice.status == InvoiceStatus(status))
        except ValueError:
            query = query.filter(Invoice.status == status)
    
    if invoice_type:
        try:
            query = query.filter(Invoice.invoice_type == InvoiceType(invoice_type))
        except ValueError:
            query = query.filter(Invoice.invoice_type == invoice_type)
    
    if start_date:
        query = query.filter(Invoice.issue_date >= start_date)
    
    if end_date:
        query = query.filter(Invoice.issue_date <= end_date)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Invoice.invoice_number.ilike(search_term),
                Invoice.description.ilike(search_term)
            )
        )
    
    # Compter le total
    total = query.count()
    
    # Trier
    sort_column = getattr(Invoice, sort_by, Invoice.issue_date)
    if sort_order.lower() == "desc":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(sort_column)
    
    # Pagination
    invoices = query.offset(offset).limit(limit).all()
    
    return invoices, total


async def generate_invoice_pdf(
    db: Session,
    invoice: Invoice,
    include_details: bool = True
) -> bytes:
    """
    Génère un PDF pour une facture.
    
    Args:
        db: Session de base de données
        invoice: La facture à convertir en PDF
        include_details: Inclure les détails de la facture
    
    Returns:
        bytes: Contenu du PDF
    """
    try:
        # Cette fonction peut utiliser une bibliothèque comme reportlab, weasyprint, ou fpdf
        # Pour l'exemple, je fournis une implémentation basique avec fpdf
        
        from fpdf import FPDF
        from io import BytesIO
        
        # Récupérer les informations de la pharmacie
        pharmacy = db.query(Pharmacy).filter(Pharmacy.id == invoice.pharmacy_id).first()
        
        class PDF(FPDF):
            def header(self):
                # Logo (à personnaliser)
                # self.image('logo.png', 10, 8, 33)
                self.set_font('Arial', 'B', 15)
                self.cell(80)
                self.cell(30, 10, 'FACTURE', 0, 0, 'C')
                self.ln(20)
            
            def footer(self):
                self.set_y(-15)
                self.set_font('Arial', 'I', 8)
                self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')
        
        pdf = PDF()
        pdf.add_page()
        pdf.set_font('Arial', '', 12)
        
        # En-tête
        pdf.set_font('Arial', 'B', 16)
        pdf.cell(0, 10, f"Facture N° {invoice.invoice_number}", 0, 1, 'C')
        pdf.ln(10)
        
        # Informations
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 8, "Informations", 0, 1)
        pdf.set_font('Arial', '', 10)
        pdf.cell(60, 6, f"Date d'émission:", 0, 0)
        pdf.cell(0, 6, invoice.issue_date.strftime('%d/%m/%Y') if invoice.issue_date else '', 0, 1)
        pdf.cell(60, 6, f"Date d'échéance:", 0, 0)
        pdf.cell(0, 6, invoice.due_date.strftime('%d/%m/%Y') if invoice.due_date else '', 0, 1)
        pdf.cell(60, 6, f"Statut:", 0, 0)
        pdf.cell(0, 6, invoice.status.value if invoice.status else '', 0, 1)
        
        if pharmacy:
            pdf.ln(5)
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 8, "Pharmacie", 0, 1)
            pdf.set_font('Arial', '', 10)
            pdf.cell(60, 6, f"Nom:", 0, 0)
            pdf.cell(0, 6, pharmacy.name or '', 0, 1)
            if pharmacy.address:
                pdf.cell(60, 6, f"Adresse:", 0, 0)
                pdf.cell(0, 6, pharmacy.address or '', 0, 1)
        
        pdf.ln(10)
        
        # Description
        if invoice.description:
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 8, "Description", 0, 1)
            pdf.set_font('Arial', '', 10)
            pdf.multi_cell(0, 6, invoice.description)
        
        pdf.ln(10)
        
        # Détails financiers
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 8, "Détails financiers", 0, 1)
        pdf.set_font('Arial', '', 10)
        
        pdf.cell(100, 8, "Sous-total HT:", 0, 0)
        pdf.cell(0, 8, f"{invoice.subtotal:.2f} {invoice.currency}", 0, 1)
        
        pdf.cell(100, 8, f"TVA ({invoice.tax_rate:.1f}%):", 0, 0)
        pdf.cell(0, 8, f"{invoice.tax_amount:.2f} {invoice.currency}", 0, 1)
        
        if invoice.discount_amount > 0:
            pdf.cell(100, 8, "Remise:", 0, 0)
            pdf.cell(0, 8, f"-{invoice.discount_amount:.2f} {invoice.currency}", 0, 1)
        
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(100, 10, "TOTAL TTC:", 0, 0)
        pdf.cell(0, 10, f"{invoice.total_amount:.2f} {invoice.currency}", 0, 1)
        
        # Générer le PDF en bytes
        pdf_output = pdf.output(dest='S').encode('latin1')
        return pdf_output
        
    except ImportError:
        # Fallback si fpdf n'est pas installé
        logger.warning("fpdf non installé, génération d'un PDF simple")
        
        # Simplification: retourner un fichier texte au lieu d'un PDF
        pdf_content = f"""
        FACTURE {invoice.invoice_number}
        ================================
        
        Date: {invoice.issue_date}
        Échéance: {invoice.due_date}
        Statut: {invoice.status}
        
        Montant HT: {invoice.subtotal} {invoice.currency}
        TVA ({invoice.tax_rate}%): {invoice.tax_amount} {invoice.currency}
        Remise: {invoice.discount_amount} {invoice.currency}
        TOTAL TTC: {invoice.total_amount} {invoice.currency}
        
        Description: {invoice.description}
        """
        
        return pdf_content.encode('utf-8')
    
    except Exception as e:
        logger.error(f"Erreur génération PDF: {str(e)}")
        raise


def mark_invoice_as_paid(
    db: Session,
    invoice_id: str,
    payment_method: str,
    payment_reference: Optional[str] = None
) -> Invoice:
    """
    Marque une facture comme payée.
    
    Args:
        db: Session de base de données
        invoice_id: ID de la facture
        payment_method: Méthode de paiement (card, bank_transfer, cash)
        payment_reference: Référence du paiement
    
    Returns:
        Invoice: La facture mise à jour
    """
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    
    if not invoice:
        raise ValueError(f"Facture {invoice_id} non trouvée")
    
    invoice.status = InvoiceStatus.PAID
    invoice.paid_at = datetime.utcnow()
    invoice.payment_method = payment_method
    invoice.payment_reference = payment_reference
    
    db.commit()
    db.refresh(invoice)
    
    logger.info(f"Facture marquée comme payée: {invoice.invoice_number}")
    return invoice


def get_overdue_invoices(
    db: Session,
    tenant_id: Optional[str] = None,
    days_overdue: Optional[int] = None
) -> List[Invoice]:
    """
    Récupère les factures en retard.
    
    Args:
        db: Session de base de données
        tenant_id: ID du tenant
        days_overdue: Nombre de jours de retard minimum
    
    Returns:
        List[Invoice]: Liste des factures en retard
    """
    query = db.query(Invoice).filter(
        Invoice.status == InvoiceStatus.SENT,
        Invoice.due_date < datetime.utcnow().date()
    )
    
    if tenant_id:
        query = query.filter(Invoice.tenant_id == tenant_id)
    
    if days_overdue:
        cutoff_date = datetime.utcnow().date() - timedelta(days=days_overdue)
        query = query.filter(Invoice.due_date <= cutoff_date)
    
    return query.order_by(Invoice.due_date.asc()).all()


def get_invoice_summary(
    db: Session,
    tenant_id: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Récupère un résumé des factures pour un tenant.
    
    Args:
        db: Session de base de données
        tenant_id: ID du tenant
        start_date: Date de début
        end_date: Date de fin
    
    Returns:
        Dict: Résumé des factures
    """
    query = db.query(Invoice).filter(Invoice.tenant_id == tenant_id)
    
    if start_date:
        query = query.filter(Invoice.issue_date >= start_date)
    if end_date:
        query = query.filter(Invoice.issue_date <= end_date)
    
    total_amount = query.with_entities(func.coalesce(func.sum(Invoice.total_amount), 0)).scalar() or 0
    paid_amount = query.filter(Invoice.status == InvoiceStatus.PAID).with_entities(
        func.coalesce(func.sum(Invoice.total_amount), 0)
    ).scalar() or 0
    
    return {
        "total_invoices": query.count(),
        "total_amount": float(total_amount),
        "paid_amount": float(paid_amount),
        "pending_amount": float(total_amount - paid_amount),
        "overdue_count": query.filter(Invoice.status == InvoiceStatus.OVERDUE).count()
    }
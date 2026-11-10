"""
app/api/v1/customers.py
Routes API pour la gestion des clients
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, desc
from typing import List, Optional, Dict, Any
from datetime import datetime, date
from uuid import UUID
import logging

from app.db.session import get_db
from app.models.customer import Customer
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.customer import (
    CustomerCreate, CustomerUpdate, CustomerInDB,
    CustomerStats, CustomerSearchResult, CustomerDebtInfo,
    CustomerSummary, CustomerLoyaltyInfo
)
from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission

router = APIRouter(prefix="/customers", tags=["Customers"])
logger = logging.getLogger(__name__)


@router.get("/", response_model=List[CustomerInDB])
@require_permission("customer_view")
def list_customers(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = None,
    type_client: Optional[str] = None,
    category: Optional[str] = None,
    ville: Optional[str] = None,
    eligible_credit: Optional[bool] = None,
    blacklisted: Optional[bool] = None,
    is_active: Optional[bool] = True,
    order_by: str = Query("nom", pattern="^(nom|total_achats|dernier_achat|dette_actuelle|loyalty_points)$"),
    order_dir: str = Query("asc", pattern="^(asc|desc)$")
):
    """Liste les clients avec filtres"""
    try:
        query = db.query(Customer).filter(Customer.tenant_id == current_tenant.id)
        
        if is_active is not None:
            query = query.filter(Customer.is_active == is_active)
        
        # Filtres de recherche
        if search:
            search_filter = or_(
                Customer.nom.ilike(f"%{search}%"),
                Customer.prenom.ilike(f"%{search}%"),
                Customer.telephone.ilike(f"%{search}%"),
                Customer.email.ilike(f"%{search}%"),
                Customer.entreprise.ilike(f"%{search}%")
            )
            query = query.filter(search_filter)
        
        if type_client:
            query = query.filter(Customer.type_client == type_client)
        
        if category:
            query = query.filter(Customer.category == category)
        
        if ville:
            query = query.filter(Customer.ville.ilike(f"%{ville}%"))
        
        if eligible_credit is not None:
            query = query.filter(Customer.eligible_credit == eligible_credit)
        
        if blacklisted is not None:
            query = query.filter(Customer.blacklisted == blacklisted)
        
        # Tri
        if order_by == "nom":
            order_field = Customer.nom
        elif order_by == "total_achats":
            order_field = Customer.total_achats
        elif order_by == "dernier_achat":
            order_field = Customer.dernier_achat
        elif order_by == "dette_actuelle":
            order_field = Customer.dette_actuelle
        elif order_by == "loyalty_points":
            order_field = Customer.loyalty_points
        
        if order_dir == "desc":
            order_field = desc(order_field)
        
        query = query.order_by(order_field)
        
        customers = query.offset(skip).limit(limit).all()
        return customers
        
    except Exception as e:
        logger.error(f"Erreur lors de la liste des clients: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la récupération des clients"
        )


@router.get("/search", response_model=List[CustomerSearchResult])
@require_permission("customer_view")
def search_customers(
    q: str = Query(..., min_length=1, max_length=100),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Recherche rapide de clients"""
    try:
        query = db.query(Customer).filter(
            Customer.tenant_id == current_tenant.id,
            Customer.is_active == True
        )
        
        search_filter = or_(
            Customer.nom.ilike(f"%{q}%"),
            Customer.prenom.ilike(f"%{q}%"),
            Customer.telephone.ilike(f"%{q}%"),
            Customer.email.ilike(f"%{q}%"),
            Customer.entreprise.ilike(f"%{q}%"),
            Customer.num_contribuable.ilike(f"%{q}%")
        )
        query = query.filter(search_filter)
        
        customers = query.limit(20).all()
        
        return [
            CustomerSearchResult(
                id=c.id,
                full_name=c.full_name,
                nom=c.nom,
                prenom=c.prenom,
                telephone=c.telephone,
                email=c.email,
                entreprise=c.entreprise,
                type_client=c.type_client,
                category=c.category,
                dette_actuelle=float(c.dette_actuelle),
                credit_available=float(c.credit_available),
                loyalty_points=c.loyalty_points
            )
            for c in customers
        ]
        
    except Exception as e:
        logger.error(f"Erreur lors de la recherche: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la recherche"
        )


@router.get("/{customer_id}", response_model=CustomerInDB)
@require_permission("customer_view")
def get_customer(
    customer_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Récupère un client par son ID"""
    customer = db.query(Customer).filter(
        Customer.id == customer_id,
        Customer.tenant_id == current_tenant.id
    ).first()
    
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client non trouvé"
        )
    
    return customer


@router.post("/", response_model=CustomerInDB, status_code=status.HTTP_201_CREATED)
@require_permission("customer_manage")
def create_customer(
    customer_data: CustomerCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Crée un nouveau client"""
    try:
        # Vérifier si le téléphone existe déjà
        existing = db.query(Customer).filter(
            Customer.tenant_id == current_tenant.id,
            Customer.telephone == customer_data.telephone
        ).first()
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Un client avec ce numéro de téléphone existe déjà"
            )
        
        # Vérifier l'email
        if customer_data.email:
            existing_email = db.query(Customer).filter(
                Customer.tenant_id == current_tenant.id,
                Customer.email == customer_data.email
            ).first()
            
            if existing_email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Un client avec cet email existe déjà"
                )
        
        # Créer le client
        customer = Customer(
            tenant_id=current_tenant.id,
            nom=customer_data.nom,
            prenom=customer_data.prenom,
            telephone=customer_data.telephone,
            email=customer_data.email,
            adresse=customer_data.adresse,
            ville=customer_data.ville,
            code_postal=customer_data.code_postal,
            pays=customer_data.pays,
            type_client=customer_data.type_client.value if customer_data.type_client else "particulier",
            category=customer_data.category.value if customer_data.category else "standard",
            entreprise=customer_data.entreprise,
            num_contribuable=customer_data.num_contribuable,
            rccm=customer_data.rccm,
            id_nat=customer_data.id_nat,
            birth_date=customer_data.birth_date,
            blood_type=customer_data.blood_type,
            allergies=customer_data.allergies,
            medical_notes=customer_data.medical_notes,
            insurance_provider=customer_data.insurance_provider,
            insurance_number=customer_data.insurance_number,
            credit_limit=customer_data.credit_limit or 0,
            eligible_credit=customer_data.eligible_credit or False,
            notes=customer_data.notes,
            preferences=customer_data.preferences or {},
            is_active=True,
            created_by=current_user.id
        )
        
        db.add(customer)
        db.commit()
        db.refresh(customer)
        
        logger.info(f"Client créé: {customer.full_name} par {current_user.nom_complet}")
        return customer
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la création du client"
        )


@router.put("/{customer_id}", response_model=CustomerInDB)
@require_permission("customer_manage")
def update_customer(
    customer_id: UUID,
    customer_data: CustomerUpdate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Met à jour un client"""
    customer = db.query(Customer).filter(
        Customer.id == customer_id,
        Customer.tenant_id == current_tenant.id
    ).first()
    
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client non trouvé"
        )
    
    try:
        # Vérifier le téléphone
        if customer_data.telephone and customer_data.telephone != customer.telephone:
            existing = db.query(Customer).filter(
                Customer.tenant_id == current_tenant.id,
                Customer.telephone == customer_data.telephone,
                Customer.id != customer_id
            ).first()
            
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Un autre client avec ce numéro existe déjà"
                )
        
        # Mettre à jour
        update_data = customer_data.dict(exclude_unset=True)
        
        # Convertir les enums
        if "type_client" in update_data and update_data["type_client"]:
            update_data["type_client"] = update_data["type_client"].value
        if "category" in update_data and update_data["category"]:
            update_data["category"] = update_data["category"].value
        
        for field, value in update_data.items():
            if value is not None:
                setattr(customer, field, value)
        
        db.commit()
        db.refresh(customer)
        
        logger.info(f"Client mis à jour: {customer.full_name}")
        return customer
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la mise à jour: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la mise à jour"
        )


@router.delete("/{customer_id}")
@require_permission("customer_manage")
def delete_customer(
    customer_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Supprime (désactive) un client"""
    customer = db.query(Customer).filter(
        Customer.id == customer_id,
        Customer.tenant_id == current_tenant.id
    ).first()
    
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client non trouvé"
        )
    
    if customer.dette_actuelle > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Impossible de supprimer un client avec des dettes"
        )
    
    customer.is_active = False
    customer.blacklisted = True
    customer.blacklist_reason = "Désactivé par l'administrateur"
    
    db.commit()
    
    logger.info(f"Client désactivé: {customer.full_name}")
    return {"message": "Client désactivé avec succès"}


@router.get("/{customer_id}/stats", response_model=CustomerStats)
@require_permission("customer_view")
def get_customer_stats(
    customer_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Statistiques détaillées d'un client"""
    customer = db.query(Customer).filter(
        Customer.id == customer_id,
        Customer.tenant_id == current_tenant.id
    ).first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Client non trouvé")
    
    days_since_last_payment = None
    if customer.date_dernier_paiement:
        days_since_last_payment = (datetime.utcnow() - customer.date_dernier_paiement).days
    
    return CustomerStats(
        customer_id=customer.id,
        full_name=customer.full_name,
        total_achats=float(customer.total_achats),
        nombre_achats=customer.nombre_achats,
        moyenne_achat=float(customer.moyenne_achat),
        credit_limit=float(customer.credit_limit),
        dette_actuelle=float(customer.dette_actuelle),
        credit_available=float(customer.credit_available),
        credit_score=customer.credit_score,
        credit_utilization=customer.credit_utilization,
        credit_status=customer.credit_status,
        loyalty_points=customer.loyalty_points,
        category=customer.category,
        days_since_last_purchase=customer.days_since_last_purchase,
        last_payment_date=customer.date_dernier_paiement,
        days_since_last_payment=days_since_last_payment,
        eligible_credit=customer.eligible_credit,
        blacklisted=customer.blacklisted
    )


@router.get("/{customer_id}/loyalty", response_model=CustomerLoyaltyInfo)
@require_permission("customer_view")
def get_customer_loyalty(
    customer_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Informations de fidélité"""
    customer = db.query(Customer).filter(
        Customer.id == customer_id,
        Customer.tenant_id == current_tenant.id
    ).first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Client non trouvé")
    
    points_to_next = 0
    next_tier = None
    
    if customer.category == "standard":
        points_to_next = 500 - customer.loyalty_points
        next_tier = "premium"
    elif customer.category == "premium":
        points_to_next = 1000 - customer.loyalty_points
        next_tier = "vip"
    
    return CustomerLoyaltyInfo(
        customer_id=customer.id,
        full_name=customer.full_name,
        loyalty_points=customer.loyalty_points,
        category=customer.category,
        points_to_next_tier=max(0, points_to_next),
        next_tier=next_tier,
        total_orders=customer.nombre_achats,
        total_spent=float(customer.total_achats)
    )


@router.get("/stats/summary", response_model=CustomerSummary)
@require_permission("customer_view")
def get_customers_summary(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """Résumé des clients"""
    try:
        base_filter = Customer.tenant_id == current_tenant.id
        
        total = db.query(func.count(Customer.id)).filter(base_filter, Customer.is_active == True).scalar()
        active = db.query(func.count(Customer.id)).filter(base_filter, Customer.is_active == True).scalar()
        with_credit = db.query(func.count(Customer.id)).filter(base_filter, Customer.eligible_credit == True, Customer.is_active == True).scalar()
        blacklisted = db.query(func.count(Customer.id)).filter(base_filter, Customer.blacklisted == True, Customer.is_active == True).scalar()
        
        total_debt = db.query(func.coalesce(func.sum(Customer.dette_actuelle), 0)).filter(base_filter, Customer.is_active == True).scalar()
        total_sales = db.query(func.coalesce(func.sum(Customer.total_achats), 0)).filter(base_filter, Customer.is_active == True).scalar()
        total_points = db.query(func.coalesce(func.sum(Customer.loyalty_points), 0)).filter(base_filter, Customer.is_active == True).scalar()
        
        # Par type
        by_type = db.query(
            Customer.type_client,
            func.count(Customer.id)
        ).filter(base_filter, Customer.is_active == True).group_by(Customer.type_client).all()
        
        # Par catégorie
        by_category = db.query(
            Customer.category,
            func.count(Customer.id)
        ).filter(base_filter, Customer.is_active == True).group_by(Customer.category).all()
        
        # Top clients
        top = db.query(Customer).filter(base_filter, Customer.is_active == True)\
            .order_by(Customer.total_achats.desc()).limit(5).all()
        
        return CustomerSummary(
            total_customers=total,
            active_customers=active,
            customers_with_credit=with_credit,
            blacklisted_customers=blacklisted,
            total_debt=float(total_debt),
            total_sales=float(total_sales),
            total_loyalty_points=int(total_points),
            customers_by_type=[{"type": t, "count": c} for t, c in by_type],
            customers_by_category=[{"category": c, "count": cnt} for c, cnt in by_category],
            top_customers=[
                {
                    "id": str(c.id),
                    "full_name": c.full_name,
                    "total_achats": float(c.total_achats),
                    "nombre_achats": c.nombre_achats,
                    "loyalty_points": c.loyalty_points
                }
                for c in top
            ]
        )
        
    except Exception as e:
        logger.error(f"Erreur résumé: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors du calcul du résumé")
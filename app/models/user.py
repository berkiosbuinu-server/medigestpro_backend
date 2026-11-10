# app/models/user.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, JSON, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base import Base

class User(Base):
    __tablename__ = "users"

    # =========================
    # Identité & Auth
    # =========================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    nom_complet = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), default="pharmacien", nullable=False)
    actif = Column(Boolean, default=True)
    telephone = Column(String(20), nullable=True)
    adresse = Column(String(200), nullable=True)
    permissions = Column(JSON, nullable=True)
    department_id = Column(UUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL"), nullable=True)

    # =========================
    # Sécurité & Contrôle accès
    # =========================
    sms_code = Column(String(10), nullable=True)
    sms_expires_at = Column(DateTime, nullable=True)
    sms_verify_attempts = Column(Integer, default=0)
    
    reset_code = Column(String(10), nullable=True)
    reset_expires = Column(DateTime, nullable=True)

    login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
    activated_at = Column(DateTime, nullable=True)

    # =========================
    # Dates
    # =========================
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_login = Column(DateTime, nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    # =========================
    # Session active (pharmacie et branche)
    # =========================
    active_pharmacy_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("pharmacies.id", ondelete="SET NULL"),
        nullable=True
    )
    active_branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id", ondelete="SET NULL"), nullable=True)
    
    # Branche directe (où l'utilisateur travaille)
    branch_id = Column(UUID(as_uuid=True), ForeignKey("branches.id", ondelete="SET NULL"), nullable=True)
    
    # =========================
    # Relations
    # =========================
    tenant = relationship("Tenant", back_populates="users", foreign_keys=[tenant_id])
    
    # Pharmacie active
    active_pharmacy = relationship(
        "Pharmacy",
        foreign_keys=[active_pharmacy_id],
        lazy="joined"
    )
    
    # Branche active
    active_branch = relationship(
        "Branch",
        foreign_keys=[active_branch_id],
        lazy="joined",
        overlaps="branch"
    )
    
    # Branche directe
    branch = relationship(
        "Branch",
        foreign_keys=[branch_id],
        back_populates="users",
        lazy="joined"
    )
    
    # Relation Many-to-Many vers les pharmacies
    pharmacies = relationship(
        "Pharmacy",
        secondary="user_pharmacy",
        back_populates="users",
        overlaps="pharmacy_associations,user_associations,user,pharmacy"
    )

    # Relation vers la table d'association
    pharmacy_associations = relationship(
        "UserPharmacy",
        back_populates="user",
        cascade="all, delete-orphan",
        overlaps="pharmacies,users"
    )

    # Relations inverses
    tenants_created = relationship("Tenant", back_populates="creator", foreign_keys="[Tenant.created_by]", lazy="noload")
    
    costs_created = relationship("Cost", foreign_keys="Cost.created_by", back_populates="creator", lazy="noload")
    costs_approved = relationship("Cost", foreign_keys="Cost.approved_by", back_populates="approver", lazy="noload")
    budgets_owned = relationship("Budget", foreign_keys="Budget.owner_id", back_populates="owner", lazy="noload")
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    
    processed_debt_payments = relationship(
        "DebtPayment", 
        foreign_keys="DebtPayment.processed_by", 
        back_populates="processor", 
        lazy="noload",
        overlaps="payments_processed"
    )
    expenses = relationship("Expense", foreign_keys="Expense.user_id", back_populates="user")
    expenses_approved = relationship("Expense", foreign_keys="Expense.approved_by")
    user_expenses = relationship("UserExpense", back_populates="user", foreign_keys="UserExpense.user_id")
    branch_associations = relationship(
        "UserBranch", 
        back_populates="user", 
        cascade="all, delete-orphan",
        foreign_keys="UserBranch.user_id"
    )
    department = relationship(
        "Department", 
        foreign_keys=[department_id],  # Ajoutez ceci
        back_populates="employees"
    )
    managed_departments = relationship("Department", foreign_keys="[Department.manager_id]", back_populates="manager")
    assisted_departments = relationship("Department", foreign_keys="[Department.assistant_manager_id]", back_populates="assistant_manager")
    managed_projects = relationship(
        "Project", 
        foreign_keys="[Project.manager_id]", 
        back_populates="manager",
        cascade="all, delete-orphan"
    )
    
    # =========================
    # Méthodes utilitaires
    # =========================
    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"

    def get_primary_pharmacy(self):
        """Récupère la pharmacie marquée comme principale via l'association"""
        for assoc in self.pharmacy_associations:
            if assoc.is_primary:
                return assoc.pharmacy
        
        if self.pharmacies:
            return self.pharmacies[0]
            
        return None

    def set_active_pharmacy(self, pharmacy_id):
        """Définit la pharmacie active pour l'utilisateur"""
        if self.has_access_to_pharmacy(pharmacy_id):
            self.active_pharmacy_id = pharmacy_id
            return True
        return False
    
    def set_active_branch(self, branch_id):
        """Définit la branche active pour l'utilisateur"""
        self.active_branch_id = branch_id
        return True

    def has_access_to_pharmacy(self, pharmacy_id) -> bool:
        """Vérifie si l'utilisateur est lié à une pharmacie spécifique"""
        return any(assoc.pharmacy_id == pharmacy_id for assoc in self.pharmacy_associations)

    def can_manage_pharmacy(self, pharmacy_id) -> bool:
        """Vérifie si l'utilisateur a les droits de gestion ou est admin"""
        if self.role in ["admin", "super_admin"]:
            return True
        for assoc in self.pharmacy_associations:
            if assoc.pharmacy_id == pharmacy_id:
                return assoc.can_manage
        return False

    def to_dict(self, include_tenant: bool = False, include_pharmacies: bool = False) -> dict:
        """Retour JSON-safe sans données sensibles"""
        data = {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "nom_complet": self.nom_complet,
            "email": self.email,
            "role": self.role,
            "actif": self.actif,
            "telephone": self.telephone,
            "adresse": self.adresse,
            "permissions": self.permissions or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "active_pharmacy_id": str(self.active_pharmacy_id) if self.active_pharmacy_id else None,
            "active_branch_id": str(self.active_branch_id) if self.active_branch_id else None,
            "branch_id": str(self.branch_id) if self.branch_id else None,
        }

        if include_tenant and self.tenant:
            data["tenant"] = {
                "id": str(self.tenant.id),
                "nom_pharmacie": self.tenant.nom_pharmacie,
                "ville": self.tenant.ville,
            }
        
        if include_pharmacies:
            data["pharmacies"] = [
                {
                    "id": str(assoc.pharmacy.id),
                    "name": assoc.pharmacy.name,
                    "is_primary": assoc.is_primary,
                    "can_manage": assoc.can_manage
                }
                for assoc in self.pharmacy_associations
            ]

        return data

    def has_permission(self, permission: str) -> bool:
        if self.role in {"super_admin", "admin"}:
            return True
        return bool(self.permissions.get(permission, False)) if self.permissions else False

    @property
    def is_active(self) -> bool:
        return self.actif is True

    def update_last_login(self):
        self.last_login = datetime.utcnow()
    
    # ========== MÉTHODES D'ABONNEMENT PAR BRANCHE ==========
    
    def get_active_branch_subscription_status(self, db_session=None):
        """
        Retourne le statut de l'abonnement de la BRANCHE active de l'utilisateur.
        Basé sur BranchSubscription (nouvelle architecture).
        """
        if not self.active_branch_id:
            return {
                "has_subscription": False,
                "mode": "READ_ONLY",
                "message": "Aucune branche active sélectionnée"
            }
        
        if not db_session:
            from app.db.session import SessionLocal
            db_session = SessionLocal()
            try:
                return self._get_branch_subscription_status(db_session)
            finally:
                db_session.close()
        
        return self._get_branch_subscription_status(db_session)
    
    def _get_branch_subscription_status(self, db_session):
        """Méthode interne pour récupérer le statut d'abonnement d'une branche"""
        from app.models.branch_subscription import BranchSubscription, SubscriptionStatus
        from datetime import datetime
        
        try:
            subscription = db_session.query(BranchSubscription).filter(
                BranchSubscription.branch_id == self.active_branch_id
            ).first()
            
            if not subscription:
                return {
                    "has_subscription": False,
                    "mode": "READ_ONLY",
                    "message": "Aucun abonnement trouvé pour cette branche",
                    "plan": None,
                    "days_remaining": 0
                }
            
            # Vérifier si l'abonnement est actif
            is_active = subscription.is_active()
            days_remaining = subscription.days_remaining()
            
            if not is_active:
                return {
                    "has_subscription": True,
                    "is_active": False,
                    "mode": "READ_ONLY",
                    "message": "Abonnement de la branche inactif ou expiré",
                    "plan": subscription.plan.value if hasattr(subscription.plan, 'value') else str(subscription.plan),
                    "plan_name": subscription.plan_name,
                    "days_remaining": days_remaining,
                    "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
                    "max_products": subscription.max_products,
                    "max_users": subscription.max_users,
                    "max_storage_mb": subscription.max_storage_mb,
                    "is_unlimited_products": subscription.max_products == 0,
                    "is_unlimited_users": subscription.max_users == 0,
                    "is_unlimited_storage": subscription.max_storage_mb == 0
                }
            
            return {
                "has_subscription": True,
                "is_active": True,
                "mode": "FULL",
                "plan": subscription.plan.value if hasattr(subscription.plan, 'value') else str(subscription.plan),
                "plan_name": subscription.plan_name,
                "days_remaining": days_remaining,
                "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
                "max_products": subscription.max_products,
                "max_users": subscription.max_users,
                "max_storage_mb": subscription.max_storage_mb,
                "is_unlimited_products": subscription.max_products == 0,
                "is_unlimited_users": subscription.max_users == 0,
                "is_unlimited_storage": subscription.max_storage_mb == 0
            }
            
        except Exception as e:
            return {
                "has_subscription": False,
                "mode": "READ_ONLY",
                "message": f"Erreur de vérification: {str(e)}"
            }
    
    def get_subscription_mode(self, db_session=None) -> str:
        """
        Retourne le mode d'accès: "FULL" ou "READ_ONLY"
        Basé sur l'abonnement de la branche active.
        """
        status = self.get_active_branch_subscription_status(db_session)
        return status.get("mode", "READ_ONLY")
    
    def can_create_product(self, db_session=None) -> bool:
        """
        Vérifie si l'utilisateur peut créer un produit dans sa branche active.
        Basé sur l'abonnement de la branche et les limites.
        """
        if not self.active_branch_id:
            return False
        
        if not db_session:
            from app.db.session import SessionLocal
            db_session = SessionLocal()
            try:
                return self._check_can_create_product(db_session)
            finally:
                db_session.close()
        
        return self._check_can_create_product(db_session)
    
    def _check_can_create_product(self, db_session):
        """Vérification interne pour la création de produit"""
        from app.models.branch_subscription import BranchSubscription
        from app.models.product import Product
        
        subscription = db_session.query(BranchSubscription).filter(
            BranchSubscription.branch_id == self.active_branch_id
        ).first()
        
        if not subscription or not subscription.is_active():
            return False
        
        # Si max_products = 0, illimité
        if subscription.max_products == 0:
            return True
        
        # Compter les produits existants
        product_count = db_session.query(Product).filter(
            Product.branch_id == self.active_branch_id
        ).count()
        
        return product_count < subscription.max_products
    
    def can_add_user_to_branch(self, db_session=None) -> bool:
        """
        Vérifie si l'admin peut ajouter un utilisateur à la branche active.
        """
        if self.role != "admin":
            return False
        
        if not self.active_branch_id:
            return False
        
        if not db_session:
            from app.db.session import SessionLocal
            db_session = SessionLocal()
            try:
                return self._check_can_add_user(db_session)
            finally:
                db_session.close()
        
        return self._check_can_add_user(db_session)
    
    def _check_can_add_user(self, db_session):
        """Vérification interne pour l'ajout d'utilisateur"""
        from app.models.branch_subscription import BranchSubscription
        from app.models.user import User
        
        subscription = db_session.query(BranchSubscription).filter(
            BranchSubscription.branch_id == self.active_branch_id
        ).first()
        
        if not subscription or not subscription.is_active():
            return False
        
        # Si max_users = 0, illimité
        if subscription.max_users == 0:
            return True
        
        # Compter les utilisateurs de la branche
        user_count = db_session.query(User).filter(
            User.branch_id == self.active_branch_id
        ).count()
        
        return user_count < subscription.max_users
    
    def can_add_user(self, db_session=None) -> bool:
        """
        Alias pour can_add_user_to_branch (compatibilité)
        """
        return self.can_add_user_to_branch(db_session)
    
    def can_create_pharmacy(self) -> bool:
        """
        Vérifie si l'admin peut créer une nouvelle pharmacie.
        """
        if self.role != "admin":
            return False
        
        if not self.actif:
            return False
        
        return True
    
    def get_current_branch_subscription(self, db_session=None):
        """
        Récupère l'objet abonnement de la branche active.
        """
        if not self.active_branch_id:
            return None
        
        if not db_session:
            from app.db.session import SessionLocal
            db_session = SessionLocal()
            try:
                return self._get_branch_subscription(db_session)
            finally:
                db_session.close()
        
        return self._get_branch_subscription(db_session)
    
    def _get_branch_subscription(self, db_session):
        """Récupère l'objet BranchSubscription"""
        from app.models.branch_subscription import BranchSubscription
        
        return db_session.query(BranchSubscription).filter(
            BranchSubscription.branch_id == self.active_branch_id
        ).first()
    
    @property
    def assigned_branches(self):
        """Retourne la liste des branches assignées à cet utilisateur"""
        return [assoc.branch for assoc in self.branch_associations if assoc.is_active]
    
    def get_primary_branch(self):
        """Récupère la branche principale de l'utilisateur"""
        for assoc in self.branch_associations:
            if assoc.is_primary and assoc.is_active:
                return assoc.branch
        if self.branch_associations:
            return self.branch_associations[0].branch
        return None
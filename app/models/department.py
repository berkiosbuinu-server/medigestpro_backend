# app/models/department.py
import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from decimal import Decimal

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Text, Date, Index, DECIMAL, Integer, CheckConstraint
)
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func

from app.db.base import Base


class Department(Base):
    """Modèle représentant un département/unité d'une entreprise"""
    __tablename__ = "departments"

    # =====================================
    # IDENTIFIANT
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("departments.id"), nullable=True)

    # =====================================
    # INFORMATION DE BASE
    # =====================================
    code = Column(String(50), unique=True, nullable=False, index=True, comment="Code unique du département")
    name = Column(String(200), nullable=False)
    short_name = Column(String(50), nullable=True)
    
    # =====================================
    # HIÉRARCHIE ET ORGANISATION
    # =====================================
    department_type = Column(
        String(30), 
        nullable=False, 
        default="department",
        comment="department, division, unit, team, branch"
    )
    level = Column(Integer, default=1, comment="Niveau dans la hiérarchie (1=root)")
    path = Column(Text, nullable=True, comment="Chemin hiérarchique (ex: 1.3.5)")
    sort_order = Column(Integer, default=0, comment="Ordre d'affichage")
    
    # =====================================
    # RESPONSABLE ET ÉQUIPE
    # =====================================
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    assistant_manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # =====================================
    # BUDGET ET COÛTS
    # =====================================
    annual_budget = Column(DECIMAL(15, 2), default=0.0)
    monthly_budget = Column(DECIMAL(15, 2), default=0.0)
    budget_spent = Column(DECIMAL(15, 2), default=0.0)
    budget_remaining = Column(DECIMAL(15, 2), default=0.0)
    
    # =====================================
    # STATISTIQUES
    # =====================================
    employee_count = Column(Integer, default=0)
    active_projects = Column(Integer, default=0)
    completed_projects = Column(Integer, default=0)
    monthly_costs = Column(DECIMAL(15, 2), default=0.0)
    ytd_costs = Column(DECIMAL(15, 2), default=0.0, comment="Coûts Year-To-Date")
    
    # =====================================
    # INFORMATIONS DE CONTACT
    # =====================================
    email = Column(String(200), nullable=True)
    phone = Column(String(50), nullable=True)
    extension = Column(String(10), nullable=True)
    location = Column(String(200), nullable=True, comment="Localisation physique")
    office_number = Column(String(50), nullable=True)
    
    # =====================================
    # DESCRIPTION ET MÉTADONNÉES
    # =====================================
    description = Column(Text, nullable=True)
    mission = Column(Text, nullable=True)
    objectives = Column(JSON, default=list)
    responsibilities = Column(JSON, default=list)
    
    # =====================================
    # CONFIGURATION
    # =====================================
    cost_center = Column(String(50), nullable=True, comment="Centre de coût")
    profit_center = Column(String(50), nullable=True, comment="Centre de profit")
    gl_account = Column(String(50), nullable=True, comment="Compte comptable général")
    
    # =====================================
    # STATUT
    # =====================================
    status = Column(String(20), default="active", comment="active, inactive, archived")
    is_cost_center = Column(Boolean, default=False, comment="Peut générer des coûts")
    is_profit_center = Column(Boolean, default=False, comment="Peut générer des profits")
    is_operational = Column(Boolean, default=True, comment="Département opérationnel")
    
    # =====================================
    # MÉTADONNÉES
    # =====================================
    extra_metadata = Column(JSON, default=dict)
    settings = Column(JSON, default=dict)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    established_date = Column(Date, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    
    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", back_populates="departments")
    parent = relationship("Department", remote_side=[id], back_populates="children")
    children = relationship("Department", back_populates="parent")
    manager = relationship("User", foreign_keys=[manager_id], back_populates="managed_departments")
    assistant_manager = relationship("User", foreign_keys=[assistant_manager_id], back_populates="assisted_departments")
    employees = relationship(
        "User", 
        foreign_keys="[User.department_id]",  # Spécifiez explicitement
        back_populates="department"
    )
    projects = relationship("Project", back_populates="department")
    costs = relationship("Cost", back_populates="department", lazy='select')
    cost_allocations = relationship("CostAllocation", back_populates="department")
    budgets = relationship("Budget", back_populates="department")
    
    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index('ix_departments_tenant_code', 'tenant_id', 'code'),
        Index('ix_departments_tenant_name', 'tenant_id', 'name'),
        Index('ix_departments_tenant_parent', 'tenant_id', 'parent_id'),
        Index('ix_departments_tenant_manager', 'tenant_id', 'manager_id'),
        Index('ix_departments_tenant_status', 'tenant_id', 'status'),
        Index('ix_departments_path', 'path'),
        CheckConstraint('annual_budget >= 0', name='check_annual_budget_positive'),
        CheckConstraint('monthly_budget >= 0', name='check_monthly_budget_positive'),
    )
    
    # =====================================
    # VALIDATIONS
    # =====================================
    @validates('email')
    def validate_email(self, key, email):
        if email and '@' not in email:
            raise ValueError("Format d'email invalide")
        return email
    
    @validates('employee_count')
    def validate_employee_count(self, key, count):
        if count < 0:
            raise ValueError("Le nombre d'employés ne peut pas être négatif")
        return count
    
    # =====================================
    # PROPRIÉTÉS
    # =====================================
    @property
    def full_name(self) -> str:
        """Nom complet du département avec code"""
        if self.short_name:
            return f"{self.code} - {self.name} ({self.short_name})"
        return f"{self.code} - {self.name}"
    
    @property
    def budget_utilization(self) -> float:
        """Pourcentage d'utilisation du budget annuel"""
        if self.annual_budget == 0:
            return 0.0
        return float((self.budget_spent / self.annual_budget) * 100)
    
    @property
    def budget_variance(self) -> float:
        """Variance budgétaire (positif = sous-budget, négatif = dépassement)"""
        return float(self.annual_budget - self.budget_spent)
    
    @property
    def hierarchy_path(self) -> list:
        """Chemin hiérarchique sous forme de liste"""
        if self.path:
            return [int(node) for node in self.path.split('.')]
        return []
    
    @property
    def total_costs_current_month(self) -> Decimal:
        """Coûts totaux du mois courant"""
        # Cette méthode serait implémentée dans une logique métier
        return self.monthly_costs or Decimal('0')
    
    # =====================================
    # MÉTHODES
    # =====================================
    def update_budget_stats(self, spent_amount: Decimal) -> 'Department':
        """Met à jour les statistiques budgétaires"""
        self.budget_spent += spent_amount
        self.budget_remaining = self.annual_budget - self.budget_spent
        return self
    
    def update_employee_count(self) -> 'Department':
        """Met à jour le nombre d'employés"""
        # Compter les employés actifs dans ce département
        from sqlalchemy import func
        from app.models.user import User
        
        count = self.employees.filter(User.is_active == True).count()
        self.employee_count = count
        return self
    
    def add_child(self, child: 'Department') -> 'Department':
        """Ajoute un département enfant"""
        child.parent_id = self.id
        child.level = self.level + 1
        child.path = f"{self.path}.{child.id}" if self.path else str(child.id)
        return self
    
    def archive(self) -> 'Department':
        """Archive le département"""
        self.status = "archived"
        self.archived_at = datetime.utcnow()
        self.is_operational = False
        return self
    
    def to_dict(self, include_children: bool = False) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        result = {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "code": self.code,
            "name": self.name,
            "short_name": self.short_name,
            "department_type": self.department_type,
            "level": self.level,
            "manager_id": str(self.manager_id) if self.manager_id else None,
            "annual_budget": float(self.annual_budget),
            "budget_spent": float(self.budget_spent),
            "budget_remaining": float(self.budget_remaining),
            "budget_utilization": self.budget_utilization,
            "employee_count": self.employee_count,
            "active_projects": self.active_projects,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        
        if include_children:
            result["children"] = [child.to_dict() for child in self.children]
        
        return result
    
    def __repr__(self) -> str:
        return f"<Department {self.code} | {self.name} | Level: {self.level}>"
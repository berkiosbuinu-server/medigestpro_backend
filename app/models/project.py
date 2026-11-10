# app/models/project.py
import uuid
from datetime import datetime, date
from decimal import Decimal
from enum import Enum as PyEnum
from typing import Optional, Dict, Any

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Text, Date, Index, DECIMAL, Integer, CheckConstraint, Float
)
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func

from app.db.base import Base


# =====================================
# ENUMS
# =====================================
class ProjectStatus(str, PyEnum):
    DRAFT = "draft"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"

class ProjectPriority(str, PyEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ProjectType(str, PyEnum):
    INTERNAL = "internal"
    CLIENT = "client"
    RND = "rnd"
    INFRASTRUCTURE = "infrastructure"
    MAINTENANCE = "maintenance"
    OTHER = "other"


# =====================================
# MODÈLE PROJECT
# =====================================
class Project(Base):
    """Modèle représentant un projet"""
    __tablename__ = "projects"

    # =====================================
    # IDENTIFIANT
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    
    # =====================================
    # INFORMATION DE BASE
    # =====================================
    code = Column(String(50), unique=True, nullable=False, index=True, comment="Code unique du projet")
    name = Column(String(200), nullable=False)
    short_name = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    
    # =====================================
    # CLASSIFICATION
    # =====================================
    project_type = Column(String(30), default=ProjectType.INTERNAL.value)
    category = Column(String(100), nullable=True)
    subcategory = Column(String(100), nullable=True)
    tags = Column(JSON, default=list)
    
    # =====================================
    # PÉRIODE ET DURÉE
    # =====================================
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    actual_start_date = Column(Date, nullable=True)
    actual_end_date = Column(Date, nullable=True)
    estimated_duration = Column(Integer, nullable=True, comment="Durée estimée en jours")
    actual_duration = Column(Integer, nullable=True, comment="Durée réelle en jours")
    
    # =====================================
    # BUDGET ET COÛTS
    # =====================================
    budget_allocated = Column(DECIMAL(15, 2), default=0.0)
    budget_spent = Column(DECIMAL(15, 2), default=0.0)
    budget_remaining = Column(DECIMAL(15, 2), default=0.0)
    estimated_cost = Column(DECIMAL(15, 2), default=0.0)
    actual_cost = Column(DECIMAL(15, 2), default=0.0)
    contingency_budget = Column(DECIMAL(15, 2), default=0.0, comment="Budget de contingence")
    
    # =====================================
    # REVENUS ET ROI
    # =====================================
    expected_revenue = Column(DECIMAL(15, 2), default=0.0)
    actual_revenue = Column(DECIMAL(15, 2), default=0.0)
    roi_expected = Column(Float, default=0.0, comment="ROI attendu en %")
    roi_actual = Column(Float, default=0.0, comment="ROI réel en %")
    
    # =====================================
    # RESPONSABILITÉS
    # =====================================
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    department_id = Column(UUID(as_uuid=True), ForeignKey("departments.id"), nullable=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True)
    
    # =====================================
    # STATUT ET PRIORITÉ
    # =====================================
    status = Column(String(20), default=ProjectStatus.DRAFT.value)
    priority = Column(String(20), default=ProjectPriority.MEDIUM.value)
    health_status = Column(String(20), default="green", comment="green, yellow, red")
    
    # =====================================
    # PROGRESSION
    # =====================================
    progress_percentage = Column(Integer, default=0)
    completion_percentage = Column(Integer, default=0)
    milestones_completed = Column(Integer, default=0)
    total_milestones = Column(Integer, default=0)
    
    # =====================================
    # RESSOURCES
    # =====================================
    team_size = Column(Integer, default=0)
    resource_hours = Column(Integer, default=0, comment="Heures de ressources allouées")
    actual_hours = Column(Integer, default=0, comment="Heures réelles travaillées")
    
    # =====================================
    # RISQUES ET PROBLÈMES
    # =====================================
    risk_level = Column(String(20), default="low", comment="low, medium, high")
    risk_count = Column(Integer, default=0)
    issue_count = Column(Integer, default=0)
    change_count = Column(Integer, default=0, comment="Nombre de changements")
    
    # =====================================
    # DOCUMENTATION
    # =====================================
    objectives = Column(JSON, default=list)
    deliverables = Column(JSON, default=list)
    requirements = Column(JSON, default=list)
    constraints = Column(JSON, default=list)
    assumptions = Column(JSON, default=list)
    
    # =====================================
    # MÉTADONNÉES
    # =====================================
    project_metadata = Column(JSON, default=dict)
    attachments = Column(JSON, default=list)
    notes = Column(Text, nullable=True)
    
    # =====================================
    # CONFIGURATION
    # =====================================
    is_billable = Column(Boolean, default=False)
    is_confidential = Column(Boolean, default=False)
    is_template = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    approved_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant", back_populates="projects")
    manager = relationship("User", foreign_keys=[manager_id], back_populates="managed_projects")
    department = relationship("Department", back_populates="projects")
    customer = relationship("Customer", back_populates="projects")
    #team_members = relationship("ProjectMember")
    #tasks = relationship("ProjectTask")
    #milestones = relationship("ProjectMilestone")
    costs = relationship("Cost", back_populates="project")
    cost_allocations = relationship("CostAllocation", back_populates="project")
    budgets = relationship("Budget", back_populates="project")
    
    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index('ix_projects_tenant_code', 'tenant_id', 'code'),
        Index('ix_projects_tenant_status', 'tenant_id', 'status'),
        Index('ix_projects_tenant_manager', 'tenant_id', 'manager_id'),
        Index('ix_projects_tenant_department', 'tenant_id', 'department_id'),
        Index('ix_projects_tenant_customer', 'tenant_id', 'customer_id'),
        Index('ix_projects_tenant_dates', 'tenant_id', 'start_date', 'end_date'),
        Index('ix_projects_tenant_priority', 'tenant_id', 'priority'),
        CheckConstraint('budget_allocated >= 0', name='check_budget_positive'),
        CheckConstraint('progress_percentage >= 0 AND progress_percentage <= 100', name='check_progress_range'),
        CheckConstraint('start_date <= end_date', name='check_project_dates'),
    )
    
    # =====================================
    # VALIDATIONS
    # =====================================
    @validates('budget_allocated', 'budget_spent', 'estimated_cost')
    def validate_amounts(self, key, value):
        """Valide que les montants ne sont pas négatifs"""
        if value < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return value
    
    @validates('progress_percentage', 'completion_percentage')
    def validate_percentages(self, key, value):
        """Valide que les pourcentages sont entre 0 et 100"""
        if value < 0 or value > 100:
            raise ValueError(f"{key} doit être entre 0 et 100")
        return value
    
    # =====================================
    # PROPRIÉTÉS
    # =====================================
    @property
    def budget_utilization(self) -> float:
        """Pourcentage d'utilisation du budget"""
        if self.budget_allocated == 0:
            return 0.0
        return float((self.budget_spent / self.budget_allocated) * 100)
    
    @property
    def cost_variance(self) -> float:
        """Variance des coûts (positif = sous-budget, négatif = dépassement)"""
        return float(self.budget_allocated - self.actual_cost)
    
    @property
    def schedule_variance(self) -> int:
        """Variance du calendrier (positif = en avance, négatif = en retard)"""
        if self.actual_end_date and self.end_date:
            return (self.end_date - self.actual_end_date).days
        return 0
    
    @property
    def days_remaining(self) -> int:
        """Jours restants jusqu'à la date de fin"""
        if self.status == ProjectStatus.COMPLETED.value:
            return 0
        today = date.today()
        if today > self.end_date:
            return 0
        return (self.end_date - today).days
    
    @property
    def days_elapsed(self) -> int:
        """Jours écoulés depuis le début"""
        if self.actual_start_date:
            start = self.actual_start_date
        else:
            start = self.start_date
        
        if self.actual_end_date:
            end = self.actual_end_date
        elif self.status == ProjectStatus.COMPLETED.value:
            end = self.updated_at.date()
        else:
            end = date.today()
        
        return max(0, (end - start).days)
    
    @property
    def is_overdue(self) -> bool:
        """Le projet est-il en retard?"""
        if self.status in [ProjectStatus.COMPLETED.value, ProjectStatus.CANCELLED.value]:
            return False
        return date.today() > self.end_date
    
    @property
    def health_color(self) -> str:
        """Couleur de santé basée sur plusieurs facteurs"""
        if self.health_status:
            return self.health_status
        
        # Calcul automatique basé sur plusieurs métriques
        issues = []
        
        # Vérifier le budget
        if self.budget_utilization > 90:
            issues.append("budget")
        
        # Vérifier le calendrier
        if self.days_remaining < 0:
            issues.append("schedule")
        
        # Vérifier la progression
        if self.progress_percentage < (self.days_elapsed / self.estimated_duration * 100) if self.estimated_duration else 50:
            issues.append("progress")
        
        if len(issues) >= 2:
            return "red"
        elif len(issues) >= 1:
            return "yellow"
        else:
            return "green"
    
    # =====================================
    # MÉTHODES
    # =====================================
    def update_progress(self, new_percentage: int) -> 'Project':
        """Met à jour la progression du projet"""
        if new_percentage < 0 or new_percentage > 100:
            raise ValueError("Le pourcentage doit être entre 0 et 100")
        
        self.progress_percentage = new_percentage
        
        # Si 100%, marquer comme complété
        if new_percentage == 100 and self.status != ProjectStatus.COMPLETED.value:
            self.status = ProjectStatus.COMPLETED.value
            self.completed_at = datetime.utcnow()
            self.actual_end_date = date.today()
            self.completion_percentage = 100
        
        return self
    
    def update_budget(self, spent_amount: Decimal) -> 'Project':
        """Met à jour les informations budgétaires"""
        self.budget_spent += spent_amount
        self.budget_remaining = self.budget_allocated - self.budget_spent
        self.actual_cost += spent_amount
        
        # Mettre à jour la santé si nécessaire
        if self.budget_utilization > 90:
            self.health_status = "red"
        elif self.budget_utilization > 75:
            self.health_status = "yellow"
        
        return self
    
    def add_milestone(self, milestone_name: str, due_date: date) -> 'Project':
        """Ajoute un jalon au projet"""
        self.total_milestones += 1
        # Note: L'implémentation réelle créerait un objet ProjectMilestone
        return self
    
    def complete_milestone(self) -> 'Project':
        """Marque un jalon comme complété"""
        self.milestones_completed += 1
        return self
    
    def calculate_roi(self) -> float:
        """Calcule le ROI réel"""
        if self.actual_cost == 0:
            return 0.0
        
        roi = ((self.actual_revenue - self.actual_cost) / self.actual_cost) * 100
        self.roi_actual = float(roi)
        return roi
    
    def to_dict(self, include_details: bool = False) -> Dict[str, Any]:
        """Convertit en dictionnaire"""
        result = {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "code": self.code,
            "name": self.name,
            "short_name": self.short_name,
            "project_type": self.project_type,
            "status": self.status,
            "priority": self.priority,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "budget_allocated": float(self.budget_allocated),
            "budget_spent": float(self.budget_spent),
            "budget_remaining": float(self.budget_remaining),
            "budget_utilization": self.budget_utilization,
            "progress_percentage": self.progress_percentage,
            "days_remaining": self.days_remaining,
            "is_overdue": self.is_overdue,
            "health_status": self.health_color,
            "manager_id": str(self.manager_id) if self.manager_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        
        if include_details:
            result.update({
                "description": self.description,
                "actual_start_date": self.actual_start_date.isoformat() if self.actual_start_date else None,
                "actual_end_date": self.actual_end_date.isoformat() if self.actual_end_date else None,
                "actual_cost": float(self.actual_cost),
                "expected_revenue": float(self.expected_revenue),
                "actual_revenue": float(self.actual_revenue),
                "roi_expected": self.roi_expected,
                "roi_actual": self.roi_actual,
                "milestones_completed": self.milestones_completed,
                "total_milestones": self.total_milestones,
                "risk_count": self.risk_count,
                "issue_count": self.issue_count,
                "team_size": self.team_size,
                "objectives": self.objectives,
                "deliverables": self.deliverables,
            })
        
        return result
    
    def __repr__(self) -> str:
        return f"<Project {self.code} | {self.name} | {self.status}>"
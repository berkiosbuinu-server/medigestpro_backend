# app/services/user_branch_service.py
"""
Affectation obligatoire d'un utilisateur à une branche (user branche).

Règle métier : tout utilisateur créé par un admin DOIT être affecté à une branche
de son tenant. Aucune affectation « par défaut » silencieuse : la branche est choisie
explicitement, validée (existante, active, du même tenant), puis enregistrée :
  - users.branch_id / users.active_branch_id
  - une ligne user_branches (branche principale de l'utilisateur)
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.branch_scope import parse_uuid
from app.models.branch import Branch
from app.models.user import User
from app.models.user_branch import UserBranch


class BranchAssignmentError(Exception):
    """Erreur d'affectation à une branche (code + message + statut HTTP suggéré)."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def to_detail(self) -> Dict[str, Any]:
        return {"error": self.code, "message": self.message}


# Permissions par défaut dans la branche selon le rôle
_ROLE_BRANCH_PERMISSIONS: Dict[str, Dict[str, bool]] = {
    "pharmacien": {"can_sell": True, "can_manage_stock": True, "can_manage_expenses": False, "can_view_reports": True, "can_manage_users": False},
    "gestionnaire": {"can_sell": True, "can_manage_stock": True, "can_manage_expenses": True, "can_view_reports": True, "can_manage_users": False},
    "vendeur": {"can_sell": True, "can_manage_stock": False, "can_manage_expenses": False, "can_view_reports": False, "can_manage_users": False},
    "caissier": {"can_sell": True, "can_manage_stock": False, "can_manage_expenses": False, "can_view_reports": False, "can_manage_users": False},
    "comptable": {"can_sell": False, "can_manage_stock": False, "can_manage_expenses": True, "can_view_reports": True, "can_manage_users": False},
    "preparateur": {"can_sell": False, "can_manage_stock": True, "can_manage_expenses": False, "can_view_reports": False, "can_manage_users": False},
    "stockiste": {"can_sell": False, "can_manage_stock": True, "can_manage_expenses": False, "can_view_reports": False, "can_manage_users": False},
}
_DEFAULT_BRANCH_PERMISSIONS = {
    "can_sell": False, "can_manage_stock": False, "can_manage_expenses": False,
    "can_view_reports": True, "can_manage_users": False,
}


def resolve_required_branch(db: Session, tenant_id: Any, raw_branch_id: Any) -> Branch:
    """
    Retourne la branche choisie pour l'utilisateur ou lève BranchAssignmentError.
    Aucune valeur par défaut : la branche doit être fournie.
    """
    if raw_branch_id is None or str(raw_branch_id).strip() == "":
        raise BranchAssignmentError(
            "branch_required",
            "Chaque utilisateur doit être affecté à une branche : choisissez une branche (branch_id).",
            400,
        )
    branch_uuid = parse_uuid(raw_branch_id)
    if branch_uuid is None:
        raise BranchAssignmentError("invalid_branch_id", "Identifiant de branche invalide.", 400)

    branch = db.query(Branch).filter(Branch.id == branch_uuid).first()
    if branch is None or getattr(branch, "is_active", True) is False:
        raise BranchAssignmentError("branch_not_found", "Branche introuvable ou inactive.", 404)
    if str(branch.tenant_id) != str(tenant_id):
        raise BranchAssignmentError("branch_not_in_tenant", "Cette branche n'appartient pas à votre tenant.", 403)
    return branch


def check_branch_user_quota(db: Session, branch: Branch) -> None:
    """Respecte la limite d'utilisateurs de l'abonnement de la branche (0 = illimité)."""
    from app.models.branch_subscription import BranchSubscription

    subscription = db.query(BranchSubscription).filter(
        BranchSubscription.branch_id == branch.id
    ).first()
    if subscription is None or not subscription.max_users or subscription.max_users <= 0:
        return
    count = db.query(User).filter(
        User.branch_id == branch.id,
        User.actif == True,  # noqa: E712
    ).count()
    if count >= subscription.max_users:
        raise BranchAssignmentError(
            "branch_user_limit_reached",
            f"Limite d'utilisateurs de la branche atteinte ({count} / {subscription.max_users}). "
            "Passez à un plan supérieur.",
            403,
        )


def assign_user_to_branch(
    db: Session,
    user: User,
    branch: Branch,
    *,
    role: Optional[str] = None,
    created_by: Optional[UUID] = None,
) -> UserBranch:
    """
    Affecte `user` à `branch` : branche principale (branch_id), branche active
    et enregistrement user_branches (primaire). Les autres affectations de
    l'utilisateur ne sont plus primaires.
    """
    user.branch_id = branch.id
    user.active_branch_id = branch.id

    db.query(UserBranch).filter(
        UserBranch.user_id == user.id,
        UserBranch.branch_id != branch.id,
        UserBranch.is_primary == True,  # noqa: E712
    ).update({"is_primary": False}, synchronize_session=False)

    link = db.query(UserBranch).filter(
        UserBranch.user_id == user.id,
        UserBranch.branch_id == branch.id,
    ).first()
    role_value = (role or getattr(user, "role", None) or "").lower() or None
    if link is None:
        link = UserBranch(
            user_id=user.id,
            branch_id=branch.id,
            role_in_branch=role_value,
            permissions=dict(_ROLE_BRANCH_PERMISSIONS.get(role_value or "", _DEFAULT_BRANCH_PERMISSIONS)),
            is_primary=True,
            is_active=True,
            created_by=created_by,
        )
        db.add(link)
    else:
        link.is_primary = True
        link.is_active = True
        if role_value:
            link.role_in_branch = role_value
    return link

#!/usr/bin/env python3
"""
Migration de données : passe les admins existants en « branch none ».

- role = 'admin' (hors super_admin) -> branch_id = NULL et active_branch_id = NULL
- Les abonnements restent portés par les branches (rien n'est modifié côté BranchSubscription)
- Les autres utilisateurs ne sont pas touchés

Usage (depuis la racine du projet) :
    python -m app.scripts.make_admins_branchless          # simulation
    python -m app.scripts.make_admins_branchless --apply  # applique
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.db.session import SessionLocal
from app.models.user import User


def main(apply: bool) -> None:
    db = SessionLocal()
    try:
        admins = db.query(User).filter(User.role == "admin").all()
        changed = 0
        for u in admins:
            if u.branch_id is not None or u.active_branch_id is not None:
                print(f"- {u.email}: branch_id={u.branch_id} active_branch_id={u.active_branch_id} -> None")
                if apply:
                    u.branch_id = None
                    u.active_branch_id = None
                changed += 1
        if apply:
            db.commit()
            print(f"{changed} admin(s) mis à jour.")
        else:
            db.rollback()
            print(f"Simulation : {changed} admin(s) seraient mis à jour. Relancez avec --apply.")
    finally:
        db.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)

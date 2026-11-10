#!/usr/bin/env python3
"""
Script pour créer manuellement un super administrateur
"""
import sys
import os
import secrets
import string

# Ajouter le chemin du projet
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.user import User
from app.core.security import hash_password

def create_super_admin(email: str, nom_complet: str, password: str = None):
    """Crée un super administrateur manuellement"""
    
    if not password:
        # Générer un mot de passe sécurisé
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        password = ''.join(secrets.choice(alphabet) for _ in range(16))
    
    db: Session = SessionLocal()
    
    try:
        # Vérifier si l'email existe déjà
        existing_user = db.query(User).filter(User.email == email.lower()).first()
        if existing_user:
            print(f"❌ L'email {email} est déjà utilisé")
            return None
        
        # Créer le super admin
        super_admin = User(
            tenant_id=None,
            nom_complet=nom_complet,
            email=email.lower(),
            password_hash=hash_password(password),
            role="super_admin",
            actif=True,
            permissions={
                "platform_management": True,
                "tenant_management": True,
                "user_management": True,
                "system_configuration": True
            }
        )
        
        db.add(super_admin)
        db.commit()
        db.refresh(super_admin)
        
        print("\n" + "="*50)
        print("✅ SUPER ADMINISTRATEUR CRÉÉ AVEC SUCCÈS")
        print("="*50)
        print(f"📧 Email: {email}")
        print(f"🔑 Mot de passe: {password}")
        print(f"👤 Nom complet: {nom_complet}")
        print(f"🆔 ID: {super_admin.id}")
        print("="*50)
        print("\n⚠️  CONSERVEZ CES INFORMATIONS DANS UN ENDROIT SÉCURISÉ ⚠️")
        print("="*50)
        
        return super_admin
        
    except Exception as e:
        db.rollback()
        print(f"❌ Erreur lors de la création: {e}")
        return None
    finally:
        db.close()

if __name__ == "__main__":
    print("Création d'un super administrateur")
    print("-" * 30)
    
    email = input("Email: ").strip()
    nom_complet = input("Nom complet: ").strip()
    generate_password = input("Générer un mot de passe aléatoire? (o/N): ").strip().lower()
    
    if generate_password == 'o':
        password = None
    else:
        password = input("Mot de passe (min 8 caractères): ").strip()
        if len(password) < 8:
            print("❌ Le mot de passe doit faire au moins 8 caractères")
            sys.exit(1)
    
    confirm = input(f"\nCréer le super admin {email}? (O/n): ").strip().lower()
    
    if confirm in ['', 'o', 'oui', 'y', 'yes']:
        create_super_admin(email, nom_complet, password)
    else:
        print("❌ Annulé")
# generate_password.py
import bcrypt
import psycopg2
from psycopg2.extras import RealDictCursor
import os

# Configuration de connexion pour Render
DATABASE_URL = "postgresql://medigest_db_jvg2_user:c9U6MAgdfuc2yAca1IXdQhr4JeuTxuLT@dpg-d6ef1tp5pdvs73cttaig-a.frankfurt-postgres.render.com:5432/medigest_db_jvg2"

# Générer le hash pour Delafoi123
password = "Delafoi123"
salt = bcrypt.gensalt(rounds=12)
hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt)
hashed_password_str = hashed_password.decode('utf-8')

print(f"Password: {password}")
print(f"Hash généré: {hashed_password_str}")
print(f"Longueur du hash: {len(hashed_password_str)}")

try:
    # Connexion à la base de données sur Render
    print("\n🔄 Connexion à la base de données Render...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    
    cur = conn.cursor()
    
    # Vérifier d'abord si l'utilisateur existe
    print("🔍 Vérification de l'utilisateur...")
    cur.execute("""
        SELECT email, role, actif 
        FROM users 
        WHERE email = 'admin@delafoi.com'
    """)
    
    user = cur.fetchone()
    
    if user:
        print(f"✅ Utilisateur trouvé: {user[0]} (rôle: {user[1]}, actif: {user[2]})")
        
        # Mettre à jour le mot de passe
        print("🔄 Mise à jour du mot de passe...")
        cur.execute("""
            UPDATE users 
            SET password_hash = %s, 
                updated_at = NOW(),
                login_attempts = 0,
                locked_until = NULL
            WHERE email = 'admin@delafoi.com'
            RETURNING email, role, updated_at
        """, (hashed_password_str,))
        
        result = cur.fetchone()
        
        if result:
            print(f"\n✅ Mot de passe mis à jour avec succès!")
            print(f"   Email: {result[0]}")
            print(f"   Rôle: {result[1]}")
            print(f"   Date mise à jour: {result[2]}")
        else:
            print("❌ Erreur lors de la mise à jour")
            
    else:
        print("❌ L'utilisateur admin@delafoi.com n'existe pas dans la base")
        
        # Lister les utilisateurs admin existants
        cur.execute("""
            SELECT email, role FROM users WHERE role = 'admin' LIMIT 5
        """)
        admins = cur.fetchall()
        if admins:
            print("\n📋 Admins existants:")
            for admin in admins:
                print(f"   - {admin[0]} ({admin[1]})")
    
    # Valider la transaction
    conn.commit()
    print("\n💾 Changements sauvegardés")
    
    # Vérification finale
    cur.execute("""
        SELECT email, role, password_hash IS NOT NULL as hash_exists
        FROM users 
        WHERE email = 'admin@delafoi.com'
    """)
    final_check = cur.fetchone()
    
    if final_check:
        print(f"\n🔐 Vérification finale:")
        print(f"   Email: {final_check[0]}")
        print(f"   Rôle: {final_check[1]}")
        print(f"   Hash présent: {'✅ Oui' if final_check[2] else '❌ Non'}")
    
    cur.close()
    conn.close()
    
    print("\n✅ Opération terminée avec succès!")
    print("\n📝 Vous pouvez maintenant vous connecter avec:")
    print("   Email: admin@delafoi.com")
    print("   Mot de passe: Delafoi123")
    
except psycopg2.OperationalError as e:
    print(f"\n❌ Erreur de connexion à la base de données:")
    print(f"   {e}")
    print("\n💡 Vérifiez que votre IP est autorisée sur Render.com")
    print("   Ou utilisez --ssl-mode=require si nécessaire")
    
except Exception as e:
    print(f"\n❌ Erreur inattendue: {e}")
    conn.rollback() if 'conn' in locals() else None
    
finally:
    if 'conn' in locals() and not conn.closed:
        conn.close()
        print("\n🔌 Connexion fermée")
# fix_payments.py
import os
import sys
from dotenv import load_dotenv

# Charge les variables d'environnement
load_dotenv()

def get_db_url():
    """Récupère l'URL de la base de données depuis votre configuration"""
    # Essayez différentes sources
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return db_url
    
    # Sinon, construisez-la
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "pharma_saas")
    db_user = os.getenv("DB_USER", "postgres")
    db_password = os.getenv("DB_PASSWORD", "")
    
    return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

def parse_db_url(url):
    """Parse l'URL de la base de données"""
    from urllib.parse import urlparse
    
    parsed = urlparse(url)
    return {
        'host': parsed.hostname,
        'port': parsed.port,
        'database': parsed.path[1:],  # Enlève le '/'
        'user': parsed.username,
        'password': parsed.password
    }

def main():
    import psycopg2
    
    try:
        # Récupérer l'URL de la base
        db_url = get_db_url()
        print(f"URL de la base: {db_url}")
        
        if not db_url:
            print("❌ Aucune URL de base de données trouvée")
            print("Vérifiez votre fichier .env ou votre configuration")
            return
        
        # Parser l'URL
        db_config = parse_db_url(db_url)
        
        # Connexion
        print(f"Connexion à {db_config['host']}:{db_config['port']}/{db_config['database']}...")
        
        conn = psycopg2.connect(
            host=db_config['host'],
            port=db_config['port'],
            database=db_config['database'],
            user=db_config['user'],
            password=db_config['password']
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        # Ajouter les colonnes
        sql_commands = [
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS subscription_plan VARCHAR(50)",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS billing_period VARCHAR(20)",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS period_start TIMESTAMP",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS period_end TIMESTAMP",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS subscription_type VARCHAR(30)"
        ]
        
        for sql in sql_commands:
            try:
                cursor.execute(sql)
                print(f"✓ {sql[:50]}...")
            except Exception as e:
                print(f"✗ Erreur: {e}")
        
        # Vérification
        cursor.execute("""
            SELECT COUNT(*) 
            FROM information_schema.columns 
            WHERE table_name = 'payments'
        """)
        count = cursor.fetchone()[0]
        print(f"\n📊 La table 'payments' a maintenant {count} colonnes")
        
        cursor.close()
        conn.close()
        
        print("\n✅ Réparation terminée ! Redémarrez votre application.")
        
    except Exception as e:
        print(f"❌ Erreur: {e}")
        print("\n🔧 Essayez manuellement avec pgAdmin:")
        print("1. Ouvrez pgAdmin")
        print("2. Connectez-vous à votre base")
        print("3. Exécutez ces commandes SQL:")
        print("""
        ALTER TABLE payments 
        ADD COLUMN subscription_plan VARCHAR(50),
        ADD COLUMN billing_period VARCHAR(20),
        ADD COLUMN period_start TIMESTAMP,
        ADD COLUMN period_end TIMESTAMP,
        ADD COLUMN subscription_type VARCHAR(30);
        """)

if __name__ == "__main__":
    main()
# add_columns.py
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

def add_columns():
    conn = None
    try:
        # Modifiez avec vos informations de connexion
        conn = psycopg2.connect(
            host="localhost",
            database= "postgresql://postgres:postgres@localhost:5432/pharma_saas",  # Remplacez
            user="postgres",          # Remplacez
            password="postgres"      # Remplacez
        )
        
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        print("Ajout des colonnes à la table 'payments'...")
        
        # Liste des colonnes à ajouter
        columns = [
            ("subscription_plan", "VARCHAR(50)"),
            ("billing_period", "VARCHAR(20)"),
            ("period_start", "TIMESTAMP"),
            ("period_end", "TIMESTAMP"),
            ("subscription_type", "VARCHAR(30)")
        ]
        
        for col_name, col_type in columns:
            try:
                # Vérifier si la colonne existe déjà
                cursor.execute(f"""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='payments' AND column_name='{col_name}'
                """)
                
                if cursor.fetchone():
                    print(f"✓ La colonne '{col_name}' existe déjà")
                else:
                    # Ajouter la colonne
                    cursor.execute(f"ALTER TABLE payments ADD COLUMN {col_name} {col_type}")
                    print(f"✓ Colonne '{col_name}' ajoutée")
                    
            except Exception as e:
                print(f"✗ Erreur avec '{col_name}': {e}")
        
        # Vérification finale
        cursor.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'payments' 
            ORDER BY ordinal_position
        """)
        
        print("\n📊 Structure actuelle de la table 'payments':")
        for row in cursor.fetchall():
            print(f"  {row[0]}: {row[1]}")
        
        print("\n✅ Migration terminée avec succès !")
        
    except Exception as e:
        print(f"❌ Erreur de connexion: {e}")
        print("Vérifiez vos informations de connexion PostgreSQL")
    finally:
        if conn:
            cursor.close()
            conn.close()

if __name__ == "__main__":
    add_columns()
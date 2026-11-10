# fix_missing_codes.py - À exécuter sur le serveur
from sqlalchemy import create_engine, text
import uuid

def fix_missing_product_codes(engine):
    """Corrige tous les produits et sale_items sans code"""
    
    with engine.connect() as conn:
        # 1. Corriger les produits
        result = conn.execute(text("""
            UPDATE products 
            SET code = CONCAT('PROD_', LEFT(REPLACE(CAST(id AS TEXT), '-', ''), 10))
            WHERE code IS NULL OR code = ''
            RETURNING id, code
        """))
        fixed_products = result.fetchall()
        print(f"✅ {len(fixed_products)} produits corrigés")
        
        # 2. Corriger les sale_items
        result = conn.execute(text("""
            UPDATE sale_items si
            SET product_code = COALESCE(
                (SELECT code FROM products p WHERE p.id = si.product_id),
                CONCAT('AUTO_', LEFT(REPLACE(CAST(si.product_id AS TEXT), '-', ''), 12))
            )
            WHERE si.product_code IS NULL OR si.product_code = ''
            RETURNING id, product_code
        """))
        fixed_items = result.fetchall()
        print(f"✅ {len(fixed_items)} sale_items corrigés")
        
        # 3. Valider
        conn.commit()

# Exécution
if __name__ == "__main__":
    from app.db.session import engine
    fix_missing_product_codes(engine)
# app/schemas/sync.py - Modèles de validation pour la synchronisation

from pydantic import BaseModel, field_validator, ValidationInfo
from typing import List, Dict, Any, Optional
from enum import Enum
import logging

logger = logging.getLogger(__name__)


# ==========================================================
# CONSTANTES ET CONFIGURATION
# ==========================================================

class SyncAction(str, Enum):
    """Actions de synchronisation autorisées"""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    UPSERT = "upsert"


# Configuration des tables autorisées
ALLOWED_TABLES = [
    'products', 'categories', 'orders', 'customers',
    'invoices', 'users', 'tenants', 'subscriptions'
]

# Mapping des alias (français -> anglais)
TABLE_ALIAS_MAPPING = {
    # Produits
    'produits': 'products',
    'produit': 'products',
    'product': 'products',
    'products': 'products',
    
    # Catégories
    'catégories': 'categories',
    'categorie': 'categories',
    'categories': 'categories',
    'category': 'categories',
    
    # Commandes
    'commandes': 'orders',
    'commande': 'orders',
    'orders': 'orders',
    'order': 'orders',
    
    # Clients
    'clients': 'customers',
    'client': 'customers',
    'customers': 'customers',
    'customer': 'customers',
    
    # Factures
    'factures': 'invoices',
    'facture': 'invoices',
    'invoices': 'invoices',
    'invoice': 'invoices',
    
    # Utilisateurs
    'utilisateurs': 'users',
    'utilisateur': 'users',
    'users': 'users',
    'user': 'users',
    
    # Tenants
    'tenants': 'tenants',
    'tenant': 'tenants',
    
    # Abonnements
    'abonnements': 'subscriptions',
    'abonnement': 'subscriptions',
    'subscriptions': 'subscriptions',
    'subscription': 'subscriptions',
}


# ==========================================================
# MODÈLES PYDANTIC
# ==========================================================

class SyncItem(BaseModel):
    """
    Modèle représentant un élément de synchronisation.
    
    Attributes:
        table_name: Nom de la table cible (accepte les alias français)
        action: Action à effectuer (create, update, delete, upsert)
        data: Données à synchroniser (optionnel pour delete)
    """
    table_name: str
    action: str
    data: Optional[Dict[str, Any]] = None
    
    @field_validator('table_name')
    @classmethod
    def validate_table_name(cls, v: str) -> str:
        """
        Valide et normalise le nom de la table.
        
        Args:
            v: Nom de la table à valider
            
        Returns:
            Nom normalisé de la table
            
        Raises:
            ValueError: Si la table n'est pas autorisée
        """
        # Normaliser le nom
        normalized = v.lower().strip()
        
        # Appliquer le mapping des alias
        mapped = TABLE_ALIAS_MAPPING.get(normalized, normalized)
        
        # Vérifier si la table est autorisée
        if mapped not in ALLOWED_TABLES:
            raise ValueError(
                f"Table '{v}' non autorisée. "
                f"Tables autorisées: {', '.join(ALLOWED_TABLES)}\n"
                f"Alias acceptés: {', '.join(TABLE_ALIAS_MAPPING.keys())}"
            )
        
        return mapped
    
    @field_validator('action')
    @classmethod
    def validate_action(cls, v: str) -> str:
        """
        Valide et normalise l'action.
        
        Args:
            v: Action à valider
            
        Returns:
            Action normalisée en minuscules
            
        Raises:
            ValueError: Si l'action n'est pas autorisée
        """
        action_lower = v.lower()
        
        if action_lower not in [action.value for action in SyncAction]:
            raise ValueError(
                f"Action '{v}' non valide. "
                f"Actions autorisées: {', '.join([a.value for a in SyncAction])}"
            )
        
        return action_lower

    @field_validator('data')
    @classmethod
    def validate_data_required(cls, v: Optional[Dict[str, Any]], info: ValidationInfo) -> Optional[Dict[str, Any]]:
        """
        Valide les données en fonction de l'action.
        """
        action = info.data.get('action', '').lower()
        
        # CORRECTION: Pour create, update et upsert, data est obligatoire
        if action in ['create', 'update', 'upsert']:
            if v is None:
                raise ValueError(f"Le champ 'data' est requis pour l'action '{action}'")
            if not isinstance(v, dict):
                raise ValueError("Le champ 'data' doit être un dictionnaire")
            if action == 'create' and not v:
                raise ValueError("Le champ 'data' ne peut pas être vide pour la création")
        
        # Pour delete, data est optionnel
        if action == 'delete' and v is not None:
            if not isinstance(v, dict):
                raise ValueError("Le champ 'data' doit être un dictionnaire")
            if 'id' not in v:
                raise ValueError("L'ID est requis pour la suppression")
        
        return v
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'item en dictionnaire."""
        return {
            "table_name": self.table_name,
            "action": self.action,
            "data": self.data
        }


class SyncPayload(BaseModel):
    """
    Modèle représentant un payload de synchronisation.
    
    Attributes:
        items: Liste des éléments à synchroniser
        metadata: Métadonnées optionnelles (version, timestamp, etc.)
    """
    items: List[SyncItem]
    metadata: Optional[Dict[str, Any]] = None
    
    @field_validator('items')
    @classmethod
    def validate_items_not_empty(cls, v: List[SyncItem]) -> List[SyncItem]:
        """
        Valide que la liste des items n'est pas vide.
        
        Args:
            v: Liste des items à valider
            
        Returns:
            Liste validée
            
        Raises:
            ValueError: Si la liste est vide
        """
        if not v:
            raise ValueError("La liste de synchronisation ne peut pas être vide")
        return v
    
    @field_validator('items')
    @classmethod
    def validate_no_duplicate_items(cls, v: List[SyncItem]) -> List[SyncItem]:
        """
        Vérifie qu'il n'y a pas d'items dupliqués.
        
        Args:
            v: Liste des items à vérifier
            
        Returns:
            Liste validée
        """
        seen = set()
        duplicates = []
        
        for item in v:
            # Créer une clé unique pour chaque item
            item_key = f"{item.table_name}:{item.action}"
            if item_key in seen:
                duplicates.append(item_key)
            seen.add(item_key)
        
        if duplicates:
            # Log un warning mais ne bloque pas
            logger.warning(f"Items dupliqués détectés: {duplicates}")
        
        return v
    
    # ==========================================================
    # MÉTHODES UTILITAIRES
    # ==========================================================
    
    def get_items_by_table(self) -> Dict[str, List[SyncItem]]:
        """
        Groupe les items par table pour un traitement optimisé.
        
        Returns:
            Dictionnaire {table_name: [items]}
        """
        grouped = {}
        for item in self.items:
            if item.table_name not in grouped:
                grouped[item.table_name] = []
            grouped[item.table_name].append(item)
        return grouped
    
    def get_items_by_action(self) -> Dict[str, List[SyncItem]]:
        """
        Groupe les items par action.
        
        Returns:
            Dictionnaire {action: [items]}
        """
        grouped = {}
        for item in self.items:
            if item.action not in grouped:
                grouped[item.action] = []
            grouped[item.action].append(item)
        return grouped
    
    def get_items_by_table_and_action(self) -> Dict[str, Dict[str, List[SyncItem]]]:
        """
        Groupe les items par table puis par action.
        
        Returns:
            Dictionnaire {table: {action: [items]}}
        """
        result = {}
        for item in self.items:
            if item.table_name not in result:
                result[item.table_name] = {}
            if item.action not in result[item.table_name]:
                result[item.table_name][item.action] = []
            result[item.table_name][item.action].append(item)
        return result
    
    def get_valid_items(self) -> List[SyncItem]:
        """
        Récupère uniquement les items valides.
        
        Returns:
            Liste des items valides
        """
        valid_items = []
        for item in self.items:
            try:
                # Vérifier que les données sont présentes pour create/update
                if item.action in ['create', 'update', 'upsert'] and item.data is None:
                    continue
                valid_items.append(item)
            except Exception:
                continue
        return valid_items
    
    def get_invalid_items(self) -> List[Dict[str, Any]]:
        """
        Récupère la liste des items invalides avec leurs erreurs.
        
        Returns:
            Liste des items invalides avec les erreurs associées
        """
        invalid = []
        for item in self.items:
            errors = []
            
            try:
                # Valider le nom de la table
                normalized = item.table_name.lower().strip()
                mapped = TABLE_ALIAS_MAPPING.get(normalized, normalized)
                if mapped not in ALLOWED_TABLES:
                    errors.append(f"Table non autorisée: {item.table_name}")
                
                # Valider l'action
                if item.action.lower() not in [a.value for a in SyncAction]:
                    errors.append(f"Action non autorisée: {item.action}")
                
                # Valider les données
                if item.action in ['create', 'update', 'upsert'] and item.data is None:
                    errors.append("Données manquantes pour cette action")
                if item.action == 'delete' and item.data is not None and 'id' not in item.data:
                    errors.append("L'ID est requis pour la suppression")
                    
            except Exception as e:
                errors.append(str(e))
            
            if errors:
                invalid.append({
                    "item": item.to_dict(),
                    "errors": errors
                })
        
        return invalid
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Génère un résumé du payload de synchronisation.
        
        Returns:
            Dict avec les statistiques du payload
        """
        summary = {
            "total_items": len(self.items),
            "valid_items": len(self.get_valid_items()),
            "invalid_items": len(self.get_invalid_items()),
            "by_table": {},
            "by_action": {},
            "metadata": self.metadata
        }
        
        # Statistiques par table
        for table, items in self.get_items_by_table().items():
            summary["by_table"][table] = {
                "count": len(items),
                "actions": {}
            }
            for action, action_items in self.get_items_by_action().items():
                summary["by_table"][table]["actions"][action] = len([
                    i for i in items if i.action == action
                ])
        
        # Statistiques par action
        for action, items in self.get_items_by_action().items():
            summary["by_action"][action] = len(items)
        
        return summary
    
    def to_batch(self, batch_size: int = 100) -> List['SyncPayload']:
        """
        Divise le payload en plusieurs lots.
        
        Args:
            batch_size: Taille de chaque lot
            
        Returns:
            Liste de payloads plus petits
        """
        batches = []
        for i in range(0, len(self.items), batch_size):
            batch_items = self.items[i:i + batch_size]
            
            # Construction des métadonnées sans utiliser le double splat
            batch_metadata = {}
            if self.metadata:
                batch_metadata.update(self.metadata)
            batch_metadata.update({
                "batch_index": len(batches),
                "total_batches": (len(self.items) + batch_size - 1) // batch_size
            })
            
            batches.append(
                SyncPayload(
                    items=batch_items,
                    metadata=batch_metadata
                )
            )
        return batches


# ==========================================================
# MODÈLES DE RÉPONSE
# ==========================================================

class SyncResponse(BaseModel):
    """
    Modèle de réponse pour la synchronisation.
    """
    status: str
    processed: int
    errors: List[Dict[str, Any]]
    synced_at: str
    summary: Optional[Dict[str, Any]] = None
    
    @classmethod
    def success(cls, processed: int, errors: List[Dict[str, Any]], synced_at: str, summary: Optional[Dict[str, Any]] = None) -> 'SyncResponse':
        """Crée une réponse de succès."""
        return cls(
            status="success",
            processed=processed,
            errors=errors,
            synced_at=synced_at,
            summary=summary
        )
    
    @classmethod
    def error(cls, message: str, processed: int = 0, synced_at: Optional[str] = None) -> 'SyncResponse':
        """Crée une réponse d'erreur."""
        from datetime import datetime
        return cls(
            status="error",
            processed=processed,
            errors=[{"message": message}],
            synced_at=synced_at or datetime.utcnow().isoformat()
        )


class SyncStatusResponse(BaseModel):
    """
    Modèle de réponse pour le statut de synchronisation.
    """
    last_sync: Optional[str]
    status: str
    pending_changes: int
    tenant_id: str
    last_sync_count: Optional[int] = None
    error: Optional[str] = None


# ==========================================================
# FONCTIONS UTILITAIRES
# ==========================================================

def create_sync_payload(items: List[Dict[str, Any]], metadata: Optional[Dict[str, Any]] = None) -> SyncPayload:
    """
    Crée un payload de synchronisation à partir d'une liste de dictionnaires.
    
    Args:
        items: Liste des items à synchroniser
        metadata: Métadonnées optionnelles
        
    Returns:
        SyncPayload validé
    """
    sync_items = []
    for item in items:
        sync_items.append(SyncItem(
            table_name=item.get('table_name'),
            action=item.get('action'),
            data=item.get('data')
        ))
    
    return SyncPayload(items=sync_items, metadata=metadata)


def validate_sync_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Valide un payload de synchronisation et retourne un résultat formaté.
    
    Args:
        data: Données à valider
        
    Returns:
        Dict avec les résultats de validation
    """
    try:
        payload = SyncPayload(**data)
        invalid_items = payload.get_invalid_items()
        
        return {
            "valid": len(invalid_items) == 0,
            "payload": payload,
            "invalid_items": invalid_items,
            "summary": payload.get_summary()
        }
    except Exception as e:
        return {
            "valid": False,
            "error": str(e),
            "payload": None,
            "invalid_items": [],
            "summary": None
        }
# app/services/sync_engine.py
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging
import json

logger = logging.getLogger(__name__)


class SyncEngine:
    """
    Moteur de synchronisation incrémentale avec gestion des conflits
    Supporte le versioning, les conflits, et les mises à jour delta
    """
    
    def __init__(self, db: Session, tenant_id: str):
        self.db = db
        self.tenant_id = tenant_id
        
    # =====================================================
    # PUSH: Mobile → Serveur
    # =====================================================
    def process_push(self, items: List[Dict], device_id: str, client_timestamp: datetime) -> Dict:
        """
        Traite les modifications envoyées par le client
        Retourne les conflits éventuels et les IDs mappés
        """
        results = {
            "processed": 0,
            "conflicts": [],
            "errors": [],
            "mapping": {}
        }
        
        for item in items:
            try:
                table = item.get("table_name")
                action = item.get("action", "CREATE").upper()
                data = item.get("data", {})
                client_version = item.get("version", 1)
                local_id = item.get("local_id")
                
                # Normaliser les données
                data = self._normalize_data(table, data)
                
                # Vérifier si l'élément existe déjà côté serveur
                server_id = data.get("id")
                existing = self._get_existing_record(table, server_id, local_id)
                
                if existing:
                    # Gestion des conflits basée sur les timestamps et versions
                    conflict_result = self._resolve_conflict(
                        table, existing, data, client_version, client_timestamp
                    )
                    
                    if conflict_result.get("resolved"):
                        results["processed"] += 1
                        if conflict_result.get("server_id"):
                            results["mapping"][local_id] = conflict_result["server_id"]
                    else:
                        results["conflicts"].append(conflict_result)
                else:
                    # Nouvel élément - création
                    new_id = self._create_record(table, data, device_id, client_version)
                    results["processed"] += 1
                    
                    # Retourner l'ID serveur au client
                    if local_id:
                        results["mapping"][local_id] = new_id
                        
            except Exception as e:
                logger.error(f"Erreur traitement push {item.get('table_name')}: {e}")
                results["errors"].append({
                    "table": item.get("table_name"),
                    "error": str(e),
                    "data": item.get("data", {})
                })
        
        # Enregistrer les logs de synchronisation
        self._log_sync_operation(device_id, "push", results)
        
        return results
    
    def _resolve_conflict(self, table: str, existing: Dict, new_data: Dict, 
                          client_version: int, client_timestamp: datetime) -> Dict:
        """
        Résout les conflits de synchronisation
        Stratégie: dernier timestamp gagne par défaut
        """
        server_version = existing.get("version", 1)
        server_modified = existing.get("modified_at")
        client_modified = new_data.get("modified_at")
        
        if client_modified:
            client_modified_dt = datetime.fromisoformat(client_modified) if isinstance(client_modified, str) else client_modified
        else:
            client_modified_dt = client_timestamp
        
        # Comparer les timestamps
        if server_modified and server_modified > client_modified_dt:
            # Le serveur a une version plus récente
            return {
                "table": table,
                "local_id": new_data.get("local_id"),
                "server_id": existing.get("id"),
                "server_data": existing,
                "client_data": new_data,
                "resolution": "server_wins",
                "resolved": False  # Nécessite une action manuelle ou auto-resolve
            }
        else:
            # Le client a une version plus récente ou égale
            self._update_record(table, existing.get("id"), new_data, client_version + 1)
            return {
                "resolved": True,
                "server_id": existing.get("id"),
                "resolution": "client_wins"
            }
    
    # =====================================================
    # PULL: Serveur → Mobile
    # =====================================================
    def process_pull(self, last_sync: Optional[datetime], device_id: str, limit: int = 500) -> Dict:
        """
        Récupère les modifications depuis la dernière synchronisation
        Utilise le versioning pour l'efficacité
        """
        if not last_sync:
            # Premier sync - récupérer les données de base
            return self._get_initial_data(limit)
        
        changes = {
            "updates": [],
            "deletes": [],
            "timestamp": datetime.now().isoformat(),
            "server_time": datetime.now().isoformat(),
            "has_more": False
        }
        
        # Récupérer les modifications depuis last_sync
        for table in self._get_sync_tables():
            try:
                # Utiliser le versioning pour les mises à jour
                query = text(f"""
                    SELECT * FROM {table} 
                    WHERE tenant_id = :tenant_id 
                    AND (modified_at > :last_sync OR created_at > :last_sync)
                    AND (deleted = false OR deleted IS NULL)
                    ORDER BY modified_at ASC
                    LIMIT :limit
                """)
                
                updates = self.db.execute(query, {
                    "tenant_id": self.tenant_id,
                    "last_sync": last_sync,
                    "limit": limit
                }).fetchall()
                
                for update in updates:
                    update_dict = dict(update._mapping)
                    # Ne pas inclure les champs sensibles
                    update_dict = self._sanitize_data(table, update_dict)
                    changes["updates"].append({
                        "table": table,
                        "data": update_dict,
                        "version": getattr(update, 'version', 1),
                        "action": "UPDATE"
                    })
                
                # Récupérer les suppressions
                delete_query = text(f"""
                    SELECT id, table_name, deleted_at FROM deleted_records 
                    WHERE tenant_id = :tenant_id 
                    AND deleted_at > :last_sync
                    LIMIT :limit
                """)
                
                deletes = self.db.execute(delete_query, {
                    "tenant_id": self.tenant_id,
                    "last_sync": last_sync,
                    "limit": limit
                }).fetchall()
                
                for delete in deletes:
                    changes["deletes"].append({
                        "table": delete.table_name,
                        "id": delete.id,
                        "action": "DELETE"
                    })
                    
            except Exception as e:
                logger.error(f"Erreur pull table {table}: {e}")
        
        # Vérifier s'il y a plus de données
        changes["has_more"] = len(changes["updates"]) >= limit or len(changes["deletes"]) >= limit
        
        # Enregistrer le log
        self._log_sync_operation(device_id, "pull", {"changes_count": len(changes["updates"])})
        
        return changes
    
    def _get_initial_data(self, limit: int = 500) -> Dict:
        """Récupère les données initiales pour un nouveau client"""
        data = {}
        total_count = 0
        
        for table in self._get_sync_tables():
            try:
                query = text(f"""
                    SELECT * FROM {table} 
                    WHERE tenant_id = :tenant_id 
                    AND (deleted = false OR deleted IS NULL)
                    LIMIT :limit
                """)
                
                records = self.db.execute(query, {
                    "tenant_id": self.tenant_id,
                    "limit": limit
                }).fetchall()
                
                sanitized_records = []
                for record in records:
                    record_dict = dict(record._mapping)
                    record_dict = self._sanitize_data(table, record_dict)
                    sanitized_records.append(record_dict)
                
                data[table] = sanitized_records
                total_count += len(sanitized_records)
                
            except Exception as e:
                logger.error(f"Erreur chargement initial table {table}: {e}")
                data[table] = []
        
        return {
            "data": data,
            "timestamp": datetime.now().isoformat(),
            "server_time": datetime.now().isoformat(),
            "is_initial": True,
            "total_count": total_count
        }
    
    # =====================================================
    # SYNC COMPLET (PUSH + PULL)
    # =====================================================
    def full_sync(self, device_id: str, last_sync: Optional[datetime] = None,
                  changes: Optional[List[Dict]] = None) -> Dict:
        """
        Synchronisation complète bidirectionnelle
        """
        # Push: envoyer les modifications du client
        push_result = {"pushed": 0}
        if changes:
            push_result = self.process_push(changes, device_id, datetime.now())
        
        # Pull: récupérer les modifications du serveur
        pull_result = self.process_pull(last_sync, device_id)
        
        return {
            "status": "success",
            "push": push_result,
            "pull": pull_result,
            "server_time": datetime.now().isoformat(),
            "server_timezone": str(datetime.now().astimezone().tzinfo)
        }
    
    # =====================================================
    # GESTION DES CONFLITS
    # =====================================================
    def resolve_conflicts(self, conflicts: List[Dict], resolution_strategy: str = "server_wins") -> Dict:
        """
        Résout manuellement les conflits
        """
        results = {
            "resolved": 0,
            "failed": 0,
            "details": []
        }
        
        for conflict in conflicts:
            try:
                table = conflict.get("table")
                server_data = conflict.get("server_data")
                client_data = conflict.get("client_data")
                
                if resolution_strategy == "server_wins":
                    # Garder les données serveur
                    self._mark_as_synced(table, server_data.get("id"))
                    results["resolved"] += 1
                    
                elif resolution_strategy == "client_wins":
                    # Écraser avec les données client
                    self._update_record(table, server_data.get("id"), client_data, 
                                       server_data.get("version", 1) + 1)
                    results["resolved"] += 1
                    
                elif resolution_strategy == "merge":
                    # Fusion intelligente
                    merged = self._merge_data(server_data, client_data)
                    self._update_record(table, server_data.get("id"), merged,
                                       max(server_data.get("version", 1), client_data.get("version", 1)) + 1)
                    results["resolved"] += 1
                    
                results["details"].append({
                    "table": table,
                    "id": server_data.get("id"),
                    "strategy": resolution_strategy,
                    "status": "resolved"
                })
                
            except Exception as e:
                logger.error(f"Erreur résolution conflit: {e}")
                results["failed"] += 1
                results["details"].append({
                    "table": conflict.get("table"),
                    "error": str(e),
                    "status": "failed"
                })
        
        return results
    
    # =====================================================
    # MÉTHODES UTILITAIRES
    # =====================================================
    def _get_sync_tables(self) -> List[str]:
        """Liste des tables à synchroniser"""
        return ["produits", "ventes", "factures", "clients", "dettes", "depenses", "paiements_dettes"]
    
    def _get_existing_record(self, table: str, server_id: Optional[int], local_id: Optional[str]) -> Optional[Dict]:
        """Récupère un enregistrement existant"""
        try:
            if server_id:
                query = text(f"""
                    SELECT * FROM {table} 
                    WHERE id = :id AND tenant_id = :tenant_id
                """)
                record = self.db.execute(query, {
                    "id": server_id,
                    "tenant_id": self.tenant_id
                }).fetchone()
                return dict(record._mapping) if record else None
            
            elif local_id:
                query = text(f"""
                    SELECT * FROM {table} 
                    WHERE local_id = :local_id AND tenant_id = :tenant_id
                """)
                record = self.db.execute(query, {
                    "local_id": local_id,
                    "tenant_id": self.tenant_id
                }).fetchone()
                return dict(record._mapping) if record else None
            
        except Exception as e:
            logger.error(f"Erreur récupération enregistrement {table}: {e}")
        
        return None
    
    def _create_record(self, table: str, data: Dict, device_id: str, version: int) -> int:
        """Crée un nouvel enregistrement"""
        # Ajouter les métadonnées
        data["tenant_id"] = self.tenant_id
        data["device_id"] = device_id
        data["version"] = version
        data["created_at"] = datetime.now()
        data["modified_at"] = datetime.now()
        
        # Générer un ID si nécessaire
        if "id" not in data:
            data["id"] = self._generate_id()
        
        # Construire la requête d'insertion
        columns = ', '.join(data.keys())
        placeholders = ', '.join([f":{k}" for k in data.keys()])
        
        query = text(f"""
            INSERT INTO {table} ({columns})
            VALUES ({placeholders})
            RETURNING id
        """)
        
        result = self.db.execute(query, data)
        self.db.commit()
        
        return result.fetchone()[0]
    
    def _update_record(self, table: str, record_id: int, data: Dict, version: int):
        """Met à jour un enregistrement existant"""
        data["version"] = version
        data["modified_at"] = datetime.now()
        
        # Ne pas mettre à jour l'ID
        if "id" in data:
            del data["id"]
        
        set_clause = ', '.join([f"{k}=:{k}" for k in data.keys()])
        
        query = text(f"""
            UPDATE {table} 
            SET {set_clause}
            WHERE id = :id AND tenant_id = :tenant_id
        """)
        
        self.db.execute(query, {**data, "id": record_id, "tenant_id": self.tenant_id})
        self.db.commit()
    
    def _delete_record(self, table: str, record_id: int):
        """Supprime logiquement un enregistrement"""
        query = text(f"""
            UPDATE {table} 
            SET deleted = true, deleted_at = :deleted_at
            WHERE id = :id AND tenant_id = :tenant_id
        """)
        
        self.db.execute(query, {
            "deleted_at": datetime.now(),
            "id": record_id,
            "tenant_id": self.tenant_id
        })
        
        # Enregistrer dans la table des suppressions
        delete_query = text("""
            INSERT INTO deleted_records (tenant_id, table_name, record_id, deleted_at)
            VALUES (:tenant_id, :table_name, :record_id, :deleted_at)
        """)
        
        self.db.execute(delete_query, {
            "tenant_id": self.tenant_id,
            "table_name": table,
            "record_id": record_id,
            "deleted_at": datetime.now()
        })
        
        self.db.commit()
    
    def _normalize_data(self, table: str, data: Dict) -> Dict:
        """Normalise les données avant insertion"""
        normalized = {}
        
        for key, value in data.items():
            # Convertir les dates
            if isinstance(value, str) and key.endswith(('_at', '_date')):
                try:
                    normalized[key] = datetime.fromisoformat(value.replace('Z', '+00:00'))
                except:
                    normalized[key] = value
            else:
                normalized[key] = value
        
        return normalized
    
    def _sanitize_data(self, table: str, data: Dict) -> Dict:
        """Nettoie les données avant envoi au client"""
        sanitized = data.copy()
        
        # Supprimer les champs sensibles
        sensitive_fields = ['password', 'token', 'secret', 'api_key']
        for field in sensitive_fields:
            if field in sanitized:
                del sanitized[field]
        
        return sanitized
    
    def _merge_data(self, server_data: Dict, client_data: Dict) -> Dict:
        """Fusion intelligente des données en conflit"""
        merged = server_data.copy()
        
        for key, value in client_data.items():
            if key not in merged or merged[key] is None:
                merged[key] = value
            elif isinstance(value, dict) and isinstance(merged[key], dict):
                merged[key] = self._merge_data(merged[key], value)
        
        return merged
    
    def _generate_id(self) -> int:
        """Génère un ID unique"""
        import random
        return random.randint(100000, 999999999)
    
    def _log_sync_operation(self, device_id: str, operation: str, result: Dict):
        """Enregistre une opération de synchronisation dans SyncLog"""
        try:
            from app.models.sync_log import SyncLog
            
            log = SyncLog(
                tenant_id=self.tenant_id,
                table_name=f"sync_{operation}",
                action=operation.upper(),
                data={
                    "device_id": device_id,
                    "operation": operation,
                    "result": result
                },
                created_at=datetime.utcnow()
            )
            self.db.add(log)
            self.db.commit()
        except Exception as e:
            logger.error(f"Erreur log sync: {e}")
    
    def _update_sync_cache(self, device_id: str, sync_time: datetime):
        """
        Met à jour le cache de synchronisation
        Note: Utilise SyncLog au lieu d'un cache dédié
        """
        try:
            from app.models.sync_log import SyncLog
            
            # Enregistrer un log spécial pour le cache
            log = SyncLog(
                tenant_id=self.tenant_id,
                table_name="sync_cache",
                action="CACHE_UPDATE",
                data={
                    "device_id": device_id,
                    "sync_time": sync_time.isoformat()
                },
                created_at=datetime.utcnow()
            )
            self.db.add(log)
            self.db.commit()
        except Exception as e:
            logger.error(f"Erreur mise à jour cache: {e}")
    
    def _mark_as_synced(self, table: str, record_id: int):
        """Marque un enregistrement comme synchronisé"""
        try:
            # Enregistrer dans SyncLog que l'enregistrement est synchronisé
            from app.models.sync_log import SyncLog
            
            log = SyncLog(
                tenant_id=self.tenant_id,
                table_name=table,
                action="SYNCED",
                data={"record_id": record_id},
                created_at=datetime.utcnow()
            )
            self.db.add(log)
            self.db.commit()
        except Exception as e:
            logger.error(f"Erreur marquage synced: {e}")


# =====================================================
# API ENDPOINTS
# =====================================================
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from datetime import datetime
import logging

from app.api.deps import get_db, get_current_user, get_current_tenant
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/incremental")
async def incremental_sync(
    payload: Dict[str, Any],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant = Depends(get_current_tenant)
):
    """
    Synchronisation incrémentale optimisée
    Push + Pull en une seule requête
    """
    # Vérifier que l'utilisateur a un tenant
    if not current_tenant and not getattr(current_user, 'is_super_admin', False):
        raise HTTPException(
            status_code=400,
            detail="Utilisateur non associé à un tenant"
        )
    
    tenant_id = str(current_tenant.id) if current_tenant else "super_admin"
    sync_engine = SyncEngine(db, tenant_id)
    
    # Traiter le push
    push_result = {}
    if payload.get("push_items"):
        push_result = sync_engine.process_push(
            payload["push_items"],
            payload.get("device_id", "unknown"),
            datetime.fromisoformat(payload.get("client_time", datetime.now().isoformat()))
        )
    
    # Récupérer les changements pour le pull
    last_sync = None
    if payload.get("last_sync"):
        last_sync = datetime.fromisoformat(payload["last_sync"])
    
    pull_result = sync_engine.process_pull(last_sync, payload.get("device_id", "unknown"))
    
    # En arrière-plan: nettoyer les anciennes versions
    if background_tasks:
        background_tasks.add_task(cleanup_old_versions, db, tenant_id)
    
    return {
        "status": "success",
        "push": push_result,
        "pull": pull_result,
        "server_time": datetime.now().isoformat(),
        "server_timezone": str(datetime.now().astimezone().tzinfo),
        "utc_offset": datetime.now().astimezone().utcoffset().total_seconds() if datetime.now().astimezone().utcoffset() else 0
    }


@router.post("/push")
async def push_changes(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant = Depends(get_current_tenant)
):
    """
    Envoie uniquement les modifications (push)
    """
    # Vérifier que l'utilisateur a un tenant
    if not current_tenant and not getattr(current_user, 'is_super_admin', False):
        raise HTTPException(
            status_code=400,
            detail="Utilisateur non associé à un tenant"
        )
    
    tenant_id = str(current_tenant.id) if current_tenant else "super_admin"
    sync_engine = SyncEngine(db, tenant_id)
    
    result = sync_engine.process_push(
        payload.get("items", []),
        payload.get("device_id", "unknown"),
        datetime.fromisoformat(payload.get("client_time", datetime.now().isoformat()))
    )
    
    return {
        "status": "success",
        "result": result,
        "server_time": datetime.now().isoformat()
    }


@router.get("/pull")
async def pull_changes(
    last_sync: Optional[str] = None,
    device_id: str = "unknown",
    limit: int = 500,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant = Depends(get_current_tenant)
):
    """
    Récupère uniquement les modifications (pull)
    """
    # Vérifier que l'utilisateur a un tenant
    if not current_tenant and not getattr(current_user, 'is_super_admin', False):
        raise HTTPException(
            status_code=400,
            detail="Utilisateur non associé à un tenant"
        )
    
    tenant_id = str(current_tenant.id) if current_tenant else "super_admin"
    sync_engine = SyncEngine(db, tenant_id)
    
    last_sync_dt = datetime.fromisoformat(last_sync) if last_sync else None
    
    result = sync_engine.process_pull(last_sync_dt, device_id, limit)
    
    return {
        "status": "success",
        "changes": result.get("updates", []),
        "deletes": result.get("deletes", []),
        "server_time": datetime.now().isoformat(),
        "has_more": result.get("has_more", False)
    }


@router.get("/server-time")
async def get_server_time():
    """
    Récupère l'heure du serveur pour synchronisation
    Utile pour détecter les fraudes horaires
    """
    now = datetime.now()
    return {
        "server_time": now.isoformat(),
        "server_timestamp": now.timestamp(),
        "timezone": str(now.astimezone().tzinfo),
        "utc_offset": now.astimezone().utcoffset().total_seconds() if now.astimezone().utcoffset() else 0,
        "iso_format": now.isoformat()
    }


@router.post("/resolve-conflicts")
async def resolve_conflicts(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant = Depends(get_current_tenant)
):
    """
    Résout les conflits de synchronisation
    """
    # Vérifier que l'utilisateur a un tenant
    if not current_tenant and not getattr(current_user, 'is_super_admin', False):
        raise HTTPException(
            status_code=400,
            detail="Utilisateur non associé à un tenant"
        )
    
    tenant_id = str(current_tenant.id) if current_tenant else "super_admin"
    sync_engine = SyncEngine(db, tenant_id)
    
    result = sync_engine.resolve_conflicts(
        payload.get("conflicts", []),
        payload.get("strategy", "server_wins")
    )
    
    return {
        "status": "success",
        "result": result
    }


@router.get("/status")
async def get_sync_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant = Depends(get_current_tenant)
):
    """
    Récupère le statut de synchronisation du tenant
    """
    from app.models.sync_log import SyncLog
    
    # Vérifier que l'utilisateur a un tenant
    if not current_tenant and not getattr(current_user, 'is_super_admin', False):
        raise HTTPException(
            status_code=400,
            detail="Utilisateur non associé à un tenant"
        )
    
    tenant_id = str(current_tenant.id) if current_tenant else "super_admin"
    
    last_sync = db.query(SyncLog).filter(
        SyncLog.tenant_id == tenant_id
    ).order_by(SyncLog.created_at.desc()).first()
    
    pending_count = db.query(SyncLog).filter(
        SyncLog.tenant_id == tenant_id,
        SyncLog.action == "PENDING"
    ).count()
    
    # Créer une instance temporaire pour accéder à _get_sync_tables
    sync_engine_temp = SyncEngine(db, tenant_id)
    
    return {
        "last_sync": last_sync.created_at.isoformat() if last_sync else None,
        "pending_count": pending_count,
        "tables": sync_engine_temp._get_sync_tables()
    }


async def cleanup_old_versions(db: Session, tenant_id: str):
    """
    Nettoie les anciennes versions (task background)
    """
    try:
        # Supprimer les logs de plus de 30 jours
        cutoff = datetime.utcnow() - timedelta(days=30)
        
        from app.models.sync_log import SyncLog
        deleted = db.query(SyncLog).filter(
            SyncLog.tenant_id == tenant_id,
            SyncLog.created_at < cutoff
        ).delete()
        
        db.commit()
        logger.info(f"Nettoyage sync logs: {deleted} supprimés")
        
    except Exception as e:
        logger.error(f"Erreur nettoyage versions: {e}")
# app/utils/pagination.py
from typing import List, TypeVar, Generic, Optional, Any
from pydantic.generics import GenericModel
from pydantic import BaseModel
from sqlalchemy.orm import Query
from sqlalchemy import func
import math

T = TypeVar('T')

class PaginatedResponse(GenericModel, Generic[T]):
    """Réponse paginée standard"""
    items: List[T]
    total: int
    page: int
    size: int
    pages: int
    has_next: bool
    has_previous: bool
    next_page: Optional[int] = None
    previous_page: Optional[int] = None

class PaginationParams(BaseModel):
    """Paramètres de pagination"""
    page: int = 1
    size: int = 100
    sort_by: Optional[str] = None
    sort_order: Optional[str] = "desc"  # asc or desc


def paginate(
    query: Query,
    page: int = 1,
    size: int = 100,
    max_size: int = 1000
) -> tuple[List[Any], int, dict]:
    """
    Pagine une requête SQLAlchemy
    
    Args:
        query: Requête SQLAlchemy à paginer
        page: Numéro de page (commence à 1)
        size: Nombre d'éléments par page
        max_size: Taille maximum autorisée
    
    Returns:
        tuple: (items, total, metadata)
    """
    # Valider et ajuster les paramètres
    page = max(1, page)
    size = min(max(1, size), max_size)
    
    # Compter le total
    total = query.count()
    
    # Calculer le nombre de pages
    pages = math.ceil(total / size) if size > 0 else 1
    
    # Ajuster la page si nécessaire
    if page > pages and pages > 0:
        page = pages
    
    # Calculer l'offset
    offset = (page - 1) * size
    
    # Exécuter la requête paginée
    items = query.offset(offset).limit(size).all()
    
    # Métadonnées
    pagina_metadata = {
        "page": page,
        "size": size,
        "total": total,
        "pages": pages,
        "has_next": page < pages,
        "has_previous": page > 1,
        "next_page": page + 1 if page < pages else None,
        "previous_page": page - 1 if page > 1 else None
    }
    
    return items, total, pagina_metadata


def paginate_with_model(
    query: Query,
    model_class,
    page: int = 1,
    size: int = 100
) -> PaginatedResponse:
    """
    Pagine une requête et retourne un objet PaginatedResponse
    
    Args:
        query: Requête SQLAlchemy
        model_class: Classe Pydantic pour les items
        page: Numéro de page
        size: Taille de page
    
    Returns:
        PaginatedResponse: Réponse paginée
    """
    items, total, metadata = paginate(query, page, size)
    
    # Convertir les items en modèles Pydantic
    pydantic_items = [model_class.from_orm(item) for item in items]
    
    return PaginatedResponse(
        items=pydantic_items,
        total=total,
        page=metadata["page"],
        size=metadata["size"],
        pages=metadata["pages"],
        has_next=metadata["has_next"],
        has_previous=metadata["has_previous"],
        next_page=metadata["next_page"],
        previous_page=metadata["previous_page"]
    )


def apply_sorting(
    query: Query,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = "desc",
    default_sort: str = "created_at"
) -> Query:
    """
    Applique un tri à une requête
    
    Args:
        query: Requête SQLAlchemy
        sort_by: Champ à trier
        sort_order: Ordre (asc/desc)
        default_sort: Tri par défaut
    
    Returns:
        Query: Requête triée
    """
    from sqlalchemy import desc, asc
    
    if not sort_by:
        sort_by = default_sort
    
    # Obtenir l'attribut de tri
    try:
        sort_column = getattr(query.column_descriptions[0]['entity'], sort_by, None)
        if sort_column is None:
            sort_column = getattr(query.column_descriptions[0]['entity'], default_sort)
    except (AttributeError, IndexError):
        # Si on ne peut pas déterminer la colonne, retourner la requête non triée
        return query
    
    # Appliquer le tri
    if sort_order and sort_order.lower() == "asc":
        query = query.order_by(asc(sort_column))
    else:
        query = query.order_by(desc(sort_column))
    
    return query


class PaginationHelper:
    """Helper pour la pagination avancée"""
    
    @staticmethod
    def get_page_range(current_page: int, total_pages: int, max_display: int = 5) -> List[int]:
        """
        Calcule la plage de pages à afficher
        """
        if total_pages <= max_display:
            return list(range(1, total_pages + 1))
        
        half = max_display // 2
        start = max(1, current_page - half)
        end = min(total_pages, current_page + half)
        
        if start == 1:
            end = max_display
        elif end == total_pages:
            start = total_pages - max_display + 1
        
        return list(range(start, end + 1))
    
    @staticmethod
    def calculate_skip(page: int, size: int) -> int:
        """Calcule la valeur skip (offset)"""
        return (page - 1) * size
    
    @staticmethod
    def get_pagination_headers(total: int, page: int, size: int) -> dict:
        """Retourne les en-têtes HTTP de pagination"""
        pages = math.ceil(total / size) if size > 0 else 1
        
        return {
            "X-Total-Count": str(total),
            "X-Page": str(page),
            "X-Per-Page": str(size),
            "X-Total-Pages": str(pages),
            "X-Has-Next": str(page < pages).lower(),
            "X-Has-Previous": str(page > 1).lower()
        }
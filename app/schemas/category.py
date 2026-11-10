# app/schemas/category.py
from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID
from datetime import datetime


class CategoryBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    is_active: bool = True
    parent_id: Optional[UUID] = None


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    is_active: Optional[bool] = None
    parent_id: Optional[UUID] = None


class CategoryResponse(CategoryBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class CategoryListResponse(BaseModel):
    """Réponse pour la liste des catégories"""
    total: int
    skip: int
    limit: int
    categories: List[CategoryResponse]
    
    class Config:
        from_attributes = True


class CategoryTreeResponse(CategoryResponse):
    """Catégorie avec ses sous-catégories"""
    children: List['CategoryTreeResponse'] = []
    
    class Config:
        from_attributes = True


# Pour résoudre la référence récursive
CategoryTreeResponse.model_rebuild()
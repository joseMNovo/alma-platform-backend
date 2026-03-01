from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List
from datetime import datetime


# ── Comentarios ────────────────────────────────────────────────────────

class IdeaCommentCreate(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El comentario no puede estar vacío")
        return v.strip()


class IdeaCommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    idea_id: int
    volunteer_id: int
    volunteer_name: Optional[str] = None
    body: str
    created_at: Optional[datetime] = None


# ── Ideas ──────────────────────────────────────────────────────────────

class IdeaBase(BaseModel):
    title: str
    body: str
    category: Optional[str] = None

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El título no puede estar vacío")
        return v.strip()

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El cuerpo no puede estar vacío")
        return v.strip()

    @field_validator("category")
    @classmethod
    def category_strip(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            return v if v else None
        return None


class IdeaCreate(IdeaBase):
    created_by_volunteer_id: int


class IdeaUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    category: Optional[str] = None

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("El título no puede estar vacío")
        return v.strip() if v else v

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("El cuerpo no puede estar vacío")
        return v.strip() if v else v

    @field_validator("category")
    @classmethod
    def category_strip(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            return v if v else None
        return None


class Idea(IdeaBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_by_volunteer_id: int
    created_by_name: Optional[str] = None
    comment_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

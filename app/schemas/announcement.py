from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional
from datetime import datetime

_AUDIENCES = {"all", "admin", "voluntario", "participante"}
_USER_TYPES = {"voluntario", "participante"}


# ── Anuncios ───────────────────────────────────────────────────────────

class AnnouncementBase(BaseModel):
    title: str
    body: str
    audience: str = "all"
    is_active: bool = True
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None

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

    @field_validator("audience")
    @classmethod
    def audience_valid(cls, v: str) -> str:
        if v not in _AUDIENCES:
            raise ValueError(f"audience inválida: {v}")
        return v


class AnnouncementCreate(AnnouncementBase):
    pass


class AnnouncementUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    audience: Optional[str] = None
    is_active: Optional[bool] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None

    @field_validator("audience")
    @classmethod
    def audience_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _AUDIENCES:
            raise ValueError(f"audience inválida: {v}")
        return v


class AnnouncementOut(AnnouncementBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Descarte ("no volver a mostrar") ────────────────────────────────────

class DismissCreate(BaseModel):
    user_type: str
    user_id: int

    @field_validator("user_type")
    @classmethod
    def user_type_valid(cls, v: str) -> str:
        if v not in _USER_TYPES:
            raise ValueError(f"user_type inválido: {v}")
        return v

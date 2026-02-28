from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime


class GrupoBase(BaseModel):
    name: str
    description: Optional[str] = None
    coordinator: Optional[str] = None
    day: Optional[str] = None
    schedule: Optional[str] = None
    participants: int = 0
    status: str = "activo"


class GrupoCreate(GrupoBase):
    pass


class GrupoUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    coordinator: Optional[str] = None
    day: Optional[str] = None
    schedule: Optional[str] = None
    participants: Optional[int] = None
    status: Optional[str] = None


class Grupo(GrupoBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

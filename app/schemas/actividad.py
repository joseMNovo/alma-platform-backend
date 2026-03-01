from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime


class ActividadBase(BaseModel):
    name: str
    description: Optional[str] = None
    status: str = "activo"


class ActividadCreate(ActividadBase):
    created_by_volunteer_id: Optional[int] = None


class ActividadUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


class Actividad(ActividadBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by_volunteer_id: Optional[int] = None
    created_by_name: Optional[str] = None

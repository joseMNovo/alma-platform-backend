from pydantic import BaseModel, ConfigDict
from typing import Optional, Literal
from datetime import date, datetime


class InscripcionBase(BaseModel):
    user_id: int
    type: Literal["taller", "grupo", "actividad"]
    item_id: int
    enrollment_date: date
    status: str = "confirmada"


class InscripcionCreate(InscripcionBase):
    pass


class InscripcionUpdate(BaseModel):
    status: Optional[str] = None
    enrollment_date: Optional[date] = None


class Inscripcion(InscripcionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None

from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional
from datetime import date, datetime

from app.utils.text import normalize_name


# ── Personas (base de datos ALMA) ─────────────────────────────────────
# Reutiliza la tabla `participant_profiles` como registro maestro de
# personas. El login (participant_id) es opcional: existe solo cuando la
# persona se registró en la plataforma.

class PersonaBase(BaseModel):
    name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    cuit: Optional[str] = None
    # Se imprime en el certificado de finalización.
    dni: Optional[str] = None
    is_member: bool = False
    birth_date: Optional[date] = None
    address: Optional[str] = None
    floor: Optional[str] = None
    apartment: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    postal_code: Optional[str] = None
    phone: Optional[str] = None

    @field_validator("name", "last_name")
    @classmethod
    def _normalize_names(cls, v):
        return normalize_name(v)


class PersonaCreate(PersonaBase):
    source: Optional[str] = "manual"


class PersonaUpdate(BaseModel):
    name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    cuit: Optional[str] = None
    dni: Optional[str] = None
    is_member: Optional[bool] = None
    birth_date: Optional[date] = None
    address: Optional[str] = None
    floor: Optional[str] = None
    apartment: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    postal_code: Optional[str] = None
    phone: Optional[str] = None

    @field_validator("name", "last_name")
    @classmethod
    def _normalize_names(cls, v):
        return normalize_name(v)


class Persona(PersonaBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    participant_id: Optional[int] = None   # presente => la persona tiene cuenta de login
    is_volunteer: bool = False             # rol voluntario (flag descriptivo)
    volunteer_id: Optional[int] = None     # presente => tiene ficha en `voluntarios`
    source: Optional[str] = None
    invited_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

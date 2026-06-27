from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Any
from datetime import date, datetime


class VoluntarioBase(BaseModel):
    name: str
    last_name: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    photo: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    registration_date: date
    birth_date: Optional[date] = None
    status: str = "pendiente"
    specialties: Optional[Any] = None
    is_admin: bool = False
    email_verified: bool = False
    email_verified_at: Optional[datetime] = None


class VoluntarioCreate(VoluntarioBase):
    pass


class VoluntarioUpdate(BaseModel):
    name: Optional[str] = None
    last_name: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    photo: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    registration_date: Optional[date] = None
    birth_date: Optional[date] = None
    status: Optional[str] = None
    specialties: Optional[Any] = None
    is_admin: Optional[bool] = None
    pin_hash: Optional[str] = None
    email_verified: Optional[bool] = None
    email_verified_at: Optional[datetime] = None


class VoluntarioAuth(BaseModel):
    """Schema especial para autenticación — incluye pin_hash."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    last_name: Optional[str] = None
    email: Optional[str] = None
    status: str
    is_admin: bool
    pin_hash: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    birth_date: Optional[date] = None
    photo: Optional[str] = None
    specialties: Optional[Any] = None


class Voluntario(VoluntarioBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class VoluntarioRegister(BaseModel):
    name: str
    last_name: Optional[str] = None
    email: str
    phone: Optional[str] = None
    birth_date: Optional[date] = None
    gender: Optional[str] = None
    age: Optional[int] = None


class VoluntarioEnrollFromDb(BaseModel):
    """Alta de voluntario disparada desde el módulo Base de datos (personas).
    Crea la ficha en `voluntarios` (pendiente de aprobación) y la vincula a la
    persona maestra. Si persona_id viene, habilita una persona ya existente;
    si no, se crea/linkea la persona por email."""
    name: str
    last_name: Optional[str] = None
    email: str
    phone: Optional[str] = None
    birth_date: Optional[date] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    persona_id: Optional[int] = None         # persona existente a habilitar
    registered_by_name: Optional[str] = None # quién la dio de alta (para el mail)

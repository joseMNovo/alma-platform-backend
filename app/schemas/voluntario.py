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
    status: str = "activo"
    specialties: Optional[Any] = None
    is_admin: bool = False
    auth_user_id: Optional[int] = None


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
    auth_user_id: Optional[int] = None
    pin_hash: Optional[str] = None


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


class Voluntario(VoluntarioBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

from pydantic import BaseModel, ConfigDict
from typing import Optional, Literal
from datetime import date, datetime


# ── Participants ──────────────────────────────────────────────────────

class ParticipantBase(BaseModel):
    email: str
    is_active: bool = True


class ParticipantCreate(ParticipantBase):
    pin_hash: Optional[str] = None


class ParticipantUpdate(BaseModel):
    email: Optional[str] = None
    is_active: Optional[bool] = None
    pin_hash: Optional[str] = None


class ParticipantAuth(BaseModel):
    """Schema especial para autenticación — incluye pin_hash."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    is_active: bool
    pin_hash: Optional[str] = None


class Participant(ParticipantBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Participant Profiles ──────────────────────────────────────────────

class ParticipantProfileBase(BaseModel):
    participant_id: int
    name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    birth_date: Optional[date] = None
    city: Optional[str] = None
    province: Optional[str] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    notes: Optional[str] = None
    accepts_notifications: bool = False
    accepts_whatsapp: bool = False


class ParticipantProfileCreate(ParticipantProfileBase):
    pass


class ParticipantProfileUpdate(BaseModel):
    name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    birth_date: Optional[date] = None
    city: Optional[str] = None
    province: Optional[str] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    notes: Optional[str] = None
    accepts_notifications: Optional[bool] = None
    accepts_whatsapp: Optional[bool] = None


class ParticipantProfile(ParticipantProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Participant Program Enrollments ───────────────────────────────────

class ParticipantProgramEnrollmentBase(BaseModel):
    participant_id: int
    type: Literal["taller", "grupo", "actividad"]
    item_id: int


class ParticipantProgramEnrollmentCreate(ParticipantProgramEnrollmentBase):
    pass


class ParticipantProgramEnrollment(ParticipantProgramEnrollmentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    enrolled_at: Optional[datetime] = None

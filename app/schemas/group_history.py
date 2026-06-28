from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List
from datetime import datetime, date

from app.utils.text import normalize_name


# ── Asistentes ──────────────────────────────────────────────────────────

class GroupHistoryAttendeeBase(BaseModel):
    person_profile_id: Optional[int] = None
    person_name: str
    person_age: Optional[int] = None
    patient_name: Optional[str] = None
    patient_age: Optional[int] = None
    relationship: Optional[str] = None
    problematica: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("person_name")
    @classmethod
    def _person_name(cls, v: str) -> str:
        v = normalize_name(v)
        if not v or not v.strip():
            raise ValueError("El nombre del asistente es obligatorio")
        return v

    @field_validator("patient_name")
    @classmethod
    def _patient_name(cls, v: Optional[str]) -> Optional[str]:
        return normalize_name(v)


class GroupHistoryAttendeeOut(GroupHistoryAttendeeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None


# ── Historial (encuentro) ───────────────────────────────────────────────

class GroupHistoryBase(BaseModel):
    group_id: Optional[int] = None
    group_name: Optional[str] = None
    title: Optional[str] = None
    session_date: Optional[date] = None
    coordinator_volunteer_id: Optional[int] = None
    summary: Optional[str] = None


class GroupHistoryCreate(GroupHistoryBase):
    created_by_volunteer_id: Optional[int] = None
    attendees: List[GroupHistoryAttendeeBase] = []


class GroupHistoryUpdate(GroupHistoryBase):
    # Si `attendees` viene presente, reemplaza la lista completa de asistentes.
    attendees: Optional[List[GroupHistoryAttendeeBase]] = None


class GroupHistorySuggestion(BaseModel):
    """Sugerencia de autocompletado de nombre de asistente."""
    label: str                              # nombre a mostrar / autocompletar
    source: str                             # "participante" | "fichero"
    person_profile_id: Optional[int] = None


class GroupHistoryOut(GroupHistoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_by_volunteer_id: Optional[int] = None
    coordinator_name: Optional[str] = None
    created_by_name: Optional[str] = None
    attendee_count: int = 0
    attendees: List[GroupHistoryAttendeeOut] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

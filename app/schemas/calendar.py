from pydantic import BaseModel, ConfigDict
from typing import Optional, Literal, List, Any
# `date` se importa con alias para que el campo llamado `date` (con default) no sombree el tipo
from datetime import date as date_type, time, datetime


# ── Calendario rico (con voluntarios JOIN) ────────────────────────────

class VolunteerRef(BaseModel):
    id: int
    name: str
    last_name: str


class CalendarInstanceRich(BaseModel):
    """Instancia de calendario con información de coordinadores incluida."""
    id: int
    type: str
    source_id: Optional[int] = None
    title: Optional[str] = None
    date: str
    start_time: str
    end_time: str
    notes: Optional[str] = None
    status: str
    notify_enabled: bool = False
    reminder_offsets: Optional[List[int]] = None
    created_by_volunteer_id: Optional[int] = None
    coordinator: Optional[VolunteerRef] = None
    # co_coordinator (singular) se conserva por compatibilidad; la UI usa la lista.
    co_coordinator: Optional[VolunteerRef] = None
    co_coordinators: List[VolunteerRef] = []
    # Conteo real de participantes anotados a ESTE evento (no cancelados).
    participants_count: int = 0
    volunteers: List[VolunteerRef] = []


# ── Operaciones bulk ──────────────────────────────────────────────────

class BulkDeleteFilters(BaseModel):
    scope: str  # 'month' | 'type' | 'series' | 'all'
    year: Optional[int] = None
    month: Optional[int] = None
    type: Optional[str] = None
    source_id: Optional[int] = None


class GenerateCalendarParams(BaseModel):
    start_date: str
    end_date: str
    first_type: str
    start_time: str = "10:00:00"
    interval_days: int = 14
    source_group_id: Optional[int] = None
    source_workshop_id: Optional[int] = None


class AssignmentUpsertRequest(BaseModel):
    volunteer_id: int


class VolunteerListRequest(BaseModel):
    """Reemplaza la lista completa de voluntarios (role='volunteer') de un evento."""
    volunteer_ids: List[int]


# ── Calendar Instances ────────────────────────────────────────────────

class CalendarInstanceBase(BaseModel):
    type: Literal["grupo", "taller", "actividad"]
    source_id: Optional[int] = None
    title: Optional[str] = None
    date: date_type
    start_time: time = time(10, 0)
    end_time: time = time(12, 0)
    notes: Optional[str] = None
    status: Literal["programado", "realizado", "cancelado"] = "programado"
    notify_enabled: bool = False
    reminder_offsets: Optional[List[int]] = None
    created_by_volunteer_id: Optional[int] = None


class CalendarInstanceCreate(CalendarInstanceBase):
    pass


class CalendarInstanceUpdate(BaseModel):
    type: Optional[Literal["grupo", "taller", "actividad"]] = None
    source_id: Optional[int] = None
    title: Optional[str] = None
    date: Optional[date_type] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    notes: Optional[str] = None
    status: Optional[Literal["programado", "realizado", "cancelado"]] = None
    notify_enabled: Optional[bool] = None
    reminder_offsets: Optional[List[int]] = None


class CalendarInstance(CalendarInstanceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Calendar Assignments ──────────────────────────────────────────────

class CalendarAssignmentBase(BaseModel):
    instance_id: int
    volunteer_id: int
    role: Literal["coordinator", "co_coordinator", "volunteer"]


class CalendarAssignmentCreate(CalendarAssignmentBase):
    pass


class CalendarAssignmentUpdate(BaseModel):
    volunteer_id: Optional[int] = None
    role: Optional[Literal["coordinator", "co_coordinator", "volunteer"]] = None


class CalendarAssignment(CalendarAssignmentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Calendar Event Participants ───────────────────────────────────────

class CalendarEventParticipantBase(BaseModel):
    event_id: int
    participant_id: int
    status: Literal["inscripto", "cancelado", "asistio"] = "inscripto"


class CalendarEventParticipantCreate(CalendarEventParticipantBase):
    pass


class CalendarEventParticipantUpdate(BaseModel):
    status: Optional[Literal["inscripto", "cancelado", "asistio"]] = None


class CalendarEventParticipant(CalendarEventParticipantBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

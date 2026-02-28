from pydantic import BaseModel, ConfigDict
from typing import Optional, Literal, List, Any
from datetime import date, time, datetime


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
    date: str
    start_time: str
    end_time: str
    notes: Optional[str] = None
    status: str
    coordinator: Optional[VolunteerRef] = None
    co_coordinator: Optional[VolunteerRef] = None


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


# ── Calendar Instances ────────────────────────────────────────────────

class CalendarInstanceBase(BaseModel):
    type: Literal["grupo", "taller", "actividad"]
    source_id: Optional[int] = None
    date: date
    start_time: time = time(10, 0)
    end_time: time = time(12, 0)
    notes: Optional[str] = None
    status: Literal["programado", "realizado", "cancelado"] = "programado"


class CalendarInstanceCreate(CalendarInstanceBase):
    pass


class CalendarInstanceUpdate(BaseModel):
    type: Optional[Literal["grupo", "taller", "actividad"]] = None
    source_id: Optional[int] = None
    date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    notes: Optional[str] = None
    status: Optional[Literal["programado", "realizado", "cancelado"]] = None


class CalendarInstance(CalendarInstanceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Calendar Assignments ──────────────────────────────────────────────

class CalendarAssignmentBase(BaseModel):
    instance_id: int
    volunteer_id: int
    role: Literal["coordinator", "co_coordinator"]


class CalendarAssignmentCreate(CalendarAssignmentBase):
    pass


class CalendarAssignmentUpdate(BaseModel):
    volunteer_id: Optional[int] = None
    role: Optional[Literal["coordinator", "co_coordinator"]] = None


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

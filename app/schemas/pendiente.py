from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime


class PendienteBase(BaseModel):
    id: str
    description: str
    assigned_volunteer_id: Optional[str] = None
    completed: bool = False
    created_date: datetime
    completed_date: Optional[datetime] = None


class PendienteCreate(PendienteBase):
    pass


class PendienteUpdate(BaseModel):
    description: Optional[str] = None
    assigned_volunteer_id: Optional[str] = None
    completed: Optional[bool] = None
    completed_date: Optional[datetime] = None


class Pendiente(PendienteBase):
    model_config = ConfigDict(from_attributes=True)

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Pending Items ────────────────────────────────────────────────────

class PendingItemBase(BaseModel):
    id: str
    pending_id: str
    description: str
    assigned_volunteer_id: Optional[str] = None
    completed: bool = False
    created_date: datetime
    completed_date: Optional[datetime] = None


class PendingItemCreate(PendingItemBase):
    pass


class PendingItemUpdate(BaseModel):
    description: Optional[str] = None
    assigned_volunteer_id: Optional[str] = None
    completed: Optional[bool] = None
    completed_date: Optional[datetime] = None


class PendingItem(PendingItemBase):
    model_config = ConfigDict(from_attributes=True)

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Sync (reemplaza toda la tabla en una sola operación) ──────────────

class PendingItemSync(BaseModel):
    id: str
    description: str
    assigned_volunteer_id: Optional[str] = None
    completed: bool = False
    created_date: datetime
    completed_date: Optional[datetime] = None


class PendienteSyncTask(BaseModel):
    id: str
    description: str
    assigned_volunteer_id: Optional[str] = None
    completed: bool = False
    created_date: datetime
    completed_date: Optional[datetime] = None
    sub_items: List[PendingItemSync] = []


class SyncPendientesRequest(BaseModel):
    tasks: List[PendienteSyncTask]

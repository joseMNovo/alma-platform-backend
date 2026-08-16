from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, Dict
from datetime import datetime

_EVENT_TYPES = {"login", "view", "create", "edit", "delete"}
_USER_TYPES = {"voluntario", "participante"}


class ActivityEventCreate(BaseModel):
    event_type: str
    module: Optional[str] = None
    action: Optional[str] = None
    user_type: str
    user_id: int
    role: str

    @field_validator("event_type")
    @classmethod
    def event_type_valid(cls, v: str) -> str:
        if v not in _EVENT_TYPES:
            raise ValueError(f"event_type inválido: {v}")
        return v

    @field_validator("user_type")
    @classmethod
    def user_type_valid(cls, v: str) -> str:
        if v not in _USER_TYPES:
            raise ValueError(f"user_type inválido: {v}")
        return v


class ActivityEventOut(ActivityEventCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None


class ActivityUserSummary(BaseModel):
    user_type: str
    user_id: int
    role: str
    login_count: int
    last_login: Optional[datetime] = None
    # Último evento de cualquier tipo: cuándo se lo vio por última vez, que
    # no es lo mismo que cuándo empezó la sesión.
    last_seen: Optional[datetime] = None
    view_counts: Dict[str, int]
    action_counts: Dict[str, int]

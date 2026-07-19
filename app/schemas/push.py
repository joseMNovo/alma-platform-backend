from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional
from datetime import datetime

_USER_TYPES = {"voluntario", "participante"}


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class SubscribeRequest(BaseModel):
    """Payload que manda el navegador al suscribirse (via el BFF de Next)."""
    user_type: str
    user_id: int
    endpoint: str
    keys: PushKeys
    user_agent: Optional[str] = None

    @field_validator("user_type")
    @classmethod
    def user_type_valid(cls, v: str) -> str:
        if v not in _USER_TYPES:
            raise ValueError(f"user_type inválido: {v}")
        return v

    @field_validator("endpoint")
    @classmethod
    def endpoint_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("endpoint no puede estar vacío")
        return v.strip()


class UnsubscribeRequest(BaseModel):
    endpoint: str


class PushSubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_type: str
    user_id: int
    endpoint: str
    created_at: Optional[datetime] = None

from pydantic import BaseModel, ConfigDict, field_validator
from typing import List, Optional
from datetime import datetime

_USER_TYPES = {"voluntario", "participante"}
_KINDS = {"announcement", "calendar_reminder", "calendar_new", "system"}


class NotificationCreate(BaseModel):
    """Crear una notificación in-app para un usuario (uso interno del backend)."""
    user_type: str
    user_id: int
    title: str
    body: Optional[str] = None
    kind: str = "system"
    url: Optional[str] = None

    @field_validator("user_type")
    @classmethod
    def user_type_valid(cls, v: str) -> str:
        if v not in _USER_TYPES:
            raise ValueError(f"user_type inválido: {v}")
        return v

    @field_validator("kind")
    @classmethod
    def kind_valid(cls, v: str) -> str:
        if v not in _KINDS:
            raise ValueError(f"kind inválido: {v}")
        return v

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El título no puede estar vacío")
        return v.strip()


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    body: Optional[str] = None
    kind: str
    url: Optional[str] = None
    is_read: bool
    read_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class UnreadCountOut(BaseModel):
    unread: int


# ── Broadcast (admin: escribir y lanzar a una audiencia) ────────────────

_AUDIENCES = {"all", "voluntario", "participante"}


class BroadcastRequest(BaseModel):
    title: str
    body: str = ""
    audience: str = "all"
    url: Optional[str] = None
    # Si True, además crea un anuncio (popup al ingresar) para esa audiencia.
    also_popup: bool = False
    # Si viene una lista de IDs de voluntarios, se envía SOLO a esos (ignora
    # 'audience' para elegir destinatarios). Si es None/vacía, se usa 'audience'.
    volunteer_ids: Optional[List[int]] = None

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El título no puede estar vacío")
        return v.strip()

    @field_validator("audience")
    @classmethod
    def audience_valid(cls, v: str) -> str:
        if v not in _AUDIENCES:
            raise ValueError(f"audience inválida: {v}")
        return v


class BroadcastResult(BaseModel):
    recipients: int  # cuántos usuarios recibieron la notificación in-app
    push_sent: int   # cuántos envíos push efectivos (dispositivos)
    popup_created: bool

from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List
from datetime import datetime
from decimal import Decimal
import re
import unicodedata


VALID_KINDS = {"video", "texto", "pdf", "link"}
VALID_STATUS = {"borrador", "publicada", "archivada"}
VALID_ACCESS_MODES = {"grant", "abierta"}


def slugify(value: str) -> str:
    """Título → slug para la URL pública (/capacitaciones/<slug>)."""
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    return cleaned[:160] or "capacitacion"


# ── Ítems de contenido ─────────────────────────────────────────────────

class TrainingItemBase(BaseModel):
    title: str
    kind: str = "video"
    description: Optional[str] = None
    provider: Optional[str] = "youtube"
    # Puede llegar la URL completa: el router extrae el ID antes de guardar.
    video_ref: Optional[str] = None
    body: Optional[str] = None
    file_url: Optional[str] = None
    duration_minutes: Optional[int] = None
    sort_order: int = 0
    is_published: bool = True

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El título no puede estar vacío")
        return v.strip()

    @field_validator("kind")
    @classmethod
    def kind_valid(cls, v: str) -> str:
        v = (v or "video").strip().lower()
        if v not in VALID_KINDS:
            raise ValueError(f"Tipo de contenido inválido. Válidos: {', '.join(sorted(VALID_KINDS))}")
        return v


class TrainingItemCreate(TrainingItemBase):
    pass


class TrainingItemUpdate(BaseModel):
    title: Optional[str] = None
    kind: Optional[str] = None
    description: Optional[str] = None
    provider: Optional[str] = None
    video_ref: Optional[str] = None
    body: Optional[str] = None
    file_url: Optional[str] = None
    duration_minutes: Optional[int] = None
    sort_order: Optional[int] = None
    is_published: Optional[bool] = None

    @field_validator("kind")
    @classmethod
    def kind_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in VALID_KINDS:
            raise ValueError(f"Tipo de contenido inválido. Válidos: {', '.join(sorted(VALID_KINDS))}")
        return v


class TrainingItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    training_id: int
    kind: str
    title: str
    description: Optional[str] = None
    provider: Optional[str] = None
    # Viaja SOLO si quien pregunta tiene acceso. Con locked=True viene en None:
    # es la frontera de seguridad del módulo.
    video_ref: Optional[str] = None
    body: Optional[str] = None
    file_url: Optional[str] = None
    duration_minutes: Optional[int] = None
    sort_order: int = 0
    is_published: bool = True
    locked: bool = False
    # Progreso de quien consulta (None si no aplica)
    last_position_sec: Optional[int] = None
    watched_sec: Optional[int] = None
    completed: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ItemReorder(BaseModel):
    """Nuevo orden de los ítems: lista de ids en el orden deseado."""
    order: List[int]


# ── Capacitaciones ─────────────────────────────────────────────────────

class TrainingBase(BaseModel):
    title: str
    description: Optional[str] = None
    cover_file_guid: Optional[str] = None
    price: Decimal = Decimal("0")
    currency: str = "ARS"
    status: str = "borrador"
    access_mode: str = "grant"
    default_access_days: Optional[int] = None
    category: Optional[str] = None
    available_from: Optional[datetime] = None
    available_until: Optional[datetime] = None
    sort_order: int = 0

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El título no puede estar vacío")
        return v.strip()

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: str) -> str:
        v = (v or "borrador").strip().lower()
        if v not in VALID_STATUS:
            raise ValueError(f"Estado inválido. Válidos: {', '.join(sorted(VALID_STATUS))}")
        return v

    @field_validator("access_mode")
    @classmethod
    def access_mode_valid(cls, v: str) -> str:
        v = (v or "grant").strip().lower()
        if v not in VALID_ACCESS_MODES:
            raise ValueError(f"Modo de acceso inválido. Válidos: {', '.join(sorted(VALID_ACCESS_MODES))}")
        return v

    @field_validator("category")
    @classmethod
    def category_strip(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            return v if v else None
        return None


class TrainingCreate(TrainingBase):
    slug: Optional[str] = None  # si no viene, se genera del título
    created_by_volunteer_id: Optional[int] = None


class TrainingUpdate(BaseModel):
    title: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    cover_file_guid: Optional[str] = None
    price: Optional[Decimal] = None
    currency: Optional[str] = None
    status: Optional[str] = None
    access_mode: Optional[str] = None
    default_access_days: Optional[int] = None
    category: Optional[str] = None
    available_from: Optional[datetime] = None
    available_until: Optional[datetime] = None
    sort_order: Optional[int] = None

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in VALID_STATUS:
            raise ValueError(f"Estado inválido. Válidos: {', '.join(sorted(VALID_STATUS))}")
        return v

    @field_validator("access_mode")
    @classmethod
    def access_mode_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in VALID_ACCESS_MODES:
            raise ValueError(f"Modo de acceso inválido. Válidos: {', '.join(sorted(VALID_ACCESS_MODES))}")
        return v


class TrainingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    description: Optional[str] = None
    cover_file_guid: Optional[str] = None
    price: Decimal = Decimal("0")
    currency: str = "ARS"
    status: str
    access_mode: str
    default_access_days: Optional[int] = None
    category: Optional[str] = None
    available_from: Optional[datetime] = None
    available_until: Optional[datetime] = None
    sort_order: int = 0
    created_by_volunteer_id: Optional[int] = None
    item_count: int = 0
    # Contexto de quien consulta
    has_access: bool = False
    access_expires_at: Optional[datetime] = None
    completed_items: int = 0
    items: List[TrainingItemOut] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Progreso y trazabilidad ────────────────────────────────────────────

class ProgressUpdate(BaseModel):
    """Ping del player. `watched_delta` son los segundos reproducidos desde
    el ping anterior: acumular deltas impide que arrastrar la barra al final
    cuente como visto."""

    user_type: str
    user_id: int
    last_position_sec: int = 0
    watched_delta: int = 0
    completed: bool = False


class ProgressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    training_item_id: int
    last_position_sec: int = 0
    watched_sec: int = 0
    completed_at: Optional[datetime] = None


class ViewCreate(BaseModel):
    user_type: str
    user_id: int
    ip: Optional[str] = None
    user_agent: Optional[str] = None


class SharedAccountAlert(BaseModel):
    """Señal de posible cuenta compartida. SOLO informativa: la decisión la
    toma una persona. Nunca se revoca por esto de forma automática."""

    person_id: int
    person_name: Optional[str] = None
    person_email: Optional[str] = None
    distinct_ips: int
    views: int


class VideoCheckRequest(BaseModel):
    url: str


class VideoCheckResult(BaseModel):
    ok: bool
    video_id: Optional[str] = None
    title: Optional[str] = None
    author_name: Optional[str] = None
    thumbnail_url: Optional[str] = None
    message: Optional[str] = None

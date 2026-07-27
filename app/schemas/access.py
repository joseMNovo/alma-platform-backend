from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal


# ── Habilitaciones ─────────────────────────────────────────────────────

class GrantCreate(BaseModel):
    """Alta de habilitación. Con `payment` cargado, la habilitación y el pago
    entran en la MISMA transacción: o quedan los dos, o no queda ninguno."""

    person_id: int
    module_key: str
    resource_id: int = 0
    expires_at: Optional[datetime] = None
    access_days: Optional[int] = None  # alternativa a expires_at
    notes: Optional[str] = None
    actor_type: str = "admin"
    actor_id: int = 0
    payment: Optional["PaymentCreate"] = None

    @field_validator("module_key")
    @classmethod
    def module_key_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El módulo es obligatorio")
        return v.strip()


class GrantBulkCreate(BaseModel):
    """Habilitación masiva: mismas condiciones para varias personas."""

    person_ids: List[int]
    module_key: str
    resource_id: int = 0
    expires_at: Optional[datetime] = None
    access_days: Optional[int] = None
    notes: Optional[str] = None
    actor_type: str = "admin"
    actor_id: int = 0

    @field_validator("person_ids")
    @classmethod
    def person_ids_not_empty(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("Seleccioná al menos una persona")
        return v


class GrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    person_id: int
    module_key: str
    resource_id: int = 0
    is_active: bool = True
    granted_by_volunteer_id: Optional[int] = None
    granted_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    notes: Optional[str] = None
    # Calculados
    is_live: bool = False           # activa Y no vencida
    person_name: Optional[str] = None
    person_email: Optional[str] = None
    resource_title: Optional[str] = None


class GrantRevoke(BaseModel):
    person_id: int
    module_key: str
    resource_id: int = 0
    notes: Optional[str] = None
    actor_type: str = "admin"
    actor_id: int = 0


# ── Pagos ──────────────────────────────────────────────────────────────

class PaymentCreate(BaseModel):
    person_id: Optional[int] = None       # lo completa el grant si viene anidado
    concept_type: str = "capacitacion"
    concept_id: int = 0
    concept_label: Optional[str] = None
    amount: Decimal
    currency: str = "ARS"
    period_year: Optional[int] = None
    period_month: Optional[int] = None
    method: Optional[str] = None
    reference: Optional[str] = None
    paid_at: Optional[date] = None
    registered_by_volunteer_id: Optional[int] = None
    notes: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: Decimal) -> Decimal:
        if v is None or v <= 0:
            raise ValueError("El monto debe ser mayor a cero")
        return v


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    person_id: int
    concept_type: str
    concept_id: int = 0
    concept_label: Optional[str] = None
    amount: Decimal
    currency: str = "ARS"
    period_year: Optional[int] = None
    period_month: Optional[int] = None
    method: Optional[str] = None
    reference: Optional[str] = None
    paid_at: Optional[date] = None
    registered_by_volunteer_id: Optional[int] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    person_name: Optional[str] = None


# ── Vistas del ABM ─────────────────────────────────────────────────────

class MatrixRow(BaseModel):
    """Una fila de la matriz de accesos: la persona y sus habilitaciones."""

    person_id: int
    name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    has_login: bool = False
    is_volunteer: bool = False
    # resource_id → vigente
    grants: dict = {}
    total_paid: Decimal = Decimal("0")


class AccessAuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    grant_id: Optional[int] = None
    person_id: int
    module_key: str
    resource_id: int = 0
    action: str
    actor_type: str
    actor_id: int = 0
    detail: Optional[dict] = None
    created_at: Optional[datetime] = None
    actor_name: Optional[str] = None


class MyAccess(BaseModel):
    """Lo que el frontend necesita para pintar la UI del usuario logueado.

    Es solo para MOSTRAR: cada endpoint vuelve a verificar el acceso del lado
    del servidor. El cliente nunca es la fuente de verdad.
    """

    person_id: Optional[int] = None
    grants: List[GrantOut] = []


GrantCreate.model_rebuild()

from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional
from decimal import Decimal
from datetime import datetime, date

ESTADOS = {"pendiente", "confirmado", "rechazado"}


class PaymentClaimCreate(BaseModel):
    """Lo que manda quien compró: "ya pagué", con su comprobante."""

    person_id: int
    concept_type: str = "capacitacion"
    concept_id: int = 0
    concept_label: Optional[str] = None
    # Opcional: no tener la captura a mano no puede impedir avisar.
    file_guid: Optional[str] = None
    message: Optional[str] = None

    @field_validator("message", "file_guid", mode="before")
    @classmethod
    def _vacio_es_nulo(cls, v):
        if v is None:
            return None
        v = str(v).strip()
        return v or None


class PaymentClaimResolve(BaseModel):
    """Lo que decide el voluntario que revisó el aviso.

    El monto viaja acá y no se toma del precio de la capacitación: puede haber
    pagado con descuento, en cuotas, o un importe viejo. Quien confirma es
    quien está mirando el comprobante.
    """

    volunteer_id: Optional[int] = None
    amount: Optional[Decimal] = None
    method: Optional[str] = None
    reference: Optional[str] = None
    paid_at: Optional[date] = None
    access_days: Optional[int] = None
    notes: Optional[str] = None


class PaymentClaimOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    person_id: int
    concept_type: str
    concept_id: int
    concept_label: Optional[str] = None
    file_guid: Optional[str] = None
    message: Optional[str] = None
    status: str
    resolved_by_volunteer_id: Optional[int] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    created_at: Optional[datetime] = None
    # Para que la cola se lea sin ir a buscar a quién pertenece cada aviso.
    person_name: Optional[str] = None
    person_email: Optional[str] = None

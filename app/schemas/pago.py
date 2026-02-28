from pydantic import BaseModel, ConfigDict
from typing import Optional, Literal
from datetime import date, datetime


class PagoBase(BaseModel):
    user_id: int
    concept: str
    amount: int
    due_date: date
    payment_method: Optional[Literal["efectivo", "transferencia", "tarjeta"]] = None
    status: Literal["pendiente", "pagado", "vencido"] = "pendiente"
    payment_date: Optional[date] = None


class PagoCreate(PagoBase):
    pass


class PagoUpdate(BaseModel):
    concept: Optional[str] = None
    amount: Optional[int] = None
    due_date: Optional[date] = None
    payment_method: Optional[Literal["efectivo", "transferencia", "tarjeta"]] = None
    status: Optional[Literal["pendiente", "pagado", "vencido"]] = None
    payment_date: Optional[date] = None


class Pago(PagoBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

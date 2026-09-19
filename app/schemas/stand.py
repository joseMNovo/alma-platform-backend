from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List
from decimal import Decimal
from datetime import datetime

MEDIOS_DE_PAGO = {"efectivo", "transferencia"}


# ── Productos ──────────────────────────────────────────────────────────

class StandProductBase(BaseModel):
    name: str
    unit_price: Decimal = Decimal("0")
    initial_stock: int = 0
    is_active: bool = True
    sort_order: int = 0

    @field_validator("name")
    @classmethod
    def _nombre_no_vacio(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El producto necesita un nombre")
        return v.strip()


class StandProductCreate(StandProductBase):
    pass


class StandProductUpdate(BaseModel):
    name: Optional[str] = None
    unit_price: Optional[Decimal] = None
    initial_stock: Optional[int] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class StandProductOut(StandProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # Calculados sobre las ventas no anuladas, no guardados: un contador
    # guardado se desincroniza en cuanto alguien anula una venta.
    sold: int = 0
    stock: int = 0


# ── Ventas ─────────────────────────────────────────────────────────────

class StandSaleItemIn(BaseModel):
    product_id: int
    quantity: int = 1

    @field_validator("quantity")
    @classmethod
    def _cantidad_positiva(cls, v: int) -> int:
        if v < 1:
            raise ValueError("La cantidad tiene que ser al menos 1")
        return v


class StandSaleCreate(BaseModel):
    payment_method: str
    items: List[StandSaleItemIn]
    notes: Optional[str] = None
    sold_by_volunteer_id: Optional[int] = None
    # Opcionales SIEMPRE: en el stand, pedir datos no puede frenar el cobro.
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None

    @field_validator("customer_name", "customer_email", mode="before")
    @classmethod
    def _vacio_es_nulo(cls, v):
        if v is None:
            return None
        v = str(v).strip()
        return v or None

    @field_validator("payment_method")
    @classmethod
    def _medio_valido(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in MEDIOS_DE_PAGO:
            raise ValueError("Medio de pago inválido. Válidos: efectivo, transferencia")
        return v

    @field_validator("items")
    @classmethod
    def _con_items(cls, v):
        if not v:
            raise ValueError("La venta no tiene productos")
        return v


class StandSaleItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    product_name: Optional[str] = None
    quantity: int
    unit_price: Decimal


class StandSaleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payment_method: str
    total: Decimal
    notes: Optional[str] = None
    sold_by_volunteer_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    is_void: bool = False
    created_at: Optional[datetime] = None
    items: List[StandSaleItemOut] = []


# ── Caja ───────────────────────────────────────────────────────────────

class StandSummaryProduct(BaseModel):
    product_id: int
    name: str
    units: int
    revenue: Decimal


class StandSummary(BaseModel):
    total: Decimal
    efectivo: Decimal
    transferencia: Decimal
    sales_count: int
    by_product: List[StandSummaryProduct]

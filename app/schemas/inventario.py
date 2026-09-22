from pydantic import BaseModel, ConfigDict
from typing import Optional
from decimal import Decimal
from datetime import date, datetime


class InventarioBase(BaseModel):
    name: str
    category: Optional[str] = None
    quantity: int = 0
    minimum_stock: int = 1
    # Cuánto VALE el ítem, no a cuánto se vende. El precio de venta vive en la
    # góndola (`stand_products.unit_price`), porque un mismo ítem puede valer
    # una cosa y venderse a otra, y porque la mayoría del inventario no se vende.
    price: Decimal = Decimal("0.00")
    supplier: Optional[str] = None
    assigned_volunteer_id: Optional[int] = None
    entry_date: date


class InventarioCreate(InventarioBase):
    # "Para vender". No es una columna de esta tabla: crear o desactivar la
    # fila de la góndola es lo que lo hace verdad. Se pide acá para que el alta
    # sea un solo formulario y una sola llamada.
    for_sale: bool = False
    sale_price: Optional[Decimal] = None


class InventarioUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[int] = None
    minimum_stock: Optional[int] = None
    price: Optional[Decimal] = None
    supplier: Optional[str] = None
    assigned_volunteer_id: Optional[int] = None
    entry_date: Optional[date] = None
    # None = no lo toques. False = sacalo de la góndola (baja lógica, no se
    # borra: se perdería el orden y el historial de ventas colgaría de la nada).
    for_sale: Optional[bool] = None
    sale_price: Optional[Decimal] = None


class Inventario(InventarioBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Derivados de la góndola. No están en la tabla: los arma el router.
    for_sale: bool = False
    sale_price: Optional[Decimal] = None
    # Unidades vendidas en el puesto, sin contar ventas anuladas.
    sold: int = 0

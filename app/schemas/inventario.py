from pydantic import BaseModel, ConfigDict
from typing import Optional
from decimal import Decimal
from datetime import date, datetime


class InventarioBase(BaseModel):
    name: str
    category: Optional[str] = None
    quantity: int = 0
    minimum_stock: int = 1
    price: Decimal = Decimal("0.00")
    supplier: Optional[str] = None
    assigned_volunteer_id: Optional[int] = None
    entry_date: date


class InventarioCreate(InventarioBase):
    pass


class InventarioUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[int] = None
    minimum_stock: Optional[int] = None
    price: Optional[Decimal] = None
    supplier: Optional[str] = None
    assigned_volunteer_id: Optional[int] = None
    entry_date: Optional[date] = None


class Inventario(InventarioBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

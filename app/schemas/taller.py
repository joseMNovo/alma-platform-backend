from pydantic import BaseModel, ConfigDict
from typing import Optional
import datetime as dt


class TallerBase(BaseModel):
    name: str
    description: Optional[str] = None
    instructor: Optional[str] = None
    date: Optional[dt.date] = None
    schedule: Optional[str] = None
    capacity: int = 0
    cost: int = 0
    enrolled: int = 0
    status: str = "activo"


class TallerCreate(TallerBase):
    pass


class TallerUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    instructor: Optional[str] = None
    date: Optional[dt.date] = None
    schedule: Optional[str] = None
    capacity: Optional[int] = None
    cost: Optional[int] = None
    enrolled: Optional[int] = None
    status: Optional[str] = None


class Taller(TallerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[dt.datetime] = None
    updated_at: Optional[dt.datetime] = None

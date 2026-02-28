from sqlalchemy import Column, Integer, String, Text, TIMESTAMP
from app.database import Base


class Actividad(Base):
    __tablename__ = "actividades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    status = Column(String(20), nullable=False, default="activo")
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

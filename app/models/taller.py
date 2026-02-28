from sqlalchemy import Column, Integer, String, Date, Text, TIMESTAMP
from app.database import Base


class Taller(Base):
    __tablename__ = "talleres"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    instructor = Column(String(100))
    date = Column(Date)
    schedule = Column(String(50))
    capacity = Column(Integer, nullable=False, default=0)
    cost = Column(Integer, nullable=False, default=0)
    enrolled = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="activo")
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

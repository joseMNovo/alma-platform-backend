from sqlalchemy import Column, Integer, String, Text, TIMESTAMP
from app.database import Base


class Grupo(Base):
    __tablename__ = "grupos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    coordinator = Column(String(100))
    day = Column(String(20))
    schedule = Column(String(50))
    participants = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="activo")
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

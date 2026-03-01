from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class Actividad(Base):
    __tablename__ = "actividades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)
    status = Column(String(20), nullable=False, default="activo")
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)
    created_by_volunteer_id = Column(Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True)

    created_by = relationship("Voluntario", foreign_keys=[created_by_volunteer_id], lazy="joined")

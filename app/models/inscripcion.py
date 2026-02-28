from sqlalchemy import Column, Integer, String, Date, TIMESTAMP, ForeignKey
from app.database import Base


class Inscripcion(Base):
    __tablename__ = "inscripciones"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("voluntarios.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(20), nullable=False)
    item_id = Column(Integer, nullable=False)
    enrollment_date = Column(Date, nullable=False)
    status = Column(String(50), nullable=False, default="confirmada")
    created_at = Column(TIMESTAMP)

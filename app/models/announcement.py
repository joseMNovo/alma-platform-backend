from sqlalchemy import Column, Integer, String, Text, Boolean, TIMESTAMP, func
from app.database import Base


class Announcement(Base):
    __tablename__ = "announcements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    # 'all' | 'admin' | 'voluntario' | 'participante'
    audience = Column(String(20), nullable=False, default="all")
    is_active = Column(Boolean, nullable=False, default=True)
    # Ventana de visibilidad opcional
    starts_at = Column(TIMESTAMP, nullable=True)
    ends_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

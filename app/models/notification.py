from sqlalchemy import Column, Integer, String, Text, Boolean, Enum, TIMESTAMP, func
from app.database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Destinatario: misma convención (user_type, user_id) sin FK. admin = voluntario
    user_type = Column(Enum("voluntario", "participante"), nullable=False)
    user_id = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)
    # Qué la generó (para iconografía/agrupación en la campanita)
    kind = Column(
        Enum("announcement", "calendar_reminder", "calendar_new", "system"),
        nullable=False,
        default="system",
    )
    # Deep-link opcional dentro de la app (ej: '/calendarios')
    url = Column(String(255), nullable=True)
    is_read = Column(Boolean, nullable=False, default=False)
    read_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

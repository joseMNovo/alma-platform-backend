from sqlalchemy import Column, Integer, String, Enum, TIMESTAMP, func
from app.database import Base


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 'voluntario' (incluye admins) | 'participante'
    user_type = Column(Enum("voluntario", "participante"), nullable=False)
    user_id = Column(Integer, nullable=False)
    # URL única del servidor push del navegador (clave natural: upsert por endpoint)
    endpoint = Column(String(512), nullable=False, unique=True)
    p256dh = Column(String(255), nullable=False)
    auth = Column(String(255), nullable=False)
    user_agent = Column(String(255), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

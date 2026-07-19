from sqlalchemy import Column, BigInteger, Integer, String, TIMESTAMP, func
from app.database import Base


class ActivityEvent(Base):
    """Evento de uso: login, vista de módulo, alta/edición/baja.

    El usuario puede vivir en dos tablas distintas (voluntarios / participants)
    y los IDs se pisan entre ellas, por eso la identidad es (user_type, user_id)
    y user_id NO lleva foreign key. `role` se guarda por evento porque
    user_type por sí solo no distingue admin de voluntario.
    """

    __tablename__ = "activity_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    event_type = Column(String(20), nullable=False)  # login | view | create | edit | delete
    module = Column(String(50), nullable=True)
    action = Column(String(100), nullable=True)
    # 'voluntario' (incluye admins, id=0 = admin por env vars) | 'participante'
    user_type = Column(String(20), nullable=False)
    user_id = Column(Integer, nullable=False)
    role = Column(String(20), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

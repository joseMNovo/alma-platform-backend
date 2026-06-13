from sqlalchemy import Column, Integer, String, TIMESTAMP, ForeignKey, UniqueConstraint, func
from app.database import Base


class AnnouncementDismissal(Base):
    """Registro de "no volver a mostrar" por usuario.

    El usuario puede vivir en dos tablas distintas (voluntarios / participants)
    y los IDs se pisan entre ellas, por eso la identidad es (user_type, user_id)
    y user_id NO lleva foreign key.
    """

    __tablename__ = "announcement_dismissals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    announcement_id = Column(
        Integer, ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False
    )
    # 'voluntario' (incluye admins) | 'participante'
    user_type = Column(String(20), nullable=False)
    user_id = Column(Integer, nullable=False)
    dismissed_at = Column(TIMESTAMP, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("announcement_id", "user_type", "user_id", name="uq_dismissal"),
    )

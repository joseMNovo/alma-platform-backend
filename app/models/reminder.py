from sqlalchemy import Column, Integer, JSON, TIMESTAMP, ForeignKey, func

from app.database import Base


class ParticipantEventReminder(Base):
    """Qué recordatorios pidió una persona para un evento puntual.

    Distinto de `calendar_instances.reminder_offsets`, que lo configura el
    admin y gobierna a los voluntarios asignados: esto lo decide la persona
    para sí misma.

    Sin fila = sin recordatorios. Anotarse a un taller no debería suscribir a
    nadie a nada que no pidió.
    """

    __tablename__ = "participant_event_reminders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    calendar_instance_id = Column(
        Integer, ForeignKey("calendar_instances.id", ondelete="CASCADE"), nullable=False
    )
    person_id = Column(
        Integer, ForeignKey("participant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    # Mismo formato que reminder_offsets: días de anticipación, p. ej. [7, 1, 0].
    # Se repite a propósito para que el cron no tenga que traducir nada.
    offsets = Column(JSON, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

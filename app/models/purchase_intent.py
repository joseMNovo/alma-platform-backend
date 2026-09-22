from sqlalchemy import Column, Integer, TIMESTAMP, ForeignKey, UniqueConstraint, func
from app.database import Base


class PurchaseIntent(Base):
    """Alguien se registró para comprar una capacitación puntual.

    Se anota en el alta, cuando el registro viene del wizard de compra. Sin
    esto no hay forma de distinguir a quien vino a comprar de quien vino a
    mirar, y el recordatorio de pago terminaría escribiéndole a cualquiera.

    `reminded_at` hace de bitácora: NULL es "todavía no se le avisó". Mismo
    criterio que `reminder_sent_log` del calendario — el cron puede correr dos
    veces o saltear un día sin que a nadie le llegue el mismo mail dos veces.
    """

    __tablename__ = "purchase_intents"
    __table_args__ = (
        UniqueConstraint("person_id", "training_id", name="uq_pi_persona_capacitacion"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    person_id = Column(
        Integer, ForeignKey("participant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    training_id = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())
    reminded_at = Column(TIMESTAMP, nullable=True)

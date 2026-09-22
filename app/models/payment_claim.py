from sqlalchemy import Column, Integer, String, TIMESTAMP, ForeignKey, func
from app.database import Base


class PaymentClaim(Base):
    """Aviso de "ya pagué" que manda quien compró, con su comprobante.

    Separada de `person_payments` a propósito: aquella significa que la plata
    entró y la completa un voluntario que lo verificó. Esto es apenas un
    reclamo sin verificar. Si compartieran tabla, un aviso falso inflaría la
    recaudación.

    Al confirmarse se crea el `PersonPayment` y la habilitación; el aviso
    queda como el rastro de quién pidió qué y quién lo revisó.
    """

    __tablename__ = "payment_claims"

    id = Column(Integer, primary_key=True, autoincrement=True)
    person_id = Column(
        Integer, ForeignKey("participant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    concept_type = Column(String(30), nullable=False, default="capacitacion")
    concept_id = Column(Integer, nullable=False, default=0)
    concept_label = Column(String(150), nullable=True)
    # Opcional: no tener la captura a mano no puede impedir avisar.
    file_guid = Column(String(36), nullable=True)
    message = Column(String(500), nullable=True)
    status = Column(String(20), nullable=False, default="pendiente")
    resolved_by_volunteer_id = Column(Integer, nullable=True)
    resolved_at = Column(TIMESTAMP, nullable=True)
    resolution_notes = Column(String(500), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

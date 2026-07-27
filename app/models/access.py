from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Numeric, Boolean,
    Date, DateTime, TIMESTAMP, SmallInteger, ForeignKey, JSON, func,
)
from app.database import Base


class PersonAccessGrant(Base):
    """Habilitación de una persona a un módulo o a un ítem puntual.

    Es el "switchboard": lo que un admin tildea. Se cuelga de la PERSONA
    (participant_profiles), no del login, así sirve igual para participantes,
    voluntarios y hasta para alguien que todavía no se registró.

    resource_id = 0 → el módulo entero (ve la pestaña)
    resource_id > 0 → un ítem de ese módulo (ej: la capacitación 7)

    NOT NULL DEFAULT 0 en vez de NULL a propósito: MySQL admite duplicados
    cuando hay NULL en un índice único, y acá el UNIQUE tiene que funcionar.
    """

    __tablename__ = "person_access_grants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    person_id = Column(
        Integer, ForeignKey("participant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    module_key = Column(String(60), nullable=False)
    resource_id = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    granted_by_volunteer_id = Column(
        Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True
    )
    granted_at = Column(TIMESTAMP, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)  # NULL = sin vencimiento
    revoked_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)


class PersonPayment(Base):
    """Ingreso registrado a nombre de una persona.

    Genérica a propósito: capacitaciones hoy, cuota de socio y donaciones
    mañana. Separada de la habilitación porque acceso ≠ pago (existen becas,
    y revocar un acceso no borra que la plata entró).
    """

    __tablename__ = "person_payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    person_id = Column(
        Integer, ForeignKey("participant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    concept_type = Column(String(30), nullable=False)
    concept_id = Column(Integer, nullable=False, default=0)
    # Nombre congelado al momento del cobro: un recibo viejo no se actualiza
    # si después renombran la capacitación.
    concept_label = Column(String(150), nullable=True)
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="ARS")
    period_year = Column(SmallInteger, nullable=True)
    period_month = Column(SmallInteger, nullable=True)
    method = Column(String(30), nullable=True)
    reference = Column(String(100), nullable=True)
    paid_at = Column(Date, nullable=True)
    registered_by_volunteer_id = Column(
        Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True
    )
    notes = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())


class AccessAudit(Base):
    """Historia de habilitaciones y pagos. Append-only: no se edita ni se borra.

    `person_access_grants` guarda solo el último estado; esto guarda el camino
    (habilitar → revocar → rehabilitar deja tres renglones).
    """

    __tablename__ = "access_audit"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    grant_id = Column(Integer, nullable=True)
    person_id = Column(Integer, nullable=False)
    module_key = Column(String(60), nullable=False)
    resource_id = Column(Integer, nullable=False, default=0)
    action = Column(String(20), nullable=False)  # grant | revoke | extend | payment
    actor_type = Column(String(20), nullable=False)
    actor_id = Column(Integer, nullable=False, default=0)
    detail = Column(JSON, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

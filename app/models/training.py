from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Numeric, Boolean,
    DateTime, TIMESTAMP, ForeignKey, func,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import relationship as orm_relationship

from app.database import Base


class Training(Base):
    """Una capacitación en video. En la UI es una sub-pestaña del módulo."""

    __tablename__ = "trainings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(150), nullable=False)
    slug = Column(String(160), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    # Portada: guid en la tabla `files`. Sin FK porque el archivo es opcional
    # y su ciclo de vida lo maneja el ABM de archivos.
    cover_file_guid = Column(String(36), nullable=True)
    price = Column(Numeric(10, 2), nullable=False, default=0)
    currency = Column(String(3), nullable=False, default="ARS")
    # Link de pago externo (MercadoPago), generado a mano en el panel de MP.
    # La plataforma NO se entera del pago: manda afuera y la habilitación la
    # sigue dando un admin. NULL = no se muestra botón de compra.
    payment_url = Column(String(500), nullable=True)
    status = Column(String(20), nullable=False, default="borrador")
    # 'grant' = requiere habilitación | 'abierta' = contenido gratuito para
    # cualquier autenticado, sin habilitar persona por persona.
    access_mode = Column(String(20), nullable=False, default="grant")
    default_access_days = Column(Integer, nullable=True)
    # Carga horaria tal como se imprime en el certificado ("8 horas"). Texto y
    # no número: la escribe quien dicta el curso y es lo que sale impreso.
    certificate_hours = Column(String(40), nullable=True)
    category = Column(String(60), nullable=True)
    available_from = Column(DateTime, nullable=True)
    available_until = Column(DateTime, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    # Plantilla de certificado propia. NULL = la marcada como default.
    certificate_template_id = Column(
        Integer, ForeignKey("certificate_templates.id", ondelete="SET NULL"), nullable=True
    )
    created_by_volunteer_id = Column(
        Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    items = orm_relationship(
        "TrainingItem",
        back_populates="training",
        cascade="all, delete-orphan",
        order_by="TrainingItem.sort_order",
        lazy="selectin",
    )


class TrainingItem(Base):
    """Una pieza de contenido: video, texto, pdf o link.

    Polimórfica a propósito (`kind`): así una capacitación puede mezclar una
    intro escrita, tres videos y un adjunto manteniendo UN solo orden.
    """

    __tablename__ = "training_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    training_id = Column(Integer, ForeignKey("trainings.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(20), nullable=False, default="video")
    title = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    provider = Column(String(20), nullable=True)
    # SOLO el ID del video, nunca la URL: cambiar de proveedor es cambiar
    # provider + ref, sin migrar nada.
    video_ref = Column(String(120), nullable=True)
    body = Column(MEDIUMTEXT, nullable=True)
    file_url = Column(String(500), nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_published = Column(Boolean, nullable=False, default=True)
    # La introducción abierta: este ítem —y solo este— se ve sin haber pagado
    # y sin tener cuenta, desde la landing pública. Es el anzuelo comercial.
    # La regla se aplica en _serialize_item (routers/capacitaciones.py), que es
    # la única función que decide qué contenido viaja al browser.
    is_free_preview = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    training = orm_relationship("Training", back_populates="items")


class TrainingItemProgress(Base):
    """Avance de una persona en una pieza de contenido."""

    __tablename__ = "training_item_progress"

    id = Column(Integer, primary_key=True, autoincrement=True)
    person_id = Column(
        Integer, ForeignKey("participant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    training_item_id = Column(
        Integer, ForeignKey("training_items.id", ondelete="CASCADE"), nullable=False
    )
    # Dónde quedó: el player arranca acá ("seguir viendo").
    last_position_sec = Column(Integer, nullable=False, default=0)
    # Segundos realmente reproducidos. Se guarda aparte de la posición porque
    # arrastrar la barra al final no puede contar como "visto".
    watched_sec = Column(Integer, nullable=False, default=0)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class TrainingItemView(Base):
    """Log de reproducciones: trazabilidad forense y detección de cuentas compartidas."""

    __tablename__ = "training_item_views"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    training_item_id = Column(
        Integer, ForeignKey("training_items.id", ondelete="CASCADE"), nullable=False
    )
    person_id = Column(Integer, nullable=True)
    user_type = Column(String(20), nullable=False)
    user_id = Column(Integer, nullable=False)
    ip = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)
    viewed_at = Column(TIMESTAMP, server_default=func.now())

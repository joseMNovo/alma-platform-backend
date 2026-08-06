from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, TIMESTAMP, ForeignKey, func,
)

from app.database import Base


class CertificateTemplate(Base):
    """Redacción editable del certificado.

    Reutilizable entre capacitaciones a propósito: el texto legal se corrige
    en un solo lugar. Una capacitación con `certificate_template_id` en NULL
    usa la que tenga `is_default`.

    IMPORTANTE para cuando se implemente la emisión: el certificado emitido
    tiene que guardar su propia copia del texto ya resuelto. Si apuntara acá,
    editar la plantilla reescribiría certificados viejos.
    """

    __tablename__ = "certificate_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    is_default = Column(Boolean, nullable=False, default=False)
    heading = Column(String(150), nullable=False, default="Certificado")
    # Cuerpo con marcadores: {{nombre}} {{dni}} {{capacitacion}} {{fecha}}
    # {{horas}} {{codigo}}. Se reemplazan al generar el PDF.
    body = Column(Text, nullable=True)
    legal_note = Column(Text, nullable=True)
    signature_name = Column(String(120), nullable=True)
    signature_role = Column(String(120), nullable=True)
    # Firma y logo: guid en `files`. Sin FK, igual que la portada de una
    # capacitación — el archivo es opcional y su ciclo de vida lo maneja el
    # ABM de archivos.
    signature_file_guid = Column(String(36), nullable=True)
    logo_file_guid = Column(String(36), nullable=True)
    created_by_volunteer_id = Column(
        Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class Certificate(Base):
    """Un certificado ENTREGADO.

    Todo lo imprimible está CONGELADO: el texto se guarda ya resuelto, sin
    marcadores. Corregir la plantilla no puede reescribir un certificado que
    alguien tiene guardado hace seis meses — un certificado que cambia solo
    no certifica nada.

    El PDF no se guarda como archivo: se regenera desde estas columnas. Un
    archivo suelto es una cosa más que respaldar y que se desincroniza.
    """

    __tablename__ = "certificates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Código público de verificación (ALMA-XXXX-XXXX).
    code = Column(String(20), nullable=False, unique=True)
    person_id = Column(
        Integer, ForeignKey("participant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL: si se borra la capacitación, el certificado sobrevive — ya se
    # entregó, y su título quedó congelado abajo.
    training_id = Column(
        Integer, ForeignKey("trainings.id", ondelete="SET NULL"), nullable=True
    )
    survey_attempt_id = Column(
        Integer, ForeignKey("survey_attempts.id", ondelete="SET NULL"), nullable=True
    )

    # ── Congelado al emitir ────────────────────────────────────────────
    holder_name = Column(String(200), nullable=False)
    holder_dni = Column(String(15), nullable=True)
    training_title = Column(String(150), nullable=True)
    hours = Column(String(40), nullable=True)
    heading = Column(String(150), nullable=True)
    body_text = Column(Text, nullable=True)
    legal_note = Column(Text, nullable=True)
    signature_name = Column(String(120), nullable=True)
    signature_role = Column(String(120), nullable=True)

    issued_at = Column(DateTime, nullable=False)
    issued_by_volunteer_id = Column(
        Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True
    )
    sent_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String(255), nullable=True)

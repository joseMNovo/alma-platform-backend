from sqlalchemy import Column, Integer, String, Date, Text, Boolean, TIMESTAMP, DateTime, ForeignKey, func
from app.database import Base


class Participant(Base):
    __tablename__ = "participants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(150), nullable=False, unique=True)
    pin_hash = Column(String(255))
    is_active = Column(Boolean, nullable=False, default=True)
    email_verified = Column(Boolean, nullable=False, default=False)
    email_verified_at = Column(DateTime, nullable=True)
    # server_default para que la fecha la ponga MySQL. Sin esto SQLAlchemy
    # mandaba NULL explícito y created_at quedaba vacío en cada alta.
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class ParticipantProfile(Base):
    __tablename__ = "participant_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Nullable: una persona puede existir en la base de datos sin cuenta de login
    # (p. ej. importada del Excel). El login se adjunta luego, vinculado por email.
    participant_id = Column(Integer, ForeignKey("participants.id", ondelete="CASCADE"), nullable=True, unique=True)
    # Link a la ficha operativa en `voluntarios` (rol voluntario de la persona).
    # NULL = la persona no tiene ficha de voluntario. Gemelo de participant_id (login).
    volunteer_id = Column(Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True, unique=True)
    name = Column(String(100))
    last_name = Column(String(100))
    # Identidad de la persona: dos filas con el mismo email serían dos registros
    # de alguien que en realidad es uno solo. El índice ya existe en MySQL
    # (uq_participant_profiles_email, ver sql/personas.sql), pero faltaba
    # declararlo acá: sin esto SQLAlchemy no lo conoce y el 409 por duplicado del
    # router quedaba dependiendo de que la base lo atajara sola.
    # MySQL y SQLite admiten varios NULL, así que se puede cargar gente sin email.
    email = Column(String(255), unique=True)
    cuit = Column(String(13))
    # Documento. Se imprime en el certificado de finalización.
    dni = Column(String(15))
    is_member = Column(Boolean, nullable=False, default=False)  # socio/a de ALMA
    is_volunteer = Column(Boolean, nullable=False, default=False)  # voluntario/a de ALMA (flag descriptivo)
    phone = Column(String(50))
    birth_date = Column(Date)
    city = Column(String(100))
    province = Column(String(100))
    postal_code = Column(String(10))
    address = Column(String(200))          # calle + número
    floor = Column(String(10))             # piso
    apartment = Column(String(20))         # depto
    emergency_contact_name = Column(String(100))
    emergency_contact_phone = Column(String(50))
    notes = Column(Text)
    accepts_notifications = Column(Boolean, nullable=False, default=False)
    accepts_whatsapp = Column(Boolean, nullable=False, default=False)
    source = Column(String(20))            # 'excel' | 'registro' | 'manual'
    invited_at = Column(DateTime)
    # server_default: si no, created_at quedaba NULL en cada persona nueva y
    # rompía el "embudo de cobro" (personas registradas ordenadas por fecha).
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class ParticipantProgramEnrollment(Base):
    __tablename__ = "participant_program_enrollments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    participant_id = Column(Integer, ForeignKey("participants.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(20), nullable=False)
    item_id = Column(Integer, nullable=False)
    # server_default: SQLAlchemy OMITE la columna en el INSERT y deja que MySQL
    # aplique el DEFAULT CURRENT_TIMESTAMP. Sin esto mandaba enrolled_at=NULL
    # explícito y pisaba el default → "cannot be null". Mismo patrón que
    # calendar_assignments.created_at.
    enrolled_at = Column(TIMESTAMP, server_default=func.now())

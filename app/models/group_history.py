from sqlalchemy import Column, Integer, String, Text, Date, TIMESTAMP, ForeignKey, func
from sqlalchemy.orm import relationship as orm_relationship
from app.database import Base


class GroupHistory(Base):
    """Historial / minuta de un encuentro cerrado de un grupo de apoyo."""

    __tablename__ = "group_histories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Grupo al que pertenece. SET NULL: el historial sobrevive si se borra el grupo.
    group_id = Column(Integer, ForeignKey("grupos.id", ondelete="SET NULL"), nullable=True)
    group_name = Column(String(255), nullable=True)  # snapshot del nombre del grupo
    title = Column(String(255), nullable=True)
    session_date = Column(Date, nullable=True)
    coordinator_volunteer_id = Column(Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True)
    summary = Column(Text, nullable=True)  # relato general / minuta (buscable)
    # Nullable: el admin por env (id=0) no tiene fila en voluntarios → se guarda NULL.
    created_by_volunteer_id = Column(Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    group = orm_relationship("Grupo", foreign_keys=[group_id], lazy="joined")
    coordinator = orm_relationship("Voluntario", foreign_keys=[coordinator_volunteer_id], lazy="joined")
    created_by = orm_relationship("Voluntario", foreign_keys=[created_by_volunteer_id], lazy="joined")
    attendees = orm_relationship(
        "GroupHistoryAttendee",
        back_populates="history",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class GroupHistoryAttendee(Base):
    """Asistente registrado en un historial de grupo. No requiere existir en la base de personas."""

    __tablename__ = "group_history_attendees"

    id = Column(Integer, primary_key=True, autoincrement=True)
    history_id = Column(Integer, ForeignKey("group_histories.id", ondelete="CASCADE"), nullable=False)
    # Vínculo OPCIONAL a una persona del registro maestro. NULL = asistente suelto.
    person_profile_id = Column(Integer, ForeignKey("participant_profiles.id", ondelete="SET NULL"), nullable=True)
    person_name = Column(String(255), nullable=False)
    person_age = Column(Integer, nullable=True)
    patient_name = Column(String(255), nullable=True)   # pariente enfermo
    patient_age = Column(Integer, nullable=True)
    relationship = Column(String(150), nullable=True)   # vínculo (hijo/a, hermano/a, etc.)
    problematica = Column(Text, nullable=True)          # qué llevó a la persona al grupo
    notes = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

    history = orm_relationship("GroupHistory", back_populates="attendees")

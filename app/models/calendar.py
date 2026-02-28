from sqlalchemy import Column, Integer, String, Date, Time, Text, TIMESTAMP, ForeignKey
from app.database import Base


class CalendarInstance(Base):
    __tablename__ = "calendar_instances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(20), nullable=False)
    source_id = Column(Integer)
    date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    notes = Column(Text)
    status = Column(String(20), nullable=False, default="programado")
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)


class CalendarAssignment(Base):
    __tablename__ = "calendar_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    instance_id = Column(Integer, ForeignKey("calendar_instances.id", ondelete="CASCADE"), nullable=False)
    volunteer_id = Column(Integer, ForeignKey("voluntarios.id", ondelete="RESTRICT"), nullable=False)
    role = Column(String(20), nullable=False)
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)


class CalendarEventParticipant(Base):
    __tablename__ = "calendar_event_participants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("calendar_instances.id", ondelete="CASCADE"), nullable=False)
    participant_id = Column(Integer, ForeignKey("participants.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(20), nullable=False, default="inscripto")
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

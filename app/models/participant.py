from sqlalchemy import Column, Integer, String, Date, Text, Boolean, TIMESTAMP, ForeignKey
from app.database import Base


class Participant(Base):
    __tablename__ = "participants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(150), nullable=False, unique=True)
    pin_hash = Column(String(255))
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)


class ParticipantProfile(Base):
    __tablename__ = "participant_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    participant_id = Column(Integer, ForeignKey("participants.id", ondelete="CASCADE"), nullable=False, unique=True)
    name = Column(String(100))
    last_name = Column(String(100))
    phone = Column(String(50))
    birth_date = Column(Date)
    city = Column(String(100))
    province = Column(String(100))
    address = Column(String(200))
    emergency_contact_name = Column(String(100))
    emergency_contact_phone = Column(String(50))
    notes = Column(Text)
    accepts_notifications = Column(Boolean, nullable=False, default=False)
    accepts_whatsapp = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)


class ParticipantProgramEnrollment(Base):
    __tablename__ = "participant_program_enrollments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    participant_id = Column(Integer, ForeignKey("participants.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(20), nullable=False)
    item_id = Column(Integer, nullable=False)
    enrolled_at = Column(TIMESTAMP)

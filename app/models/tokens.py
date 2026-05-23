from sqlalchemy import Column, Integer, String, DateTime, Enum, TIMESTAMP, ForeignKey
from app.database import Base


class VolunteerVerificationToken(Base):
    __tablename__ = "volunteer_verification_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    volunteer_id = Column(Integer, ForeignKey("voluntarios.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(TIMESTAMP)


class ParticipantVerificationToken(Base):
    __tablename__ = "participant_verification_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    participant_id = Column(Integer, ForeignKey("participants.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(TIMESTAMP)


class PinResetToken(Base):
    __tablename__ = "pin_reset_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_type = Column(Enum("volunteer", "participant"), nullable=False)
    user_id = Column(Integer, nullable=False)
    token_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(TIMESTAMP)

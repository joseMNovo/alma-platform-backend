import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from app.models.tokens import VolunteerVerificationToken, ParticipantVerificationToken, PinResetToken
from config import settings


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=settings.TOKEN_EXPIRY_HOURS)


def generate_raw() -> str:
    return secrets.token_urlsafe(32)


# ── Volunteer verification ────────────────────────────────────────────

def create_volunteer_verification_token(db: Session, volunteer_id: int) -> str:
    raw = generate_raw()
    db.add(VolunteerVerificationToken(
        volunteer_id=volunteer_id,
        token_hash=_hash(raw),
        expires_at=_expiry(),
    ))
    db.commit()
    return raw


def verify_volunteer_token(db: Session, raw: str) -> VolunteerVerificationToken | None:
    return db.query(VolunteerVerificationToken).filter(
        VolunteerVerificationToken.token_hash == _hash(raw),
        VolunteerVerificationToken.used_at.is_(None),
        VolunteerVerificationToken.expires_at > datetime.now(timezone.utc),
    ).first()


# ── Participant verification ──────────────────────────────────────────

def create_participant_verification_token(
    db: Session, participant_id: int, minutes: int | None = None
) -> str:
    """Token de verificación de email. `minutes` permite una ventana más corta
    que el default general (el registro usa 30 minutos)."""
    raw = generate_raw()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=minutes) if minutes else _expiry()
    )
    db.add(ParticipantVerificationToken(
        participant_id=participant_id,
        token_hash=_hash(raw),
        expires_at=expires_at,
    ))
    db.commit()
    return raw


def invalidate_participant_tokens(db: Session, participant_id: int) -> None:
    """Marca como usados los tokens previos de esa persona.

    Se llama antes de emitir uno nuevo: si alguien pide "reenviar", el link
    viejo deja de servir. Evita tener varios links vivos a la vez.
    """
    db.query(ParticipantVerificationToken).filter(
        ParticipantVerificationToken.participant_id == participant_id,
        ParticipantVerificationToken.used_at.is_(None),
    ).update({"used_at": datetime.now(timezone.utc)}, synchronize_session=False)
    db.commit()


def verify_participant_token(db: Session, raw: str) -> ParticipantVerificationToken | None:
    return db.query(ParticipantVerificationToken).filter(
        ParticipantVerificationToken.token_hash == _hash(raw),
        ParticipantVerificationToken.used_at.is_(None),
        ParticipantVerificationToken.expires_at > datetime.now(timezone.utc),
    ).first()


# ── PIN reset (volunteers + participants) ─────────────────────────────

def create_pin_reset_token(db: Session, user_type: str, user_id: int) -> str:
    raw = generate_raw()
    db.add(PinResetToken(
        user_type=user_type,
        user_id=user_id,
        token_hash=_hash(raw),
        expires_at=_expiry(),
    ))
    db.commit()
    return raw


def verify_pin_reset_token(db: Session, raw: str, user_type: str) -> PinResetToken | None:
    return db.query(PinResetToken).filter(
        PinResetToken.token_hash == _hash(raw),
        PinResetToken.user_type == user_type,
        PinResetToken.used_at.is_(None),
        PinResetToken.expires_at > datetime.now(timezone.utc),
    ).first()

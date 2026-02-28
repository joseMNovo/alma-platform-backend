from sqlalchemy import Column, Integer, String, DateTime, Boolean, TIMESTAMP, ForeignKey
from app.database import Base


class AuthUser(Base):
    __tablename__ = "auth_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    volunteer_id = Column(Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True)
    email = Column(String(150), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    email_verified = Column(Boolean, nullable=False, default=False)
    is_volunteer = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime)
    last_login_ip = Column(String(45))
    last_login_user_agent = Column(String(255))
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    auth_user_id = Column(Integer, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime)
    created_at = Column(TIMESTAMP)


class AuthLoginEvent(Base):
    __tablename__ = "auth_login_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    auth_user_id = Column(Integer, ForeignKey("auth_users.id", ondelete="SET NULL"), nullable=True)
    email = Column(String(150), nullable=False)
    success = Column(Boolean, nullable=False, default=False)
    failure_reason = Column(String(100))
    ip_address = Column(String(45))
    user_agent = Column(String(255))
    created_at = Column(TIMESTAMP)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    auth_user_id = Column(Integer, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    session_token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime)
    ip_address = Column(String(45))
    user_agent = Column(String(255))
    created_at = Column(TIMESTAMP)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    auth_user_id = Column(Integer, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime)
    created_at = Column(TIMESTAMP)

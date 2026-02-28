from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime


# ── Auth Users ───────────────────────────────────────────────────────

class AuthUserBase(BaseModel):
    volunteer_id: Optional[int] = None
    email: str
    email_verified: bool = False
    is_volunteer: bool = False
    is_active: bool = True


class AuthUserCreate(AuthUserBase):
    password_hash: str


class AuthUserUpdate(BaseModel):
    volunteer_id: Optional[int] = None
    email: Optional[str] = None
    password_hash: Optional[str] = None
    email_verified: Optional[bool] = None
    is_volunteer: Optional[bool] = None
    is_active: Optional[bool] = None
    last_login_at: Optional[datetime] = None
    last_login_ip: Optional[str] = None
    last_login_user_agent: Optional[str] = None


class AuthUser(AuthUserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    password_hash: str  # API interna — requerido para verificación en alma-platform
    last_login_at: Optional[datetime] = None
    last_login_ip: Optional[str] = None
    last_login_user_agent: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Email Verification Tokens ─────────────────────────────────────────

class EmailVerificationTokenCreate(BaseModel):
    auth_user_id: int
    token_hash: str
    expires_at: datetime


class EmailVerificationToken(EmailVerificationTokenCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


# ── Auth Login Events ─────────────────────────────────────────────────

class AuthLoginEventCreate(BaseModel):
    auth_user_id: Optional[int] = None
    email: str
    success: bool = False
    failure_reason: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


class AuthLoginEvent(AuthLoginEventCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: Optional[datetime] = None


# ── Auth Sessions ─────────────────────────────────────────────────────

class AuthSessionCreate(BaseModel):
    auth_user_id: int
    session_token_hash: str
    expires_at: datetime
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


class AuthSessionUpdate(BaseModel):
    revoked_at: Optional[datetime] = None


class AuthSession(AuthSessionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    revoked_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


# ── Password Reset Tokens ─────────────────────────────────────────────

class PasswordResetTokenCreate(BaseModel):
    auth_user_id: int
    token_hash: str
    expires_at: datetime


class PasswordResetToken(PasswordResetTokenCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

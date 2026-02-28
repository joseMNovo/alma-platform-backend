from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.auth import (
    AuthUser as AuthUserModel,
    EmailVerificationToken as EVTModel,
    AuthLoginEvent as ALEModel,
    AuthSession as AuthSessionModel,
    PasswordResetToken as PRTModel,
)
from app.schemas.auth import (
    AuthUser, AuthUserCreate, AuthUserUpdate,
    EmailVerificationToken, EmailVerificationTokenCreate,
    AuthLoginEvent, AuthLoginEventCreate,
    AuthSession, AuthSessionCreate, AuthSessionUpdate,
    PasswordResetToken, PasswordResetTokenCreate,
)

router = APIRouter()


# ── Auth Users ────────────────────────────────────────────────────────

@router.get("/users", response_model=List[AuthUser])
def list_auth_users(
    skip: int = 0,
    limit: int = 100,
    is_active: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(AuthUserModel)
    if is_active is not None:
        q = q.filter(AuthUserModel.is_active == is_active)
    return q.offset(skip).limit(limit).all()


@router.get("/users/{id}", response_model=AuthUser)
def get_auth_user(id: int, db: Session = Depends(get_db)):
    u = db.query(AuthUserModel).filter(AuthUserModel.id == id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return u


@router.get("/users/by-email/{email}", response_model=AuthUser)
def get_auth_user_by_email(email: str, db: Session = Depends(get_db)):
    u = db.query(AuthUserModel).filter(AuthUserModel.email == email).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return u


@router.post("/users", response_model=AuthUser, status_code=201)
def create_auth_user(data: AuthUserCreate, db: Session = Depends(get_db)):
    u = AuthUserModel(**data.model_dump())
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@router.put("/users/{id}", response_model=AuthUser)
def update_auth_user(id: int, data: AuthUserUpdate, db: Session = Depends(get_db)):
    u = db.query(AuthUserModel).filter(AuthUserModel.id == id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(u, key, value)
    db.commit()
    db.refresh(u)
    return u


@router.delete("/users/{id}", status_code=204)
def delete_auth_user(id: int, db: Session = Depends(get_db)):
    u = db.query(AuthUserModel).filter(AuthUserModel.id == id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    db.delete(u)
    db.commit()


# ── Email Verification Tokens ─────────────────────────────────────────

@router.post("/verification-tokens", response_model=EmailVerificationToken, status_code=201)
def create_verification_token(data: EmailVerificationTokenCreate, db: Session = Depends(get_db)):
    t = EVTModel(**data.model_dump())
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.get("/verification-tokens/by-hash/{token_hash}", response_model=EmailVerificationToken)
def get_verification_token(token_hash: str, db: Session = Depends(get_db)):
    t = db.query(EVTModel).filter(EVTModel.token_hash == token_hash).first()
    if not t:
        raise HTTPException(status_code=404, detail="Token no encontrado")
    return t


@router.put("/verification-tokens/{id}", response_model=EmailVerificationToken)
def mark_verification_token_used(id: int, db: Session = Depends(get_db)):
    from datetime import datetime
    t = db.query(EVTModel).filter(EVTModel.id == id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Token no encontrado")
    t.used_at = datetime.utcnow()
    db.commit()
    db.refresh(t)
    return t


# ── Auth Login Events ─────────────────────────────────────────────────

@router.get("/login-events", response_model=List[AuthLoginEvent])
def list_login_events(
    skip: int = 0,
    limit: int = 100,
    auth_user_id: Optional[int] = Query(None),
    success: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(ALEModel)
    if auth_user_id is not None:
        q = q.filter(ALEModel.auth_user_id == auth_user_id)
    if success is not None:
        q = q.filter(ALEModel.success == success)
    return q.order_by(ALEModel.created_at.desc()).offset(skip).limit(limit).all()


@router.post("/login-events", response_model=AuthLoginEvent, status_code=201)
def create_login_event(data: AuthLoginEventCreate, db: Session = Depends(get_db)):
    e = ALEModel(**data.model_dump())
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


# ── Auth Sessions ─────────────────────────────────────────────────────

@router.get("/sessions", response_model=List[AuthSession])
def list_sessions(
    skip: int = 0,
    limit: int = 100,
    auth_user_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(AuthSessionModel)
    if auth_user_id is not None:
        q = q.filter(AuthSessionModel.auth_user_id == auth_user_id)
    return q.offset(skip).limit(limit).all()


@router.post("/sessions", response_model=AuthSession, status_code=201)
def create_session(data: AuthSessionCreate, db: Session = Depends(get_db)):
    s = AuthSessionModel(**data.model_dump())
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.get("/sessions/by-hash/{token_hash}", response_model=AuthSession)
def get_session_by_hash(token_hash: str, db: Session = Depends(get_db)):
    s = db.query(AuthSessionModel).filter(AuthSessionModel.session_token_hash == token_hash).first()
    if not s:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    return s


@router.put("/sessions/{id}", response_model=AuthSession)
def update_session(id: int, data: AuthSessionUpdate, db: Session = Depends(get_db)):
    s = db.query(AuthSessionModel).filter(AuthSessionModel.id == id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(s, key, value)
    db.commit()
    db.refresh(s)
    return s


@router.put("/sessions/revoke-by-hash/{token_hash}", response_model=AuthSession)
def revoke_session_by_hash(token_hash: str, db: Session = Depends(get_db)):
    from datetime import datetime
    s = db.query(AuthSessionModel).filter(AuthSessionModel.session_token_hash == token_hash).first()
    if not s:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    s.revoked_at = datetime.utcnow()
    db.commit()
    db.refresh(s)
    return s


# ── Password Reset Tokens ─────────────────────────────────────────────

@router.post("/reset-tokens", response_model=PasswordResetToken, status_code=201)
def create_reset_token(data: PasswordResetTokenCreate, db: Session = Depends(get_db)):
    t = PRTModel(**data.model_dump())
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.get("/reset-tokens/by-hash/{token_hash}", response_model=PasswordResetToken)
def get_reset_token(token_hash: str, db: Session = Depends(get_db)):
    t = db.query(PRTModel).filter(PRTModel.token_hash == token_hash).first()
    if not t:
        raise HTTPException(status_code=404, detail="Token no encontrado")
    return t


@router.delete("/verification-tokens/expired/{auth_user_id}", status_code=204)
def delete_expired_verification_tokens(auth_user_id: int, db: Session = Depends(get_db)):
    from datetime import datetime
    db.query(EVTModel).filter(
        EVTModel.auth_user_id == auth_user_id,
        (EVTModel.expires_at < datetime.utcnow()) | (EVTModel.used_at.isnot(None))
    ).delete(synchronize_session=False)
    db.commit()


@router.put("/reset-tokens/{id}", response_model=PasswordResetToken)
def mark_reset_token_used(id: int, db: Session = Depends(get_db)):
    from datetime import datetime
    t = db.query(PRTModel).filter(PRTModel.id == id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Token no encontrado")
    t.used_at = datetime.utcnow()
    db.commit()
    db.refresh(t)
    return t

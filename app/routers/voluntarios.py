from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date, datetime, timezone

from app.database import get_db
from app.models.voluntario import Voluntario as VoluntarioModel
from app.schemas.voluntario import Voluntario, VoluntarioCreate, VoluntarioUpdate, VoluntarioAuth, VoluntarioRegister
from app.schemas.tokens import VerifyEmailRequest
from app.schemas.email_log import SendEmailRequest
from app.services import token_service, email_service
from config import settings

router = APIRouter()


@router.get("/auth/{email}", response_model=VoluntarioAuth)
def get_voluntario_auth(email: str, db: Session = Depends(get_db)):
    """Endpoint interno para autenticación — devuelve pin_hash."""
    v = db.query(VoluntarioModel).filter(VoluntarioModel.email == email).first()
    if not v:
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    return v


@router.get("/by-email/{email}", response_model=Voluntario)
def get_voluntario_by_email(email: str, db: Session = Depends(get_db)):
    v = db.query(VoluntarioModel).filter(VoluntarioModel.email == email).first()
    if not v:
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    return v


@router.get("/", response_model=List[Voluntario])
def list_voluntarios(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = Query(None),
    is_admin: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(VoluntarioModel)
    if status is not None:
        q = q.filter(VoluntarioModel.status == status)
    if is_admin is not None:
        q = q.filter(VoluntarioModel.is_admin == is_admin)
    return q.offset(skip).limit(limit).all()


@router.get("/{id}", response_model=Voluntario)
def get_voluntario(id: int, db: Session = Depends(get_db)):
    v = db.query(VoluntarioModel).filter(VoluntarioModel.id == id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    return v


@router.post("/", response_model=Voluntario, status_code=201)
def create_voluntario(data: VoluntarioCreate, db: Session = Depends(get_db)):
    v = VoluntarioModel(**data.model_dump())
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@router.put("/{id}", response_model=Voluntario)
def update_voluntario(id: int, data: VoluntarioUpdate, db: Session = Depends(get_db)):
    v = db.query(VoluntarioModel).filter(VoluntarioModel.id == id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(v, key, value)
    db.commit()
    db.refresh(v)
    return v


@router.delete("/{id}", status_code=204)
def delete_voluntario(id: int, db: Session = Depends(get_db)):
    v = db.query(VoluntarioModel).filter(VoluntarioModel.id == id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    db.delete(v)
    db.commit()


# ── Auto-registro ─────────────────────────────────────────────────────

@router.post("/register", response_model=Voluntario, status_code=201)
def register_voluntario(data: VoluntarioRegister, db: Session = Depends(get_db)):
    if db.query(VoluntarioModel).filter(VoluntarioModel.email == data.email).first():
        raise HTTPException(status_code=409, detail="El email ya está registrado")

    v = VoluntarioModel(
        **data.model_dump(),
        registration_date=date.today(),
        status="pendiente",
        is_admin=False,
        email_verified=True,
    )
    db.add(v)
    db.commit()
    db.refresh(v)

    return v


@router.post("/verify-email")
def verify_email_voluntario(data: VerifyEmailRequest, db: Session = Depends(get_db)):
    token = token_service.verify_volunteer_token(db, data.token)
    if not token:
        raise HTTPException(status_code=400, detail="Token inválido o expirado")

    v = db.query(VoluntarioModel).filter(VoluntarioModel.id == token.volunteer_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")

    v.email_verified = True
    v.email_verified_at = datetime.now(timezone.utc)
    token.used_at = datetime.now(timezone.utc)
    db.commit()

    return {"message": "Email verificado. Tu cuenta está pendiente de aprobación por el administrador."}


@router.post("/{id}/approve", response_model=Voluntario)
def approve_voluntario(id: int, db: Session = Depends(get_db)):
    v = db.query(VoluntarioModel).filter(VoluntarioModel.id == id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    if v.status != "pendiente":
        raise HTTPException(status_code=400, detail="El voluntario no está pendiente de aprobación")

    v.status = "activo"
    db.commit()
    db.refresh(v)

    raw = token_service.create_pin_reset_token(db, "volunteer", v.id)
    pin_reset_url = f"{settings.APP_BASE_URL}/restablecer-pin?token={raw}&type=volunteer"

    email_service.send_email(db, SendEmailRequest(
        to=[v.email],
        subject="¡Tu cuenta fue aprobada! - ALMA",
        template="approved",
        variables={
            "name": v.name,
            "pin_reset_url": pin_reset_url,
        },
    ))

    return v

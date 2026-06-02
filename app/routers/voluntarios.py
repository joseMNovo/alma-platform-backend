from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date, datetime, timezone

from app.database import get_db
from app.models.voluntario import Voluntario as VoluntarioModel
from app.schemas.voluntario import Voluntario, VoluntarioCreate, VoluntarioUpdate, VoluntarioAuth, VoluntarioRegister
from app.schemas.tokens import VerifyEmailRequest
from app.schemas.email_log import SendEmailRequest
from app.services import token_service, email_service
from app.utils.logger import log_info, log_warn, log_error
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
    try:
        v = VoluntarioModel(**data.model_dump())
        db.add(v)
        db.commit()
        db.refresh(v)
        log_info("Voluntario creado", module="voluntarios", action="create", meta={"id": v.id})
        return v
    except Exception as exc:
        log_error("Error al crear voluntario", module="voluntarios", action="create", exc_info=True)
        raise


@router.put("/{id}", response_model=Voluntario)
def update_voluntario(id: int, data: VoluntarioUpdate, db: Session = Depends(get_db)):
    v = db.query(VoluntarioModel).filter(VoluntarioModel.id == id).first()
    if not v:
        log_warn("Voluntario no encontrado para editar", module="voluntarios", action="edit", meta={"id": id})
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    try:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(v, key, value)
        db.commit()
        db.refresh(v)
        log_info("Voluntario actualizado", module="voluntarios", action="edit", meta={"id": id})
        return v
    except Exception:
        log_error("Error al actualizar voluntario", module="voluntarios", action="edit", meta={"id": id}, exc_info=True)
        raise


@router.delete("/{id}", status_code=204)
def delete_voluntario(id: int, db: Session = Depends(get_db)):
    v = db.query(VoluntarioModel).filter(VoluntarioModel.id == id).first()
    if not v:
        log_warn("Voluntario no encontrado para eliminar", module="voluntarios", action="delete", meta={"id": id})
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    try:
        db.delete(v)
        db.commit()
        log_info("Voluntario eliminado", module="voluntarios", action="delete", meta={"id": id})
    except Exception:
        log_error("Error al eliminar voluntario", module="voluntarios", action="delete", meta={"id": id}, exc_info=True)
        raise


# ── Auto-registro ─────────────────────────────────────────────────────

@router.post("/register", response_model=Voluntario, status_code=201)
def register_voluntario(data: VoluntarioRegister, db: Session = Depends(get_db)):
    if db.query(VoluntarioModel).filter(VoluntarioModel.email == data.email).first():
        log_warn("Intento de registro con email ya existente", module="voluntarios", action="register")
        raise HTTPException(status_code=409, detail="El email ya está registrado")

    try:
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
        email_masked = data.email[:2] + "***" + data.email[data.email.find("@"):]
        log_info("Voluntario registrado", module="voluntarios", action="register", meta={"id": v.id, "email_masked": email_masked})
        return v
    except Exception:
        log_error("Error al registrar voluntario", module="voluntarios", action="register", exc_info=True)
        raise


@router.post("/verify-email")
def verify_email_voluntario(data: VerifyEmailRequest, db: Session = Depends(get_db)):
    token = token_service.verify_volunteer_token(db, data.token)
    if not token:
        log_warn("Token de verificación inválido o expirado (voluntario)", module="voluntarios", action="verify_email")
        raise HTTPException(status_code=400, detail="Token inválido o expirado")

    v = db.query(VoluntarioModel).filter(VoluntarioModel.id == token.volunteer_id).first()
    if not v:
        log_warn("Voluntario no encontrado al verificar email", module="voluntarios", action="verify_email", meta={"volunteer_id": token.volunteer_id})
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")

    try:
        v.email_verified = True
        v.email_verified_at = datetime.now(timezone.utc)
        token.used_at = datetime.now(timezone.utc)
        db.commit()
        log_info("Email de voluntario verificado", module="voluntarios", action="verify_email", meta={"id": v.id})
    except Exception:
        log_error("Error al verificar email de voluntario", module="voluntarios", action="verify_email", meta={"id": v.id}, exc_info=True)
        raise

    return {"message": "Email verificado. Tu cuenta está pendiente de aprobación por el administrador."}


@router.post("/{id}/approve", response_model=Voluntario)
def approve_voluntario(id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    v = db.query(VoluntarioModel).filter(VoluntarioModel.id == id).first()
    if not v:
        log_warn("Voluntario no encontrado para aprobar", module="voluntarios", action="approve", meta={"id": id})
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    if v.status != "pendiente":
        log_warn("Intento de aprobar voluntario que no está pendiente", module="voluntarios", action="approve", meta={"id": id, "status": v.status})
        raise HTTPException(status_code=400, detail="El voluntario no está pendiente de aprobación")

    try:
        v.status = "activo"
        db.commit()
        db.refresh(v)
        log_info("Voluntario aprobado", module="voluntarios", action="approve", meta={"id": id})
    except Exception:
        log_error("Error al aprobar voluntario", module="voluntarios", action="approve", meta={"id": id}, exc_info=True)
        raise

    raw = token_service.create_pin_reset_token(db, "volunteer", v.id)
    pin_reset_url = f"{settings.APP_BASE_URL}/restablecer-pin?token={raw}&type=volunteer"

    background_tasks.add_task(
        email_service.send_email,
        db,
        SendEmailRequest(
            to=[v.email],
            subject="¡Tu cuenta fue aprobada! - ALMA",
            template="approved",
            variables={
                "name": v.name,
                "pin_reset_url": pin_reset_url,
            },
        ),
    )

    return v

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date, datetime, timezone

from app.database import get_db
from app.models.voluntario import Voluntario as VoluntarioModel
from app.models.participant import ParticipantProfile as ProfileModel
from app.schemas.voluntario import Voluntario, VoluntarioCreate, VoluntarioUpdate, VoluntarioAuth, VoluntarioRegister, VoluntarioEnrollFromDb
from app.schemas.tokens import VerifyEmailRequest
from app.schemas.email_log import SendEmailRequest
from app.services import token_service, email_service
from app.services.notification_service import notify_user
from app.utils.logger import log_info, log_warn, log_error
from config import settings

router = APIRouter()


def _notify_admins_new_volunteer(db: Session, admin_ids: List[int], vol_display: str) -> None:
    """Avisa a cada admin (campanita + push) que hay una solicitud por aprobar.

    100% best-effort y silencioso: corre como background task SEPARADA del
    email (que ya se envió antes). Cualquier fallo se traga acá y NUNCA afecta
    al email ni al registro. Doble red: try/except por admin + try/except global.
    """
    try:
        for aid in admin_ids:
            try:
                notify_user(
                    db,
                    user_type="voluntario",
                    user_id=aid,
                    title="Nueva solicitud de voluntario/a",
                    body=f"{vol_display} se registró y espera aprobación.",
                    kind="system",
                    url="/aprobaciones",
                )
            except Exception:
                log_error("Fallo al notificar aprobación pendiente a admin", module="voluntarios", action="register", meta={"admin_id": aid})
    except Exception:
        # Blindaje total: nada de esta tarea puede propagar una excepción.
        log_error("Fallo general al notificar aprobación pendiente", module="voluntarios", action="register", exc_info=True)


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


def _sync_persona_espejo(db: Session, v: VoluntarioModel, persona_id: Optional[int] = None) -> Optional[ProfileModel]:
    """Crea o vincula la persona espejo de un voluntario (registro maestro).
    - persona_id: vincula esa persona puntual (si existe).
    - si no, busca por email; si tampoco, crea una persona nueva.
    No 'secuestra' una persona ya vinculada a OTRO voluntario (devuelve None).
    Requiere que `v` ya tenga id (llamar tras db.flush()). No hace commit."""
    profile = None
    if persona_id is not None:
        profile = db.query(ProfileModel).filter(ProfileModel.id == persona_id).first()
    if profile is None and v.email:
        profile = db.query(ProfileModel).filter(ProfileModel.email == v.email).first()

    if profile is not None:
        if profile.volunteer_id is not None and profile.volunteer_id != v.id:
            return None  # ya pertenece a otro voluntario: no la tocamos
    else:
        profile = ProfileModel(
            name=v.name, last_name=v.last_name, email=v.email,
            phone=v.phone, birth_date=v.birth_date, source="voluntario",
        )
        db.add(profile)

    profile.is_volunteer = True
    profile.volunteer_id = v.id
    # Completar contacto faltante con los datos del voluntario.
    if not profile.name:
        profile.name = v.name
    if not profile.last_name:
        profile.last_name = v.last_name
    if not profile.email:
        profile.email = v.email
    if not profile.phone:
        profile.phone = v.phone
    if not profile.birth_date:
        profile.birth_date = v.birth_date
    return profile


@router.post("/", response_model=Voluntario, status_code=201)
def create_voluntario(data: VoluntarioCreate, db: Session = Depends(get_db)):
    try:
        v = VoluntarioModel(**data.model_dump())
        db.add(v)
        db.flush()  # obtener v.id antes de espejar la persona
        _sync_persona_espejo(db, v)  # mantiene el directorio de personas consistente
        db.commit()
        db.refresh(v)
        log_info("Voluntario creado", module="voluntarios", action="create", meta={"id": v.id})
        return v
    except Exception:
        db.rollback()
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

FALLBACK_ADMIN_EMAIL = "manunovo@gmail.com"

@router.post("/register", response_model=Voluntario, status_code=201)
def register_voluntario(data: VoluntarioRegister, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
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
    except Exception:
        log_error("Error al registrar voluntario", module="voluntarios", action="register", exc_info=True)
        raise

    # Notificar a admins (activos). Reusamos la query para email + push.
    admins = db.query(VoluntarioModel).filter(
        VoluntarioModel.is_admin == True,
        VoluntarioModel.status == "activo",
    ).all()
    admin_emails = [a.email for a in admins if a.email]
    admin_ids = [a.id for a in admins]
    recipients = admin_emails if admin_emails else [FALLBACK_ADMIN_EMAIL]

    vol_display = f"{v.name}{(' ' + v.last_name) if v.last_name else ''}"

    background_tasks.add_task(
        email_service.send_email,
        db,
        SendEmailRequest(
            to=recipients,
            subject="Nueva solicitud de voluntario/a — ALMA",
            template="new_volunteer",
            variables={
                "name": vol_display,
                "email": v.email,
                "app_url": settings.APP_BASE_URL,
            },
        ),
    )

    # Campanita + push a los admins (in-app, además del email).
    background_tasks.add_task(_notify_admins_new_volunteer, db, admin_ids, vol_display)

    return v


@router.post("/enroll-from-db", response_model=Voluntario, status_code=201)
def enroll_volunteer_from_db(data: VoluntarioEnrollFromDb, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Habilita a una persona (o crea una nueva) como voluntario/a desde el
    módulo Base de datos. Crea la ficha en `voluntarios` (status='pendiente'),
    la vincula a la persona maestra y avisa por mail a la persona + a los admins."""
    if db.query(VoluntarioModel).filter(VoluntarioModel.email == data.email).first():
        log_warn("Intento de habilitar voluntario con email ya existente", module="voluntarios", action="enroll_from_db")
        raise HTTPException(status_code=409, detail="Ya existe un voluntario con ese email")

    # Si se indicó una persona existente, validarla antes de crear nada.
    profile = None
    if data.persona_id is not None:
        profile = db.query(ProfileModel).filter(ProfileModel.id == data.persona_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Persona no encontrada")
        if profile.volunteer_id is not None:
            raise HTTPException(status_code=409, detail="La persona ya está vinculada a un voluntario")

    try:
        v = VoluntarioModel(
            name=data.name,
            last_name=data.last_name,
            email=data.email,
            phone=data.phone,
            birth_date=data.birth_date,
            gender=data.gender,
            age=data.age,
            registration_date=date.today(),
            status="pendiente",
            is_admin=False,
            email_verified=True,
        )
        db.add(v)
        db.flush()  # obtiene v.id sin cerrar la transacción

        # Vincular / crear la persona espejo.
        if profile is None and data.email:
            # No se pasó persona_id: reusar una persona con el mismo email si existe.
            profile = db.query(ProfileModel).filter(ProfileModel.email == data.email).first()
        if profile is None:
            profile = ProfileModel(
                name=data.name,
                last_name=data.last_name,
                email=data.email,
                phone=data.phone,
                birth_date=data.birth_date,
                source="voluntario",
            )
            db.add(profile)

        profile.is_volunteer = True
        profile.volunteer_id = v.id
        # Completar datos de contacto faltantes con los del voluntario.
        if not profile.name:
            profile.name = data.name
        if not profile.last_name:
            profile.last_name = data.last_name
        if not profile.email:
            profile.email = data.email
        if not profile.phone:
            profile.phone = data.phone
        if not profile.birth_date:
            profile.birth_date = data.birth_date

        db.commit()
        db.refresh(v)
        log_info("Voluntario habilitado desde la base de datos", module="voluntarios", action="enroll_from_db", meta={"id": v.id, "persona_id": profile.id})
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        log_error("Error al habilitar voluntario desde la base de datos", module="voluntarios", action="enroll_from_db", exc_info=True)
        raise

    registered_by = (data.registered_by_name or "").strip() or "El equipo de ALMA"

    # 1) Mail a la persona: avisarle que quedó pendiente de aprobación.
    background_tasks.add_task(
        email_service.send_email,
        db,
        SendEmailRequest(
            to=[v.email],
            subject="Te sumamos como voluntario/a — ALMA",
            template="volunteer_pending",
            variables={"name": v.name, "registered_by": registered_by},
        ),
    )

    # 2) Mail a los admins: nueva solicitud para aprobar (reusa el template existente).
    admin_emails = [
        a.email for a in
        db.query(VoluntarioModel).filter(
            VoluntarioModel.is_admin == True,
            VoluntarioModel.status == "activo",
            VoluntarioModel.email != None,
        ).all()
        if a.email
    ]
    recipients = admin_emails if admin_emails else [FALLBACK_ADMIN_EMAIL]
    background_tasks.add_task(
        email_service.send_email,
        db,
        SendEmailRequest(
            to=recipients,
            subject="Nueva solicitud de voluntario/a — ALMA",
            template="new_volunteer",
            variables={
                "name": f"{v.name}{(' ' + v.last_name) if v.last_name else ''}",
                "email": v.email,
                "app_url": settings.APP_BASE_URL,
            },
        ),
    )

    return v


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
        # Recién al aprobar el voluntario entra al directorio maestro de personas.
        _sync_persona_espejo(db, v)
        db.commit()
        db.refresh(v)
        log_info("Voluntario aprobado", module="voluntarios", action="approve", meta={"id": id})
    except Exception:
        db.rollback()
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

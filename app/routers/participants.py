from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, timezone

from config import settings
from app.database import get_db, SessionLocal
from app.schemas.tokens import VerifyEmailRequest
from app.schemas.email_log import SendEmailRequest
from app.services import token_service, email_service
from app.models.participant import (
    Participant as ParticipantModel,
    ParticipantProfile as ProfileModel,
    ParticipantProgramEnrollment as EnrollmentModel,
)
from app.models.voluntario import Voluntario as VoluntarioModel
from app.schemas.participant import (
    Participant, ParticipantCreate, ParticipantUpdate,
    ParticipantAuth,
    ParticipantProfile, ParticipantProfileCreate, ParticipantProfileUpdate,
    ParticipantProgramEnrollment, ParticipantProgramEnrollmentCreate,
    InviteParticipantRequest, RevertVolunteerRequest, ConversionResult,
)
from app.models.access import PersonAccessGrant
from app.models.calendar import CalendarEventParticipant as CEPModel
from app.services import access_service
from app.utils.logger import log_info, log_warn, log_error

router = APIRouter()


def _mask(email: str) -> str:
    at = email.find("@")
    return f"{email[:2]}***{email[at:]}" if at > 0 else "***"


def send_invitation_email(participant_id: int, email: str, name: str | None, registered_by: str | None) -> None:
    """Manda el mail de invitación a la plataforma.

    Corre en background con su PROPIA sesión (la del request ya se cerró).
    Reusa el token de restablecer-pin: la persona no tiene PIN todavía, así que
    el link la lleva a crearlo (`&new=1` cambia el copy de esa pantalla). Fijar
    el PIN por ese link, además, le verifica el email (ver pin_reset.confirm).
    """
    db = SessionLocal()
    try:
        raw = token_service.create_pin_reset_token(db, "participant", participant_id)
        url = f"{settings.APP_BASE_URL}/restablecer-pin?token={raw}&type=participant&new=1"
        email_service.send_email(
            db,
            SendEmailRequest(
                to=[email],
                subject="Te invitamos a Comunidad ALMA",
                template="invitation",
                variables={
                    "name": name or email.split("@")[0],
                    "invite_url": url,
                    "expiry_hours": str(settings.TOKEN_EXPIRY_HOURS),
                    "registered_by": (registered_by or "").strip() or "El equipo de ALMA",
                },
            ),
        )
        log_info("Invitación a la plataforma enviada", module="participantes", action="invite",
                 meta={"participant_id": participant_id, "email_masked": _mask(email)})
    except Exception:
        log_error("Fallo al enviar la invitación a la plataforma", module="participantes", action="invite",
                  meta={"participant_id": participant_id, "email_masked": _mask(email)}, exc_info=True)
    finally:
        db.close()


@router.post("/invite", response_model=ConversionResult)
def invite_participant(data: InviteParticipantRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Invita a una persona ya cargada en la base a crear su cuenta de participante.

    Le crea el login (sin PIN, sin verificar) y le manda el mail para que elija
    su PIN. Idempotente: si ya fue invitada y todavía no activó, REENVÍA el link
    en vez de fallar. Si ya tiene cuenta activa, corta con 409.
    """
    profile = db.query(ProfileModel).filter(ProfileModel.id == data.profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Persona no encontrada")

    email = (profile.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="La persona necesita un email para poder invitarla")

    # Un voluntario/a NO puede ser participante a la vez (mutuamente excluyente).
    if db.query(VoluntarioModel).filter(VoluntarioModel.email == email).first():
        raise HTTPException(status_code=409, detail="Ese email pertenece a un voluntario/a. No puede ser participante a la vez.")

    try:
        part = None
        outcome = "invited"

        if profile.participant_id is not None:
            part = db.query(ParticipantModel).filter(ParticipantModel.id == profile.participant_id).first()

        # Defensa: puede existir un login por email sin estar vinculado a la ficha.
        if part is None:
            part = db.query(ParticipantModel).filter(ParticipantModel.email == email).first()

        if part is not None:
            if part.pin_hash:
                raise HTTPException(status_code=409, detail="La persona ya tiene una cuenta de participante activa")
            # Existe pero sin PIN => nunca terminó de activarse: se reactiva y se reenvía.
            part.is_active = True
            part.email = email
            outcome = "reinvited"
        else:
            part = ParticipantModel(email=email, pin_hash=None, is_active=True, email_verified=False)
            db.add(part)
            db.flush()

        profile.participant_id = part.id
        profile.invited_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(part)
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        log_error("Error al invitar participante", module="participantes", action="invite", exc_info=True)
        raise

    name = (profile.name or "").strip() or None
    background_tasks.add_task(send_invitation_email, part.id, email, name, data.registered_by_name)
    log_info("Persona invitada a la plataforma", module="participantes", action="invite",
             meta={"profile_id": profile.id, "participant_id": part.id, "outcome": outcome})
    return ConversionResult(ok=True, outcome=outcome, participant_id=part.id, email=email)


@router.post("/revert-volunteer", response_model=ConversionResult)
def revert_volunteer_to_participant(data: RevertVolunteerRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Quita el rol de voluntario/a de una persona y la vuelve participante.

    Inversa de enroll-from-db. NO borra nada ("nada se borra solo"): la ficha de
    voluntario/a queda con status='inactivo'. Después:
      - si la persona tenía login de participante (se desactivó al hacerla
        voluntaria), se REACTIVA y entra con su PIN de siempre;
      - si ese login no tenía PIN, o no había login, se le crea/invita para que
        elija uno. El email es obligatorio para poder invitar.
    """
    profile = db.query(ProfileModel).filter(ProfileModel.id == data.persona_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    if profile.volunteer_id is None and not profile.is_volunteer:
        raise HTTPException(status_code=409, detail="La persona no es voluntaria")

    vol = None
    if profile.volunteer_id is not None:
        vol = db.query(VoluntarioModel).filter(VoluntarioModel.id == profile.volunteer_id).first()
        if vol and vol.is_admin:
            raise HTTPException(status_code=409, detail="No se puede quitar el rol a un administrador desde acá.")

    email = (profile.email or "").strip().lower()

    try:
        # 1) Desactivar la ficha de voluntario/a (sin borrarla) y desvincular.
        if vol is not None:
            vol.status = "inactivo"
        profile.is_volunteer = False
        profile.volunteer_id = None

        # 2) Resolver el login de participante.
        outcome = "no_login"
        part = None
        needs_invite = False

        if profile.participant_id is not None:
            part = db.query(ParticipantModel).filter(ParticipantModel.id == profile.participant_id).first()

        if part is None and email:
            # No estaba vinculado pero puede existir por email.
            part = db.query(ParticipantModel).filter(ParticipantModel.email == email).first()
            if part is not None:
                profile.participant_id = part.id

        if part is not None:
            part.is_active = True
            if part.pin_hash:
                outcome = "reactivated"         # entra con su PIN de siempre
            else:
                needs_invite = True
                outcome = "reinvited"
        elif email:
            part = ParticipantModel(email=email, pin_hash=None, is_active=True, email_verified=False)
            db.add(part)
            db.flush()
            profile.participant_id = part.id
            profile.invited_at = datetime.now(timezone.utc)
            needs_invite = True
            outcome = "invited"
        else:
            # Sin email no hay forma de crear login: queda como persona suelta.
            outcome = "no_login"

        db.commit()
        if part is not None:
            db.refresh(part)
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        log_error("Error al revertir voluntario a participante", module="participantes", action="revert_volunteer", exc_info=True)
        raise

    if needs_invite and part is not None and email:
        name = (profile.name or "").strip() or None
        background_tasks.add_task(send_invitation_email, part.id, email, name, data.registered_by_name)

    log_info("Voluntario revertido a participante", module="participantes", action="revert_volunteer",
             meta={"persona_id": profile.id, "outcome": outcome})
    return ConversionResult(ok=True, outcome=outcome, participant_id=(part.id if part else None), email=email or None)


# ── Vista de gestión (pestaña "Participantes") ────────────────────────

@router.get("/gestion")
def list_participants_management(db: Session = Depends(get_db)):
    """Lista de personas que tienen login de participante, con los datos que
    importan para gestionarlas: verificación del email, fecha de registro,
    inscripciones y si tienen alguna capacitación habilitada.

    Es la gemela de la vista de Voluntarios: acá NO se listan las personas
    sueltas del directorio (esas van en Base de datos), solo las que se
    registraron como participante (participant_profiles.participant_id != NULL).
    """
    profiles = db.query(ProfileModel).filter(ProfileModel.participant_id.isnot(None)).all()
    if not profiles:
        return []

    participant_ids = [p.participant_id for p in profiles]
    person_ids = [p.id for p in profiles]

    # Datos del login (email_verified, created_at) en una sola consulta.
    parts = {
        pt.id: pt
        for pt in db.query(ParticipantModel).filter(ParticipantModel.id.in_(participant_ids)).all()
    }

    # Inscripciones por participante = anotaciones a encuentros del calendario
    # (calendar_event_participants). La inscripción es por evento, no por
    # programa, así que este número cuenta encuentros a los que se anotó.
    enroll_counts = dict(
        db.query(CEPModel.participant_id, func.count(CEPModel.id))
        .filter(
            CEPModel.participant_id.in_(participant_ids),
            CEPModel.status != "cancelado",
        )
        .group_by(CEPModel.participant_id)
        .all()
    )

    # ¿Quién tiene alguna capacitación habilitada VIGENTE? (cruce con Accesos)
    granted_person_ids = {
        row[0]
        for row in db.query(PersonAccessGrant.person_id)
        .filter(
            PersonAccessGrant.person_id.in_(person_ids),
            PersonAccessGrant.module_key == "capacitaciones",
            *access_service.live_filter(),
        )
        .all()
    }

    result = []
    for p in profiles:
        pt = parts.get(p.participant_id)
        result.append({
            "person_id": p.id,
            "participant_id": p.participant_id,
            "name": p.name,
            "last_name": p.last_name,
            "email": p.email or (pt.email if pt else None),
            "phone": p.phone,
            "email_verified": bool(pt.email_verified) if pt else False,
            "created_at": pt.created_at if pt and pt.created_at else p.created_at,
            "enrollments_count": int(enroll_counts.get(p.participant_id, 0)),
            "has_training_access": p.id in granted_person_ids,
        })

    # Más recientes primero; los sin fecha (datos viejos) al final.
    result.sort(key=lambda r: (r["created_at"] is not None, r["created_at"] or ""), reverse=True)
    return result


# ── Participants ──────────────────────────────────────────────────────

@router.get("/auth/{email}", response_model=ParticipantAuth)
def get_participant_auth(email: str, db: Session = Depends(get_db)):
    """Endpoint interno para autenticación — devuelve pin_hash."""
    p = db.query(ParticipantModel).filter(ParticipantModel.email == email).first()
    if not p:
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    return p


@router.get("/", response_model=List[Participant])
def list_participants(
    skip: int = 0,
    limit: int = 100,
    is_active: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(ParticipantModel)
    if is_active is not None:
        q = q.filter(ParticipantModel.is_active == is_active)
    return q.offset(skip).limit(limit).all()


@router.get("/{id}", response_model=Participant)
def get_participant(id: int, db: Session = Depends(get_db)):
    p = db.query(ParticipantModel).filter(ParticipantModel.id == id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    return p


@router.get("/by-email/{email}", response_model=Participant)
def get_participant_by_email(email: str, db: Session = Depends(get_db)):
    p = db.query(ParticipantModel).filter(ParticipantModel.email == email).first()
    if not p:
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    return p


@router.post("/", response_model=Participant, status_code=201)
def create_participant(data: ParticipantCreate, db: Session = Depends(get_db)):
    try:
        p = ParticipantModel(**data.model_dump())
        db.add(p)
        db.commit()
        db.refresh(p)
        log_info("Participante creado", module="participantes", action="create_participant", meta={"id": p.id})
        return p
    except Exception:
        log_error("Error al crear participante", module="participantes", action="create_participant", exc_info=True)
        raise


@router.put("/{id}", response_model=Participant)
def update_participant(id: int, data: ParticipantUpdate, db: Session = Depends(get_db)):
    p = db.query(ParticipantModel).filter(ParticipantModel.id == id).first()
    if not p:
        log_warn("Participante no encontrado para editar", module="participantes", action="edit_participant", meta={"id": id})
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    try:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(p, key, value)
        db.commit()
        db.refresh(p)
        log_info("Participante actualizado", module="participantes", action="edit_participant", meta={"id": id})
        return p
    except Exception:
        log_error("Error al actualizar participante", module="participantes", action="edit_participant", meta={"id": id}, exc_info=True)
        raise


@router.delete("/{id}", status_code=204)
def delete_participant(id: int, db: Session = Depends(get_db)):
    p = db.query(ParticipantModel).filter(ParticipantModel.id == id).first()
    if not p:
        log_warn("Participante no encontrado para eliminar", module="participantes", action="delete_participant", meta={"id": id})
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    try:
        db.delete(p)
        db.commit()
        log_info("Participante eliminado", module="participantes", action="delete_participant", meta={"id": id})
    except Exception:
        log_error("Error al eliminar participante", module="participantes", action="delete_participant", meta={"id": id}, exc_info=True)
        raise


# ── Participant Profiles ──────────────────────────────────────────────

@router.get("/{id}/profile", response_model=ParticipantProfile)
def get_profile(id: int, db: Session = Depends(get_db)):
    prof = db.query(ProfileModel).filter(ProfileModel.participant_id == id).first()
    if not prof:
        raise HTTPException(status_code=404, detail="Perfil no encontrado")
    return prof


@router.post("/{id}/profile", response_model=ParticipantProfile, status_code=201)
def create_profile(id: int, data: ParticipantProfileCreate, db: Session = Depends(get_db)):
    if not db.query(ParticipantModel).filter(ParticipantModel.id == id).first():
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    prof = ProfileModel(**{**data.model_dump(), "participant_id": id})
    db.add(prof)
    db.commit()
    db.refresh(prof)
    return prof


@router.put("/{id}/profile", response_model=ParticipantProfile)
def update_profile(id: int, data: ParticipantProfileUpdate, db: Session = Depends(get_db)):
    prof = db.query(ProfileModel).filter(ProfileModel.participant_id == id).first()
    if not prof:
        raise HTTPException(status_code=404, detail="Perfil no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(prof, key, value)
    db.commit()
    db.refresh(prof)
    return prof


# ── Participant Program Enrollments ───────────────────────────────────

@router.get("/{id}/enrollments", response_model=List[ParticipantProgramEnrollment])
def list_enrollments(id: int, db: Session = Depends(get_db)):
    return db.query(EnrollmentModel).filter(EnrollmentModel.participant_id == id).all()


@router.post("/{id}/enrollments", response_model=ParticipantProgramEnrollment, status_code=201)
def create_enrollment(id: int, data: ParticipantProgramEnrollmentCreate, db: Session = Depends(get_db)):
    if not db.query(ParticipantModel).filter(ParticipantModel.id == id).first():
        raise HTTPException(status_code=404, detail="Participante no encontrado")

    # Idempotente: si ya está inscripto al mismo programa, se devuelve la fila
    # existente en vez de crear un duplicado (y de chocar contra el UNIQUE de
    # la tabla). Inscribirse dos veces no es un error, es un no-op.
    existing = (
        db.query(EnrollmentModel)
        .filter(
            EnrollmentModel.participant_id == id,
            EnrollmentModel.type == data.type,
            EnrollmentModel.item_id == data.item_id,
        )
        .first()
    )
    if existing:
        return existing

    e = EnrollmentModel(**{**data.model_dump(), "participant_id": id})
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


@router.delete("/{id}/enrollments/{enrollment_id}", status_code=204)
def delete_enrollment(id: int, enrollment_id: int, db: Session = Depends(get_db)):
    e = db.query(EnrollmentModel).filter(
        EnrollmentModel.id == enrollment_id,
        EnrollmentModel.participant_id == id,
    ).first()
    if not e:
        raise HTTPException(status_code=404, detail="Inscripción no encontrada")
    db.delete(e)
    db.commit()


# ── Email verification ────────────────────────────────────────────────

@router.post("/verify-email")
def verify_email_participant(data: VerifyEmailRequest, db: Session = Depends(get_db)):
    token = token_service.verify_participant_token(db, data.token)
    if not token:
        log_warn("Token de verificación inválido o expirado (participante)", module="participantes", action="verify_email")
        raise HTTPException(status_code=400, detail="Token inválido o expirado")

    p = db.query(ParticipantModel).filter(ParticipantModel.id == token.participant_id).first()
    if not p:
        log_warn("Participante no encontrado al verificar email", module="participantes", action="verify_email", meta={"participant_id": token.participant_id})
        raise HTTPException(status_code=404, detail="Participante no encontrado")

    try:
        p.email_verified = True
        p.email_verified_at = datetime.now(timezone.utc)
        token.used_at = datetime.now(timezone.utc)
        db.commit()
        log_info("Email de participante verificado", module="participantes", action="verify_email", meta={"id": p.id})
    except Exception:
        log_error("Error al verificar email de participante", module="participantes", action="verify_email", meta={"id": p.id}, exc_info=True)
        raise

    return {"message": "Email verificado correctamente."}

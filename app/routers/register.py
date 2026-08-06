"""
app/routers/register.py — ALMA Backend — Auto-registro de participantes
========================================================================
El formulario público (/registro) manda email + PIN. El PIN llega YA hasheado
con bcrypt desde el BFF de Next: la contraseña en claro nunca sale de Next.js.

IDENTIDAD POR EMAIL REAL: no hay token compartido. La cuenta nace inactiva y
se activa haciendo click en el link que llega al correo, válido 30 minutos.
Esto es más simple para la persona (no hay que pasarle un código por otro
canal) y le deja a ALMA algo que un token nunca dio: una casilla verificada
para avisar de habilitaciones, vencimientos y novedades.

Contraparte del registro de voluntarios (`POST /voluntarios/register`), con
una diferencia deliberada: el voluntario queda 'pendiente' hasta que un admin
lo aprueba, mientras que el participante entra solo con verificar su mail —
su cuenta sola no le da acceso a nada pago. Lo que se cobra son las
HABILITACIONES, que un admin otorga después (ver app/routers/accesos.py).

LINK-BY-EMAIL: si la persona ya existe en `participant_profiles` (importada
del Excel o cargada a mano) y todavía no tiene login, se le ADJUNTA la cuenta
en vez de crear un duplicado.
"""
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from config import settings
from app.database import get_db, SessionLocal
from app.models.participant import Participant, ParticipantProfile
from app.models.voluntario import Voluntario
from app.schemas.email_log import SendEmailRequest
from app.schemas.register import RegisterRequest, RegisterResponse, ResendVerificationRequest
from app.services import email_service, token_service
from app.services.notification_service import notify_user
from app.utils.logger import log_info, log_warn, log_error

router = APIRouter()


def _mask(email: str) -> str:
    """Email enmascarado para los logs: no se loguean datos personales completos."""
    at = email.find("@")
    return f"{email[:2]}***{email[at:]}" if at > 0 else "***"


def _expiry_label() -> str:
    minutes = settings.PARTICIPANT_VERIFICATION_MINUTES
    if minutes % 60 == 0 and minutes >= 60:
        hours = minutes // 60
        return f"{hours} hora" if hours == 1 else f"{hours} horas"
    return f"{minutes} minutos"


def send_verification_email(participant_id: int, email: str, name: str | None = None, next_path: str | None = None) -> None:
    """Emite un token nuevo y manda el mail de verificación.

    Corre en background con su PROPIA sesión: la del request ya se cerró.
    Invalida los tokens anteriores, así "reenviar" deja muerto al link viejo.
    """
    db = SessionLocal()
    try:
        token_service.invalidate_participant_tokens(db, participant_id)
        raw = token_service.create_participant_verification_token(
            db, participant_id, minutes=settings.PARTICIPANT_VERIFICATION_MINUTES
        )
        url = f"{settings.APP_BASE_URL}/verificar-email?token={raw}&type=participant"
        # Volver a la compra después de verificar. Solo rutas internas de
        # capacitaciones: el frontend lo revalida antes de navegar.
        if next_path and next_path.startswith("/capacitacion/") and not next_path.startswith("//"):
            url += f"&next={quote(next_path, safe='/')}"

        email_service.send_email(
            db,
            SendEmailRequest(
                to=[email],
                subject="Confirmá tu email — ALMA",
                template="verification",
                variables={
                    "name": name or email.split("@")[0],
                    "verification_url": url,
                    "expiry": _expiry_label(),
                },
            ),
        )
        log_info(
            "Email de verificación enviado",
            module="registro", action="send_verification",
            meta={"participant_id": participant_id, "email_masked": _mask(email)},
        )
    except Exception:
        log_error(
            "Fallo al enviar el email de verificación",
            module="registro", action="send_verification",
            meta={"participant_id": participant_id, "email_masked": _mask(email)},
            exc_info=True,
        )
    finally:
        db.close()


def _notify_admins_new_participant(email: str) -> None:
    """Avisa a los admins que hay una persona nueva registrada.

    Es la punta del embudo de cobro: alguien que se registra y todavía no tiene
    ninguna habilitación es alguien a quien hay que pasarle el CBU.
    """
    db = SessionLocal()
    try:
        # Se traen SOLO los ids (ints planos), no los objetos ORM: si un
        # notify_user falla y aborta la transacción, acceder a `admin.id` en el
        # manejador de error dispararía un lazy-load sobre una sesión rota
        # (PendingRollbackError). Con ints planos el except nunca toca la base.
        admin_ids = [row[0] for row in db.query(Voluntario.id).filter(Voluntario.is_admin.is_(True)).all()]
        for admin_id in admin_ids:
            try:
                notify_user(
                    db,
                    user_type="voluntario",
                    user_id=admin_id,
                    title="Nueva persona registrada",
                    body=f"{email} creó su cuenta. Si va a comprar una capacitación, habilitala desde Accesos.",
                    kind="system",
                    url="/accesos",
                )
            except Exception:
                # Dejar la sesión usable para el próximo admin.
                db.rollback()
                log_error(
                    "Fallo al notificar registro a un admin",
                    module="registro", action="notify_admin",
                    meta={"admin_id": admin_id}, exc_info=True,
                )
    except Exception:
        log_error("Fallo general al notificar el registro", module="registro", action="notify_admin", exc_info=True)
    finally:
        db.close()


@router.post("/participante", response_model=RegisterResponse, status_code=201)
def register_participante(
    data: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Alta de cuenta de participante desde el formulario público."""
    email = str(data.email).strip().lower()

    # 1) ¿Ya tiene cuenta?
    existing = db.query(Participant).filter(Participant.email == email).first()
    if existing:
        # Si nunca verificó, no es un choque real: es alguien que se registró,
        # perdió el mail y vuelve a intentar. Se le reenvía el link en vez de
        # frenarlo con un error que no puede resolver.
        if not existing.email_verified:
            background_tasks.add_task(send_verification_email, existing.id, email)
            log_info(
                "Reintento de registro sin verificar: se reenvía el link",
                module="registro", action="register_participant",
                meta={"id": existing.id, "email_masked": _mask(email)},
            )
            return RegisterResponse(
                id=existing.id, email=existing.email, role="participante",
                email_verified=False, verification_sent_to=email,
            )

        log_warn(
            "Registro con email ya existente",
            module="registro", action="register_participant",
            meta={"email_masked": _mask(email)},
        )
        raise HTTPException(status_code=409, detail="El email ya está registrado")

    # 2) ¿Es voluntario? Ya tiene cuenta por otro lado y se confundió de
    #    formulario. Mejor decírselo que crearle una segunda identidad.
    if db.query(Voluntario).filter(Voluntario.email == email).first():
        raise HTTPException(
            status_code=409,
            detail="Ese email ya está registrado como voluntario/a. Ingresá con tu PIN de voluntario/a.",
        )

    try:
        participant = Participant(
            email=email,
            pin_hash=data.pin_hash,
            is_active=True,
            # Nace SIN verificar: hasta el click en el mail no puede entrar.
            email_verified=False,
        )
        db.add(participant)
        db.flush()  # necesitamos el id para vincular la ficha

        # 3) LINK-BY-EMAIL: si la persona ya está en la base (Excel, carga
        #    manual), se le adjunta el login en vez de duplicarla.
        profile = (
            db.query(ParticipantProfile)
            .filter(ParticipantProfile.email == email, ParticipantProfile.participant_id.is_(None))
            .first()
        )

        if profile:
            profile.participant_id = participant.id
            # Si la ficha vieja no tenía nombre y ahora lo dieron, se completa;
            # nunca se pisa lo que ya estaba cargado.
            if not (profile.name or "").strip() and (data.name or "").strip():
                profile.name = data.name.strip()
            if not (profile.last_name or "").strip() and (data.last_name or "").strip():
                profile.last_name = data.last_name.strip()
            linked = True
        else:
            profile = ParticipantProfile(
                participant_id=participant.id,
                email=email,
                name=(data.name or "").strip() or None,
                last_name=(data.last_name or "").strip() or None,
                source="registro",
                accepts_notifications=False,
                accepts_whatsapp=False,
            )
            db.add(profile)
            linked = False

        db.commit()
        db.refresh(participant)

    except Exception:
        db.rollback()
        log_error(
            "Error al registrar participante",
            module="registro", action="register_participant",
            meta={"email_masked": _mask(email)}, exc_info=True,
        )
        raise

    log_info(
        "Participante registrado (pendiente de verificar email)",
        module="registro", action="register_participant",
        meta={"id": participant.id, "email_masked": _mask(email), "persona_vinculada": linked},
    )

    name = (profile.name or "").strip() or None
    background_tasks.add_task(send_verification_email, participant.id, email, name, data.next)
    background_tasks.add_task(_notify_admins_new_participant, email)

    return RegisterResponse(
        id=participant.id, email=participant.email, role="participante",
        email_verified=False, verification_sent_to=email,
    )


@router.post("/participante/reenviar", status_code=202)
def resend_verification(
    data: ResendVerificationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Reenvía el link de verificación.

    Responde SIEMPRE lo mismo exista o no la cuenta: si contestara distinto,
    cualquiera podría averiguar qué emails están registrados probando de a uno.
    """
    email = str(data.email).strip().lower()
    participant = db.query(Participant).filter(Participant.email == email).first()

    if participant and not participant.email_verified:
        background_tasks.add_task(send_verification_email, participant.id, email)
        log_info(
            "Reenvío de verificación solicitado",
            module="registro", action="resend_verification",
            meta={"id": participant.id, "email_masked": _mask(email)},
        )
    else:
        log_warn(
            "Reenvío pedido para una cuenta inexistente o ya verificada",
            module="registro", action="resend_verification",
            meta={"email_masked": _mask(email)},
        )

    return {"message": "Si la cuenta existe y está pendiente, te reenviamos el email."}

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.database import get_db
from app.models.voluntario import Voluntario as VoluntarioModel
from app.models.participant import Participant as ParticipantModel
from app.schemas.tokens import RequestPinResetRequest, ConfirmPinResetRequest
from app.schemas.email_log import SendEmailRequest
from app.services import token_service, email_service
from app.utils.logger import log_info, log_warn, log_error
from config import settings

router = APIRouter()


@router.post("/request")
def request_pin_reset(data: RequestPinResetRequest, db: Session = Depends(get_db)):
    if data.user_type == "volunteer":
        user = db.query(VoluntarioModel).filter(VoluntarioModel.email == data.email).first()
    else:
        user = db.query(ParticipantModel).filter(ParticipantModel.email == data.email).first()

    # Respuesta genérica para no revelar si el email existe
    if not user:
        log_info("Solicitud de reset de PIN para usuario no encontrado (respuesta genérica)", module="pin_reset", action="request_reset", meta={"user_type": data.user_type})
        return {"message": "Si el email existe, vas a recibir un link para restablecer tu PIN."}

    try:
        raw = token_service.create_pin_reset_token(db, data.user_type, user.id)
        reset_url = f"{settings.APP_BASE_URL}/restablecer-pin?token={raw}&type={data.user_type}"

        name = getattr(user, "name", "")
        email_service.send_email(db, SendEmailRequest(
            to=[data.email],
            subject="Restablecer PIN - ALMA",
            template="pin_reset",
            variables={
                "name": name,
                "reset_url": reset_url,
                "expiry_hours": str(settings.TOKEN_EXPIRY_HOURS),
            },
        ))
        log_info("Solicitud de reset de PIN procesada", module="pin_reset", action="request_reset", meta={"user_type": data.user_type, "user_id": user.id})
    except Exception:
        log_error("Error al procesar solicitud de reset de PIN", module="pin_reset", action="request_reset", meta={"user_type": data.user_type}, exc_info=True)
        raise

    return {"message": "Si el email existe, vas a recibir un link para restablecer tu PIN."}


@router.post("/confirm")
def confirm_pin_reset(data: ConfirmPinResetRequest, db: Session = Depends(get_db)):
    token = token_service.verify_pin_reset_token(db, data.token, data.user_type)
    if not token:
        log_warn("Token de reset de PIN inválido o expirado", module="pin_reset", action="confirm_reset", meta={"user_type": data.user_type})
        raise HTTPException(status_code=400, detail="Token inválido o expirado")

    if data.user_type == "volunteer":
        user = db.query(VoluntarioModel).filter(VoluntarioModel.id == token.user_id).first()
    else:
        user = db.query(ParticipantModel).filter(ParticipantModel.id == token.user_id).first()

    if not user:
        log_warn("Usuario no encontrado al confirmar reset de PIN", module="pin_reset", action="confirm_reset", meta={"user_type": data.user_type, "user_id": token.user_id})
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    try:
        user.pin_hash = data.new_pin_hash
        token.used_at = datetime.now(timezone.utc)
        db.commit()
        log_info("PIN restablecido correctamente", module="pin_reset", action="confirm_reset", meta={"user_type": data.user_type, "user_id": token.user_id})
    except Exception:
        log_error("Error al confirmar reset de PIN", module="pin_reset", action="confirm_reset", meta={"user_type": data.user_type}, exc_info=True)
        raise

    return {"message": "PIN actualizado correctamente"}

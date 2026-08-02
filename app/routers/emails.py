from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.email_log import EmailLog
from app.schemas.email_log import SendEmailRequest, EmailLogOut
from app.services import email_service
from app.utils.logger import log_info, log_error

router = APIRouter()


@router.post("/send", response_model=EmailLogOut, status_code=201)
def send_email(req: SendEmailRequest, db: Session = Depends(get_db)):
    """Manda el email y devuelve el EmailLog ya resuelto ('sent' o 'failed').

    Espera a Resend, así que la respuesta tarda lo que tarde Resend. Quien llame
    por HTTP tiene que darle timeout de sobra: `alma_cron.py` usa 90s por esto.
    """
    try:
        result = email_service.send_email(db, req)
        log_info("Email enviado", module="emails", action="send_email", meta={"template": req.template, "to_count": len(req.to)})
        return result
    except Exception:
        log_error("Error al enviar email", module="emails", action="send_email", meta={"template": req.template}, exc_info=True)
        raise


@router.get("/logs", response_model=List[EmailLogOut])
def list_logs(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = Query(None),
    template: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(EmailLog).order_by(EmailLog.sent_at.desc())
    if status:
        q = q.filter(EmailLog.status == status)
    if template:
        q = q.filter(EmailLog.template == template)
    return q.offset(skip).limit(limit).all()

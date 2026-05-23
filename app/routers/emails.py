from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.email_log import EmailLog
from app.schemas.email_log import SendEmailRequest, EmailLogOut
from app.services import email_service

router = APIRouter()


@router.post("/send", response_model=EmailLogOut, status_code=201)
def send_email(req: SendEmailRequest, db: Session = Depends(get_db)):
    return email_service.send_email(db, req)


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

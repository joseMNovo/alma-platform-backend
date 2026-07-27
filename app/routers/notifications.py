from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from typing import List, Optional

from app.database import get_db
from app.models.notification import Notification
from app.models.announcement import Announcement
from app.schemas.notification import (
    NotificationOut,
    NotificationCreate,
    UnreadCountOut,
    BroadcastRequest,
    BroadcastResult,
)
from app.services.notification_service import broadcast as broadcast_service, notify_user, send_broadcast_push
from app.utils.logger import log_info, log_error

router = APIRouter()

_USER_TYPES = {"voluntario", "participante"}


def _validate_user_type(user_type: str) -> None:
    if user_type not in _USER_TYPES:
        raise HTTPException(status_code=422, detail=f"user_type inválido: {user_type}")


# ── Lectura (la campanita) ─────────────────────────────────────────────

@router.get("/", response_model=List[NotificationOut])
def list_notifications(
    user_type: str = Query(...),
    user_id: int = Query(...),
    limit: int = Query(30, le=100),
    db: Session = Depends(get_db),
):
    """Notificaciones de un usuario, más recientes primero."""
    _validate_user_type(user_type)
    return (
        db.query(Notification)
        .filter(Notification.user_type == user_type, Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(
    user_type: str = Query(...),
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Cantidad de no leídas (el badge de la campanita). Llamada por el polling."""
    _validate_user_type(user_type)
    count = (
        db.query(func.count(Notification.id))
        .filter(
            Notification.user_type == user_type,
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
        .scalar()
    )
    return {"unread": int(count or 0)}


# ── Escritura ──────────────────────────────────────────────────────────

@router.post("/", response_model=NotificationOut, status_code=201)
def create_notification(data: NotificationCreate, db: Session = Depends(get_db)):
    """Crea una notificación in-app SIN enviar push (uso interno/manual).

    Para avisar con push usá el servicio notify_user() desde el backend.
    """
    try:
        notif = Notification(**data.model_dump())
        db.add(notif)
        db.commit()
        db.refresh(notif)
        return notif
    except Exception:
        db.rollback()
        log_error("Error al crear notificación", module="notifications", action="create", exc_info=True)
        raise


@router.post("/notify", response_model=NotificationOut, status_code=201)
def notify(data: NotificationCreate, db: Session = Depends(get_db)):
    """Crea la notificación in-app de UN usuario Y le dispara el push.

    Lo consume el cron de recordatorios (alma_cron.py) por HTTP, igual que
    /emails/send. El push va envuelto en el servicio: nunca hace fallar esto.
    """
    try:
        return notify_user(
            db,
            user_type=data.user_type,
            user_id=data.user_id,
            title=data.title,
            body=data.body or "",
            kind=data.kind,
            url=data.url,
        )
    except Exception:
        db.rollback()
        log_error("Error en notify", module="notifications", action="notify", exc_info=True)
        raise


@router.post("/broadcast", response_model=BroadcastResult, status_code=201)
def broadcast(data: BroadcastRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Lanza una notificación a una audiencia (campanita + push).

    Responde apenas quedan guardadas las campanitas. Los push salen DESPUÉS,
    en background: son llamadas HTTPS a servicios ajenos (FCM/Apple) y
    esperarlas dentro del request hacía que el endpoint tardara segundos por
    destinatario y terminara en timeout. Nadie se pierde el aviso: la
    notificación in-app ya está persistida cuando esto devuelve.

    Solo lo consume el BFF de Next, que ya validó que sea admin. Si
    also_popup=True, además crea un anuncio (popup al ingresar).
    """
    try:
        result = broadcast_service(
            db,
            title=data.title,
            body=data.body,
            audience=data.audience,
            url=data.url,
            volunteer_ids=data.volunteer_ids,
        )
        targets = result.pop("targets", [])

        popup_created = False
        if data.also_popup:
            db.add(Announcement(title=data.title, body=data.body or data.title, audience=data.audience))
            db.commit()
            popup_created = True

        if targets:
            background_tasks.add_task(
                send_broadcast_push,
                targets,
                data.title,
                data.body or "",
                data.url,
            )

        return {**result, "popup_created": popup_created}
    except Exception:
        db.rollback()
        log_error("Error en broadcast", module="notifications", action="broadcast", exc_info=True)
        raise


@router.post("/mark-read", status_code=204)
def mark_read(
    user_type: str = Query(...),
    user_id: int = Query(...),
    id: Optional[int] = Query(None, description="ID puntual; si se omite, marca TODAS"),
    db: Session = Depends(get_db),
):
    """Marca como leída una notificación puntual, o todas las del usuario."""
    _validate_user_type(user_type)
    try:
        q = db.query(Notification).filter(
            Notification.user_type == user_type,
            Notification.user_id == user_id,
            Notification.is_read.is_(False),
        )
        if id is not None:
            q = q.filter(Notification.id == id)
        q.update({"is_read": True, "read_at": datetime.now()}, synchronize_session=False)
        db.commit()
        log_info("Notificaciones marcadas leídas", module="notifications", action="mark_read", user=user_id, meta={"id": id})
    except Exception:
        db.rollback()
        log_error("Error al marcar leídas", module="notifications", action="mark_read", exc_info=True)
        raise

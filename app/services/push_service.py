"""
push_service.py — Envío de Web Push (VAPID) a los dispositivos suscritos.

Usa pywebpush. Las suscripciones "muertas" (404/410) se borran solas.
Si no hay claves VAPID configuradas, el envío es un no-op silencioso (la
feature queda dormida sin romper nada).
"""
import json
from typing import Optional

from sqlalchemy.orm import Session

from config import settings
from app.models.push_subscription import PushSubscription
from app.utils.logger import log_info, log_warn, log_error

try:
    from pywebpush import webpush, WebPushException
    _PYWEBPUSH_AVAILABLE = True
except Exception:  # pragma: no cover - dependencia opcional hasta instalarla
    _PYWEBPUSH_AVAILABLE = False


def _vapid_configured() -> bool:
    return bool(settings.VAPID_PRIVATE_KEY and settings.VAPID_SUBJECT)


def send_push_to_user(
    db: Session,
    user_type: str,
    user_id: int,
    title: str,
    body: str = "",
    url: Optional[str] = None,
) -> int:
    """
    Envía un push a TODOS los dispositivos suscritos de un usuario.
    Devuelve la cantidad de envíos exitosos. Nunca lanza excepción hacia
    afuera: cualquier fallo se loguea (para no arrastrar al proceso que lo
    llama, ej. el envío de emails del cron).
    """
    if not _PYWEBPUSH_AVAILABLE:
        log_warn("pywebpush no instalado; push omitido", module="push", action="send")
        return 0
    if not _vapid_configured():
        log_warn("VAPID no configurado; push omitido", module="push", action="send")
        return 0

    subs = (
        db.query(PushSubscription)
        .filter(PushSubscription.user_type == user_type, PushSubscription.user_id == user_id)
        .all()
    )
    if not subs:
        return 0

    payload = json.dumps({"title": title, "body": body, "url": url or "/"})
    vapid_claims = {"sub": settings.VAPID_SUBJECT}
    sent = 0
    dead_ids: list[int] = []

    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims=dict(vapid_claims),
                ttl=86400,
            )
            sent += 1
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            # 404/410 = suscripción muerta: la marcamos para borrar.
            if status in (404, 410):
                dead_ids.append(sub.id)
            else:
                log_warn(
                    "Fallo al enviar push",
                    module="push",
                    action="send",
                    user=user_id,
                    meta={"status": status, "endpoint": sub.endpoint[:60]},
                )
        except Exception:
            log_error("Error inesperado enviando push", module="push", action="send", user=user_id, exc_info=True)

    if dead_ids:
        try:
            db.query(PushSubscription).filter(PushSubscription.id.in_(dead_ids)).delete(synchronize_session=False)
            db.commit()
            log_info("Suscripciones push muertas eliminadas", module="push", action="cleanup", meta={"count": len(dead_ids)})
        except Exception:
            db.rollback()
            log_error("Error limpiando suscripciones muertas", module="push", action="cleanup", exc_info=True)

    return sent

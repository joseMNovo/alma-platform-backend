"""
push_service.py — Envío de Web Push (VAPID) a los dispositivos suscritos.

Usa pywebpush. Las suscripciones "muertas" (404/410) se borran solas.
Si no hay claves VAPID configuradas, el envío es un no-op silencioso (la
feature queda dormida sin romper nada).
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
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


# Segundos que se espera a cada servicio de push (FCM, Apple, Mozilla).
# CRÍTICO: pywebpush usa `requests` y SIN timeout una sola suscripción colgada
# bloquea el proceso para siempre. Con esto, lo peor que pasa son 8 segundos.
PUSH_TIMEOUT_SECONDS = 8

# Envíos simultáneos. Es I/O puro (esperar a un servidor ajeno), así que los
# hilos sirven aunque exista el GIL: mientras uno espera, los otros avanzan.
PUSH_MAX_WORKERS = 12


def _send_one(sub_info: dict, payload: str) -> tuple[int, Optional[int]]:
    """Envía a UN dispositivo. Devuelve (enviados, status_si_falló).

    No toca la base: recibe un dict plano justamente para poder correr en otro
    hilo sin compartir la sesión de SQLAlchemy (que no es thread-safe).
    """
    try:
        webpush(
            subscription_info={
                "endpoint": sub_info["endpoint"],
                "keys": {"p256dh": sub_info["p256dh"], "auth": sub_info["auth"]},
            },
            data=payload,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},
            ttl=86400,
            timeout=PUSH_TIMEOUT_SECONDS,
        )
        return 1, None
    except WebPushException as e:
        return 0, getattr(getattr(e, "response", None), "status_code", None)
    except Exception:
        # Timeout, DNS caído, etc. No es una suscripción muerta: no se borra.
        return 0, None


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
    sent = 0
    dead_ids: list[int] = []

    # Los datos se copian a dicts ANTES de repartir el trabajo: la sesión de
    # SQLAlchemy no es thread-safe y no puede cruzar a los hilos.
    jobs = [
        ({"endpoint": s.endpoint, "p256dh": s.p256dh, "auth": s.auth}, s.id, s.endpoint)
        for s in subs
    ]

    # En paralelo: N dispositivos tardan lo que el más lento, no la suma.
    with ThreadPoolExecutor(max_workers=min(PUSH_MAX_WORKERS, len(jobs))) as pool:
        futures = {pool.submit(_send_one, info, payload): (sub_id, endpoint) for info, sub_id, endpoint in jobs}
        for future in as_completed(futures):
            sub_id, endpoint = futures[future]
            try:
                ok, status = future.result()
            except Exception:
                log_error("Error inesperado enviando push", module="push", action="send", user=user_id, exc_info=True)
                continue

            sent += ok
            # 404/410 = suscripción muerta (desinstaló la PWA, revocó permisos).
            if status in (404, 410):
                dead_ids.append(sub_id)
            elif not ok:
                log_warn(
                    "Fallo al enviar push",
                    module="push",
                    action="send",
                    user=user_id,
                    meta={"status": status, "endpoint": endpoint[:60]},
                )

    if dead_ids:
        try:
            db.query(PushSubscription).filter(PushSubscription.id.in_(dead_ids)).delete(synchronize_session=False)
            db.commit()
            log_info("Suscripciones push muertas eliminadas", module="push", action="cleanup", meta={"count": len(dead_ids)})
        except Exception:
            db.rollback()
            log_error("Error limpiando suscripciones muertas", module="push", action="cleanup", exc_info=True)

    return sent

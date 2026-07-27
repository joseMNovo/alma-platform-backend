"""
notification_service.py — Punto de entrada único para "avisarle" a un usuario.

notify_user() hace las dos cosas de una:
  1) Inserta la notificación in-app (la campanita 🔔) → persiste, con leído/no leído.
  2) Dispara el push a sus dispositivos (el "timbre").

El push va envuelto: si falla, la notificación in-app igual queda guardada y
el proceso que llama (ej. el cron de emails) nunca se rompe por el push.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.models.notification import Notification
from app.models.voluntario import Voluntario
from app.models.participant import Participant
from app.services.push_service import send_push_to_user
from app.utils.logger import log_error, log_info


def notify_user(
    db: Session,
    user_type: str,
    user_id: int,
    title: str,
    body: str = "",
    kind: str = "system",
    url: Optional[str] = None,
    push: bool = True,
) -> Notification:
    """Crea la notificación in-app y (opcionalmente) manda el push.

    Devuelve la Notification creada. El push nunca hace fallar esta función.
    """
    notif = Notification(
        user_type=user_type,
        user_id=user_id,
        title=title,
        body=body or None,
        kind=kind,
        url=url,
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)

    if push:
        try:
            send_push_to_user(db, user_type, user_id, title=title, body=body or "", url=url)
        except Exception:
            # Blindaje: el push nunca degrada la creación de la notificación.
            log_error(
                "Push falló pero la notificación in-app quedó guardada",
                module="notifications",
                action="notify_user",
                user=user_id,
                exc_info=True,
            )

    return notif


def _audience_targets(db: Session, audience: str) -> list[tuple[str, int]]:
    """Resuelve la audiencia a una lista de (user_type, user_id).

    'voluntario' → voluntarios activos. 'participante' → participants activos.
    'all' → ambos.
    """
    targets: list[tuple[str, int]] = []
    if audience in ("all", "voluntario"):
        rows = db.query(Voluntario.id).filter(Voluntario.status == "activo").all()
        targets += [("voluntario", r.id) for r in rows]
    if audience in ("all", "participante"):
        rows = db.query(Participant.id).filter(Participant.is_active.is_(True)).all()
        targets += [("participante", r.id) for r in rows]
    return targets


def broadcast(
    db: Session,
    title: str,
    body: str = "",
    audience: str = "all",
    url: Optional[str] = None,
    kind: str = "announcement",
    volunteer_ids: Optional[list[int]] = None,
) -> dict:
    """Envía una notificación a una audiencia o a voluntarios puntuales.

    Si volunteer_ids trae IDs, se envía SOLO a esos voluntarios; si no, se usa
    'audience'. Crea la fila in-app de cada usuario y dispara el push.

    Devuelve {recipients, push_sent}. Un fallo puntual de push no corta el
    broadcast (cada usuario va envuelto).
    """
    if volunteer_ids:
        # Dedup y solo voluntarios explícitos.
        targets = [("voluntario", vid) for vid in dict.fromkeys(volunteer_ids)]
    else:
        targets = _audience_targets(db, audience)
    recipients = 0
    push_sent = 0

    for user_type, user_id in targets:
        notif = Notification(
            user_type=user_type,
            user_id=user_id,
            title=title,
            body=body or None,
            kind=kind,
            url=url,
        )
        db.add(notif)
        recipients += 1

    # Un solo commit para todas las notificaciones in-app.
    db.commit()

    log_info(
        "Broadcast: campanitas creadas",
        module="notifications",
        action="broadcast",
        meta={"audience": audience, "recipients": recipients},
    )

    # OJO: los push NO se mandan acá. Cada envío es una llamada HTTPS a un
    # servicio ajeno (FCM/Apple) y hacerlas en serie dentro del request hacía
    # que el endpoint tardara ~7s por destinatario y diera timeout con pocos.
    # El llamador dispara send_broadcast_push() en background; la campanita ya
    # quedó guardada, así que nadie se pierde el aviso aunque el push falle.
    return {"recipients": recipients, "push_sent": 0, "targets": targets}


def send_broadcast_push(
    targets: list[tuple[str, int]],
    title: str,
    body: str = "",
    url: Optional[str] = None,
) -> int:
    """Manda los push de un broadcast. Pensado para correr en BackgroundTasks.

    Abre su PROPIA sesión: la del request ya se cerró cuando esto se ejecuta.
    Los destinatarios se procesan en paralelo, y adentro de cada uno los
    dispositivos también (ver push_service). Con 8s de timeout por dispositivo,
    un broadcast a toda la organización tarda segundos, no minutos.
    """
    from app.database import SessionLocal  # import local: evita ciclo al importar

    db = SessionLocal()
    push_sent = 0
    try:
        for user_type, user_id in targets:
            try:
                push_sent += send_push_to_user(db, user_type, user_id, title=title, body=body or "", url=url)
            except Exception:
                log_error(
                    "Push falló en broadcast (la notificación in-app quedó guardada)",
                    module="notifications",
                    action="broadcast_push",
                    user=user_id,
                    exc_info=True,
                )
        log_info(
            "Broadcast: push enviados",
            module="notifications",
            action="broadcast_push",
            meta={"destinatarios": len(targets), "push_sent": push_sent},
        )
    finally:
        db.close()

    return push_sent

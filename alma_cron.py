#!/usr/bin/env python3
"""
alma_cron.py — Recordatorios por email a voluntarios asignados a eventos del calendario.

Se ejecuta 1 vez por día, todos los días a las 06:00 AM (hora del servidor), desde cron
en la VPS Linux. Entrada de crontab:

    # Todos los días a las 06:00 AM (hora del servidor)
    0 6 * * *  cd /ruta/alma-platform-backend && /ruta/venv/bin/python alma_cron.py >> /var/log/alma_cron.out 2>&1

Lógica:
  1. Toma la fecha de hoy.
  2. Trae los eventos futuros con notify_enabled = 1, sus offsets de recordatorio
     (reminder_offsets: p.ej. [7, 1, 0]) y los voluntarios asignados (con email).
  3. Para cada (evento × voluntario × offset) calcula la fecha de envío
     send_on = fecha_evento - offset. Si hoy >= send_on (y el evento no pasó),
     el recordatorio está "vencido" y se manda.
  4. La tabla reminder_sent_log evita reenvíos: cada (evento, voluntario, offset)
     se manda una sola vez (idempotente ante doble corrida o días salteados).
  5. El envío se hace llamando al endpoint interno POST /emails/send del backend
     (mismo Resend + email_log que el resto del sistema). NO manda mail directo.

Todo queda logueado en logs/cron/alma_cron.log y en stdout.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pymysql

from config import settings

# ── Configuración ──────────────────────────────────────────────────────────

# Etiqueta legible para cada offset (días antes del evento)
OFFSET_LABELS: dict[int, str] = {
    7: "en una semana",
    1: "mañana",
    0: "hoy",
}

# Nombre legible por tipo de evento
TYPE_LABELS: dict[str, str] = {
    "grupo": "Grupo de apoyo",
    "taller": "Taller",
    "actividad": "Actividad",
}

EMAIL_ENDPOINT = f"http://{settings.API_HOST}:{settings.API_PORT}/emails/send"
NOTIFY_ENDPOINT = f"http://{settings.API_HOST}:{settings.API_PORT}/notifications/notify"
EMAIL_TEMPLATE = "event_assignment"
REQUEST_TIMEOUT = 20  # segundos


# ── Logging ────────────────────────────────────────────────────────────────

LOG_DIR = Path(__file__).resolve().parent / "logs" / "cron"
LOG_FILE = LOG_DIR / "alma_cron.log"


def _build_logger() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("alma_cron")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


log = _build_logger()


# ── Base de datos ──────────────────────────────────────────────────────────

def get_connection() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def fetch_candidates(conn, today: date) -> list[dict]:
    """
    Eventos futuros (o de hoy) con notificación activada, junto con cada voluntario
    asignado que tenga email. Incluye coordinador, co-coordinador y la lista N
    (cualquier fila en calendar_assignments).
    """
    sql = """
        SELECT
            ci.id            AS event_id,
            ci.type          AS event_type,
            ci.date          AS event_date,
            ci.start_time    AS start_time,
            ci.notes         AS notes,
            ci.reminder_offsets AS reminder_offsets,
            ci.created_at    AS created_at,
            ca.role          AS role,
            v.id             AS volunteer_id,
            v.name           AS name,
            v.last_name      AS last_name,
            v.email          AS email
        FROM calendar_instances ci
        JOIN calendar_assignments ca ON ca.instance_id = ci.id
        JOIN voluntarios v           ON v.id = ca.volunteer_id
        WHERE ci.notify_enabled = 1
          AND ci.date >= %s
          AND v.email IS NOT NULL
          AND v.email <> ''
        ORDER BY ci.date ASC, ci.id ASC
    """
    with conn.cursor() as cur:
        cur.execute(sql, (today,))
        return cur.fetchall()


def parse_offsets(raw) -> list[int]:
    """reminder_offsets puede venir como str JSON o como list (según driver)."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        values = raw
    else:
        try:
            values = json.loads(raw)
        except (ValueError, TypeError):
            log.warning("reminder_offsets no parseable: %r", raw)
            return []
    out: list[int] = []
    for v in values:
        try:
            out.append(int(v))
        except (ValueError, TypeError):
            continue
    return out


def already_sent(conn, event_id: int, volunteer_id: int, offset: int) -> bool:
    """
    Reserva el recordatorio de forma atómica. INSERT IGNORE: si la fila ya existía
    (UNIQUE event_id+volunteer_id+offset_days), rowcount == 0 -> ya se mandó.
    """
    sql = """
        INSERT IGNORE INTO reminder_sent_log (event_id, volunteer_id, offset_days, sent_on)
        VALUES (%s, %s, %s, %s)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (event_id, volunteer_id, offset, date.today()))
        inserted = cur.rowcount
    conn.commit()
    return inserted == 0


def release_reservation(conn, event_id: int, volunteer_id: int, offset: int) -> None:
    """Si el envío falla, liberamos la reserva para reintentar en la próxima corrida."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM reminder_sent_log WHERE event_id=%s AND volunteer_id=%s AND offset_days=%s",
            (event_id, volunteer_id, offset),
        )
    conn.commit()


# ── Envío de email ─────────────────────────────────────────────────────────

ALERT_EMAIL = "manunovo@gmail.com"


def send_cron_alert(reason: str, error_message: str, tb: str = "") -> None:
    """Avisa por email si el cron falla, para no depender de mirar los logs.

    Usa el mismo endpoint /emails/send que los recordatorios. Limitación
    honesta: si el backend está caído, esta alerta tampoco puede salir (viaja
    por el mismo canal). Cubre el 99% de los casos (bug, timeout, error de DB
    con el backend arriba). Nunca lanza: una alerta que falla no debe tapar el
    error original.
    """
    try:
        payload = {
            "to": [ALERT_EMAIL],
            "subject": "⚠️ Falló el cron de recordatorios — ALMA",
            "template": "cron_alert",
            "variables": {
                "reason": reason,
                "error_message": error_message or "(sin mensaje)",
                "traceback": (tb or "Sin traceback.")[-3000:],
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            "sent_by_volunteer_id": None,
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            EMAIL_ENDPOINT,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "X-API-Key": settings.INTERNAL_API_KEY},
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            log.info("Alerta de fallo del cron enviada a %s (status %s)", ALERT_EMAIL, resp.status)
    except Exception:
        log.exception("No se pudo enviar la alerta de fallo del cron")


def send_reminder(row: dict, offset: int) -> bool:
    event_date: date = row["event_date"]
    type_label = TYPE_LABELS.get(row["event_type"], row["event_type"])
    when_label = OFFSET_LABELS.get(offset, f"en {offset} días")
    start_time = str(row["start_time"])[:5] if row["start_time"] is not None else ""

    full_name = f"{row['name']} {row['last_name'] or ''}".strip()

    variables = {
        "name": row["name"] or "",
        "event_label": type_label,
        "event_date": event_date.strftime("%d/%m/%Y"),
        "event_time": start_time,
        "when_label": when_label,
        "notes": row["notes"] or "",
        "app_url": settings.APP_BASE_URL,
    }

    subject = f"Recordatorio: {type_label} {when_label} ({event_date.strftime('%d/%m')})"

    payload = {
        "to": [row["email"]],
        "subject": subject,
        "template": EMAIL_TEMPLATE,
        "variables": variables,
        "sent_by_volunteer_id": None,
    }

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        EMAIL_ENDPOINT,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": settings.INTERNAL_API_KEY,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            status_code = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        log.error(
            "Endpoint /emails/send devolvió %s para %s (evento %s, offset %s): %s",
            exc.code, row["email"], row["event_id"], offset, body,
        )
        return False
    except urllib.error.URLError as exc:
        log.error(
            "Fallo de red al enviar a %s (evento %s, offset %s): %s",
            row["email"], row["event_id"], offset, exc,
        )
        return False
    except TimeoutError as exc:
        # En Python 3.12, si el timeout ocurre LEYENDO la respuesta (la conexión
        # ya se abrió pero Resend tardó), TimeoutError se propaga SIN envolverse
        # en URLError. Antes escapaba al except del bucle y mataba toda la corrida.
        # Con este catch, el fallo queda aislado a ese envío y el cron sigue.
        log.error(
            "Timeout al enviar a %s (evento %s, offset %s): %s",
            row["email"], row["event_id"], offset, exc,
        )
        return False

    if status_code != 201:
        log.error(
            "Endpoint /emails/send devolvió %s para %s (evento %s, offset %s)",
            status_code, row["email"], row["event_id"], offset,
        )
        return False

    log.info(
        "Enviado a %s <%s> | evento %s (%s %s) | recordatorio %s",
        full_name, row["email"], row["event_id"], type_label,
        event_date.strftime("%d/%m/%Y"), when_label,
    )
    return True


def send_reminder_push(row: dict, offset: int) -> None:
    """Envía el MISMO recordatorio como notificación push + campanita al voluntario.

    Best-effort y totalmente aislado del email: cualquier fallo se loguea y se
    ignora. No afecta contadores ni el flujo de recordatorios por email.
    """
    event_date: date = row["event_date"]
    type_label = TYPE_LABELS.get(row["event_type"], row["event_type"])
    when_label = OFFSET_LABELS.get(offset, f"en {offset} días")
    start_time = str(row["start_time"])[:5] if row["start_time"] is not None else ""

    title = f"Recordatorio: {type_label} {when_label}"
    body_parts = [f"{event_date.strftime('%d/%m/%Y')}"]
    if start_time:
        body_parts.append(f"{start_time} hs")
    body = " · ".join(body_parts)

    payload = {
        "user_type": "voluntario",
        "user_id": row["volunteer_id"],
        "title": title,
        "body": body,
        "kind": "calendar_reminder",
        "url": "/calendarios",
    }

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        NOTIFY_ENDPOINT,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": settings.INTERNAL_API_KEY,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 201:
                log.warning(
                    "Push recordatorio devolvió %s | evento %s | voluntario %s",
                    resp.status, row["event_id"], row["volunteer_id"],
                )
    except Exception as exc:  # noqa: BLE001 — el push nunca debe romper el cron
        log.warning(
            "No se pudo enviar push recordatorio | evento %s | voluntario %s | %s",
            row["event_id"], row["volunteer_id"], exc,
        )


# ── Orquestación ───────────────────────────────────────────────────────────

def run() -> int:
    today = date.today()
    log.info("=== alma_cron iniciado | hoy = %s | log = %s ===", today.isoformat(), LOG_FILE)

    sent = 0
    skipped = 0
    failed = 0
    due = 0  # recordatorios que vencieron hoy o antes (corresponde actuar sobre ellos)

    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001
        log.error("No se pudo conectar a la base de datos: %s", exc)
        return 1

    try:
        candidates = fetch_candidates(conn, today)
        log.info("Candidatos (evento × voluntario con notificación activa): %d", len(candidates))

        for row in candidates:
            offsets = parse_offsets(row["reminder_offsets"])
            if not offsets:
                continue

            event_date: date = row["event_date"]
            created_at = row.get("created_at")
            created_date = created_at.date() if isinstance(created_at, datetime) else None
            for offset in offsets:
                send_on = event_date - timedelta(days=offset)

                # Aún no llegó el momento de este recordatorio
                if today < send_on:
                    continue

                # La ventana de este recordatorio cayó ANTES de que el evento se creara:
                # quedó obsoleta (evento cargado tarde). No se manda. Evita, p. ej., que un
                # evento creado el jueves dispare el recordatorio de "7 días antes" del miércoles.
                if created_date is not None and send_on < created_date:
                    log.info(
                        "OMITIDO (ventana previa a la creación) | evento %s | offset %s | send_on %s < creado %s",
                        row["event_id"], offset, send_on.isoformat(), created_date.isoformat(),
                    )
                    continue

                # Vencido (today >= send_on) y el evento no pasó (garantizado por el WHERE).
                due += 1
                when_label = OFFSET_LABELS.get(offset, f"{offset} días antes")

                # Reservamos de forma atómica para no duplicar.
                if already_sent(conn, row["event_id"], row["volunteer_id"], offset):
                    skipped += 1
                    log.info(
                        "OMITIDO (ya enviado) | evento %s | voluntario %s <%s> | recordatorio %s",
                        row["event_id"], row["volunteer_id"], row["email"], when_label,
                    )
                    continue

                if send_reminder(row, offset):
                    sent += 1
                    # El push va DESPUÉS del email y aislado: si falla, el
                    # recordatorio por email ya salió y quedó registrado igual.
                    send_reminder_push(row, offset)
                else:
                    release_reservation(conn, row["event_id"], row["volunteer_id"], offset)
                    failed += 1

        if due == 0:
            log.info("No hay recordatorios vencidos para hoy. Nada para enviar.")

    except Exception as exc:  # noqa: BLE001
        log.exception("Error inesperado durante la corrida: %s", exc)
        # Falla catastrófica (la corrida se cortó): alerta con el traceback completo.
        send_cron_alert("La corrida del cron se cortó por un error inesperado.", str(exc), traceback.format_exc())
        return 1
    finally:
        conn.close()

    # Falla parcial: la corrida terminó, pero algunos envíos fallaron (timeouts,
    # etc.). Avisamos igual para tener visibilidad, sin traceback (no hubo crash).
    if failed > 0:
        send_cron_alert(
            f"La corrida terminó, pero {failed} recordatorio(s) no se pudieron enviar.",
            f"Vencidos={due}, enviados={sent}, omitidos={skipped}, fallidos={failed}.",
        )

    log.info(
        "=== alma_cron finalizado | vencidos=%d → enviados=%d, omitidos(ya enviados)=%d, fallidos=%d ===",
        due, sent, skipped, failed,
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())

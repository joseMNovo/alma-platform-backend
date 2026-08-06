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
     Ese endpoint espera a Resend, así que el timeout de acá tiene que ser
     generoso (ver EMAIL_REQUEST_TIMEOUT) y, si igual no hay respuesta, el
     recordatorio NO se reintenta: ver send_reminder().

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
from app.utils.timezone import today_ar, to_ar_date

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

# /emails/send espera a Resend antes de contestar, así que este timeout tiene que
# cubrir la latencia de un tercero, no la nuestra. Con 20s se pasaba seguido: el
# cron daba el envío por fallido, liberaba la reserva y mandaba el recordatorio
# duplicado al día siguiente, aunque el mail original SÍ había salido. Resend
# normalmente responde en menos de un segundo; si tarda más de 90 está roto de
# verdad. Correr a las 06:00 y en serie hace que esperar de más no le moleste a
# nadie: el costo de un timeout falso es mucho más caro que el de la espera.
EMAIL_REQUEST_TIMEOUT = 90  # segundos

# Resultado de intentar mandar un recordatorio. La diferencia entre REJECTED y
# UNKNOWN es la que evita los duplicados: solo se reintenta cuando SABEMOS que no
# salió ningún mail.
ACCEPTED = "accepted"  # 201: el mail salió y quedó en email_logs.
REJECTED = "rejected"  # el backend contestó un error: no salió nada, se puede reintentar.
UNKNOWN = "unknown"    # sin respuesta (timeout): puede haber salido igual. NO se reintenta.


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


# ── Formato de los mensajes ────────────────────────────────────────────────
# Estos logs los lee una persona a la mañana, no una máquina. La regla es que
# cada línea se entienda sola, sin saber cómo funciona el cron por dentro.

DAY_NAMES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def _human_date(d: date) -> str:
    """'lunes 28/07/2026'."""
    return f"{DAY_NAMES[d.weekday()]} {d.strftime('%d/%m/%Y')}"


def _describe(row: dict, offset: int) -> str:
    """Una línea que identifica el recordatorio sin jerga ni IDs.

    Ej.: 'Victoria Rébori — Taller 29/07 (es mañana)'.
    """
    full_name = f"{row['name']} {row['last_name'] or ''}".strip()
    type_label = TYPE_LABELS.get(row["event_type"], row["event_type"])
    when_label = OFFSET_LABELS.get(offset, f"en {offset} días")
    return f"{full_name} — {type_label} {row['event_date'].strftime('%d/%m')} (es {when_label})"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


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
        cur.execute(sql, (event_id, volunteer_id, offset, today_ar()))
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
        with urllib.request.urlopen(request, timeout=EMAIL_REQUEST_TIMEOUT):
            log.info("Te avisé del problema por mail a %s.", ALERT_EMAIL)
    except Exception:
        log.exception("Tampoco pude mandarte el mail de aviso")


def send_reminder(row: dict, offset: int) -> tuple[str, str]:
    """Manda el recordatorio. Devuelve (resultado, motivo).

    resultado es ACCEPTED / REJECTED / UNKNOWN; motivo es un texto corto para el
    log cuando algo salió mal (vacío si salió bien). Los tres estados existen
    para no mandar nunca un recordatorio duplicado: la reserva de
    reminder_sent_log solo se libera con REJECTED, el único caso donde SABEMOS
    que no salió ningún mail.

    Quien loguea el resultado es run(), para que todas las líneas salgan con el
    mismo formato.
    """
    event_date: date = row["event_date"]
    type_label = TYPE_LABELS.get(row["event_type"], row["event_type"])
    when_label = OFFSET_LABELS.get(offset, f"en {offset} días")
    start_time = str(row["start_time"])[:5] if row["start_time"] is not None else ""

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
        with urllib.request.urlopen(request, timeout=EMAIL_REQUEST_TIMEOUT) as resp:
            status_code = resp.status
            raw_body = resp.read()
    except urllib.error.HTTPError as exc:
        # El backend contestó con un código de error: el envío no prosperó y no
        # salió ningún mail. Reintentar mañana es seguro.
        detail = exc.read().decode("utf-8", "replace")[:200].strip()
        return REJECTED, f"el servidor respondió {exc.code}" + (f" ({detail})" if detail else "")
    except urllib.error.URLError as exc:
        # Un timeout de conexión llega envuelto acá; el resto (conexión rechazada,
        # DNS) son fallas donde el pedido nunca se procesó.
        if isinstance(exc.reason, TimeoutError):
            return UNKNOWN, "el servidor no respondió a tiempo al conectar"
        return REJECTED, f"no me pude conectar al servidor ({exc.reason})"
    except TimeoutError:
        # En Python 3.12, si el timeout ocurre LEYENDO la respuesta, TimeoutError
        # se propaga SIN envolverse en URLError. Antes escapaba al except del
        # bucle y mataba toda la corrida; con este catch queda aislado al envío.
        #
        # Este caso era LA causa del problema: el timeout saltaba mientras Resend
        # todavía estaba procesando, el cron liberaba la reserva de un mail que sí
        # había salido, y al día siguiente lo mandaba de nuevo. Por eso es UNKNOWN
        # y no un fallo: la reserva queda puesta, así que en el peor caso falta un
        # recordatorio (avisado por alerta y visible en email_logs), pero nunca
        # llega uno duplicado. El timeout largo hace que esto sea raro de entrada.
        return UNKNOWN, f"el servidor no respondió en {EMAIL_REQUEST_TIMEOUT}s"

    if status_code != 201:
        return REJECTED, f"el servidor respondió {status_code}"

    # OJO: /emails/send devuelve 201 AUNQUE Resend rechace el mail — el 201 dice
    # "quedó registrado en email_logs", no "salió". El resultado real viene en el
    # campo `status` de esa fila. Sin mirarlo, el cron cantaba "enviado" para
    # mails que nunca llegaron a destino.
    try:
        body = json.loads(raw_body)
    except (ValueError, TypeError):
        body = {}

    if body.get("status") == "failed":
        motivo = (body.get("error_message") or "").strip()[:200]
        return REJECTED, f"Resend lo rechazó" + (f": {motivo}" if motivo else "")

    return ACCEPTED, ""


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
                log.warning("  (el mail salió, pero la notificación al celular no: respondió %s)", resp.status)
    except Exception as exc:  # noqa: BLE001 — el push nunca debe romper el cron
        log.warning("  (el mail salió, pero la notificación al celular no: %s)", exc)


# ── Recordatorios de PARTICIPANTES ─────────────────────────────────────────
#
# Los de arriba son para voluntarios: los prende el admin por evento con
# `calendar_instances.reminder_offsets`, y salen para todos los asignados.
#
# Estos son distintos: los pide CADA PERSONA para CADA evento al que se
# anotó (participant_event_reminders). Por eso no miran `notify_enabled` —
# ese switch es del admin y gobierna a los voluntarios. Acá el pedido de la
# persona ES el permiso.
#
# Registro de envíos aparte (participant_reminder_sent_log) para no tocar el
# de voluntarios, que es el que ya viene funcionando.

def fetch_participant_candidates(conn, today: date) -> list[dict]:
    """Eventos futuros con los recordatorios que pidió cada participante.

    Se descarta a quien no tenga email: sin dirección no hay nada que mandar,
    y contarlo como "fallido" ensuciaría el resumen todos los días.
    """
    sql = """
        SELECT
            ci.id            AS event_id,
            ci.type          AS event_type,
            ci.date          AS event_date,
            ci.start_time    AS start_time,
            ci.notes         AS notes,
            ci.created_at    AS created_at,
            r.offsets        AS reminder_offsets,
            p.id             AS person_id,
            p.participant_id AS participant_id,
            p.name           AS name,
            p.last_name      AS last_name,
            p.email          AS email
        FROM participant_event_reminders r
        JOIN calendar_instances ci    ON ci.id = r.calendar_instance_id
        JOIN participant_profiles p   ON p.id = r.person_id
        WHERE ci.date >= %s
          AND p.email IS NOT NULL
          AND p.email <> ''
        ORDER BY ci.date ASC, ci.id ASC
    """
    with conn.cursor() as cur:
        cur.execute(sql, (today,))
        return cur.fetchall()


def already_sent_participant(conn, event_id: int, person_id: int, offset: int) -> bool:
    """Reserva atómica: devuelve True si ESTE aviso ya se había mandado.

    Igual que con los voluntarios, el INSERT IGNORE contra la clave única es
    lo que hace idempotente al cron: si corre dos veces el mismo día, o si se
    saltea un día, cada aviso sale UNA sola vez.
    """
    sql = """
        INSERT IGNORE INTO participant_reminder_sent_log (event_id, person_id, offset_days, sent_on)
        VALUES (%s, %s, %s, %s)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (event_id, person_id, offset, today_ar()))
        inserted = cur.rowcount
    conn.commit()
    return inserted == 0


def release_reservation_participant(conn, event_id: int, person_id: int, offset: int) -> None:
    """Si el envío falla, se libera la reserva para reintentar mañana."""
    sql = """
        DELETE FROM participant_reminder_sent_log
        WHERE event_id = %s AND person_id = %s AND offset_days = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (event_id, person_id, offset))
    conn.commit()


def run_participantes(conn, today: date) -> dict:
    """Corre la tanda de participantes. Devuelve los contadores para el resumen.

    Comparte send_reminder() con los voluntarios: las filas se arman con las
    mismas claves a propósito, así el texto del mail es uno solo y no se
    desincroniza.
    """
    contadores = {"sent": 0, "skipped": 0, "failed": 0, "unknown": 0}

    candidates = fetch_participant_candidates(conn, today)
    if not candidates:
        log.info("Participantes: nadie pidió recordatorios para eventos futuros.")
        return contadores

    log.info(
        "Participantes: reviso %s en %s.",
        _plural(len({r["person_id"] for r in candidates}), "persona", "personas"),
        _plural(len({r["event_id"] for r in candidates}), "evento", "eventos"),
    )

    for row in candidates:
        offsets = parse_offsets(row["reminder_offsets"])
        if not offsets:
            continue

        event_date: date = row["event_date"]
        created_at = row.get("created_at")
        created_date = to_ar_date(created_at) if isinstance(created_at, datetime) else None

        for offset in offsets:
            send_on = event_date - timedelta(days=offset)

            if today < send_on:
                continue

            # Ventana obsoleta: el evento se cargó después del día en que
            # correspondía este aviso.
            if created_date is not None and send_on < created_date:
                log.debug("Participante · fuera de ventana: %s", _describe(row, offset))
                continue

            if already_sent_participant(conn, row["event_id"], row["person_id"], offset):
                contadores["skipped"] += 1
                log.debug("Participante · ya se había enviado: %s", _describe(row, offset))
                continue

            outcome, motivo = send_reminder(row, offset)
            if outcome == ACCEPTED:
                contadores["sent"] += 1
                log.info("✓ Participante · %s", _describe(row, offset))
                send_participant_push(row, offset)
            elif outcome == REJECTED:
                # Sabemos que no salió: liberar la reserva no puede duplicar nada.
                release_reservation_participant(conn, row["event_id"], row["person_id"], offset)
                contadores["failed"] += 1
                log.error(
                    "✗ Participante · NO salió: %s. Motivo: %s. Lo reintento mañana.",
                    _describe(row, offset), motivo,
                )
            else:  # UNKNOWN
                # NO se libera: el mail pudo haber salido igual y reintentarlo
                # sería mandarlo dos veces.
                contadores["unknown"] += 1
                log.error(
                    "? Participante · SIN CONFIRMAR: %s. Motivo: %s. Puede haber salido "
                    "igual, así que NO lo reintento.",
                    _describe(row, offset), motivo,
                )

    return contadores


def send_participant_push(row: dict, offset: int) -> None:
    """Campanita + push del participante. Nunca corta la corrida.

    Va DESPUÉS del email y aislado: si el push falla, el recordatorio por mail
    ya salió y quedó registrado igual.
    """
    participant_id = row.get("participant_id")
    if not participant_id:
        return  # la persona no tiene cuenta: no hay a quién notificar

    type_label = TYPE_LABELS.get(row["event_type"], row["event_type"])
    when_label = OFFSET_LABELS.get(offset, f"en {offset} días")

    payload = {
        "user_type": "participante",
        "user_id": int(participant_id),
        "title": f"{type_label} {row['event_date'].strftime('%d/%m')}",
        "body": f"Te anotaste y es {when_label}.",
        "kind": "calendar_reminder",
        "url": "/calendarios",
    }

    request = urllib.request.Request(
        NOTIFY_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": settings.INTERNAL_API_KEY,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 201:
                log.warning("  (el mail salió, pero la notificación al celular no: respondió %s)", resp.status)
    except Exception as exc:  # noqa: BLE001 — el push nunca debe romper el cron
        log.warning("  (el mail salió, pero la notificación al celular no: %s)", exc)


# ── Orquestación ───────────────────────────────────────────────────────────

def run() -> int:
    today = today_ar()
    log.info("── Recordatorios ALMA · %s ──", _human_date(today))
    log.debug("Log completo en %s", LOG_FILE)

    sent = 0
    skipped = 0
    failed = 0
    unknown = 0  # sin confirmación de envío: no se reintentan, para no duplicar
    due = 0  # recordatorios que vencieron hoy o antes (corresponde actuar sobre ellos)

    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001
        log.error("No pude conectarme a la base de datos: %s", exc)
        return 1

    try:
        candidates = fetch_candidates(conn, today)
        # El total de filas (evento × voluntario) no le dice nada a nadie; lo que
        # se entiende es cuántos eventos hay con recordatorios prendidos.
        log.info("Reviso %s con recordatorios activos.", _plural(
            len({r["event_id"] for r in candidates}), "evento", "eventos"))

        for row in candidates:
            offsets = parse_offsets(row["reminder_offsets"])
            if not offsets:
                continue

            event_date: date = row["event_date"]
            created_at = row.get("created_at")
            created_date = to_ar_date(created_at) if isinstance(created_at, datetime) else None
            for offset in offsets:
                send_on = event_date - timedelta(days=offset)

                # Aún no llegó el momento de este recordatorio
                if today < send_on:
                    continue

                # La ventana de este recordatorio cayó ANTES de que el evento se creara:
                # quedó obsoleta (evento cargado tarde). No se manda. Evita, p. ej., que un
                # evento creado el jueves dispare el recordatorio de "7 días antes" del miércoles.
                if created_date is not None and send_on < created_date:
                    log.debug("Fuera de ventana (el evento se cargó después): %s", _describe(row, offset))
                    continue

                # Vencido (today >= send_on) y el evento no pasó (garantizado por el WHERE).
                due += 1

                # Reservamos de forma atómica para no duplicar. Los ya enviados van
                # a DEBUG: son la mayoría de las líneas y repetirlos todos los días
                # tapaba lo único que importa, que es lo que salió hoy.
                if already_sent(conn, row["event_id"], row["volunteer_id"], offset):
                    skipped += 1
                    log.debug("Ya se había enviado en otra corrida: %s", _describe(row, offset))
                    continue

                outcome, motivo = send_reminder(row, offset)
                if outcome == ACCEPTED:
                    sent += 1
                    log.info("✓ %s", _describe(row, offset))
                    # El push va DESPUÉS del email y aislado: si falla, el
                    # recordatorio por email ya salió y quedó registrado igual.
                    send_reminder_push(row, offset)
                elif outcome == REJECTED:
                    # Sabemos que no salió ningún mail: liberar la reserva para
                    # reintentar mañana no puede duplicar nada.
                    release_reservation(conn, row["event_id"], row["volunteer_id"], offset)
                    failed += 1
                    log.error("✗ NO salió: %s. Motivo: %s. Lo reintento mañana.",
                              _describe(row, offset), motivo)
                else:  # UNKNOWN
                    # NO se libera la reserva a propósito. El mail pudo haber salido
                    # igual (es justo lo que pasaba con el timeout viejo), así que
                    # reintentar mañana sería mandarlo dos veces. Se avisa por
                    # alerta y queda para revisar a mano.
                    unknown += 1
                    log.error("? SIN CONFIRMAR: %s. Motivo: %s. Puede haber salido igual, "
                              "así que NO lo reintento (evita mandarlo duplicado).",
                              _describe(row, offset), motivo)

        # Segunda tanda: los recordatorios que pidió cada participante.
        # Va adentro del mismo try/conn para compartir conexión y para que un
        # error acá también dispare la alerta.
        participantes = run_participantes(conn, today)
        sent += participantes["sent"]
        skipped += participantes["skipped"]
        failed += participantes["failed"]
        unknown += participantes["unknown"]

    except Exception as exc:  # noqa: BLE001
        log.exception("Se cortó la corrida por un error inesperado: %s", exc)
        # Falla catastrófica (la corrida se cortó): alerta con el traceback completo.
        send_cron_alert("La corrida del cron se cortó por un error inesperado.", str(exc), traceback.format_exc())
        return 1
    finally:
        conn.close()

    # Falla parcial: la corrida terminó, pero algunos envíos fallaron (timeouts,
    # etc.). Avisamos igual para tener visibilidad, sin traceback (no hubo crash).
    if failed > 0 or unknown > 0:
        detalle = f"Enviados={sent}, ya estaban={skipped}, fallidos={failed}, sin confirmar={unknown}."
        if unknown > 0:
            send_cron_alert(
                f"Quedaron {unknown} recordatorio(s) SIN CONFIRMAR: el servidor no respondió a "
                f"tiempo, así que pueden haber salido igual. NO se reintentan, para no mandarlos "
                f"duplicados. Fijate en el log del cron cuáles son y, si no llegaron, reenvialos "
                f"a mano desde el botón del calendario.",
                detalle,
            )
        else:
            send_cron_alert(
                f"{failed} recordatorio(s) no se pudieron enviar. Se reintentan solos "
                f"en la corrida de mañana, no hay que hacer nada.",
                detalle,
            )

    # Resumen en una línea: cuando todo sale bien, es lo único que hace falta leer.
    resumen = []
    if sent:
        resumen.append(_plural(sent, "aviso enviado", "avisos enviados"))
    if skipped:
        resumen.append(_plural(skipped, "ya estaba enviado", "ya estaban enviados"))
    if failed:
        resumen.append(_plural(failed, "falló (se reintenta mañana)", "fallaron (se reintentan mañana)"))
    if unknown:
        resumen.append(_plural(unknown, "sin confirmar", "sin confirmar"))
    if not resumen:
        resumen.append("no había nada para enviar")

    log.info("── Listo: %s ──", ", ".join(resumen))
    return 0


if __name__ == "__main__":
    sys.exit(run())

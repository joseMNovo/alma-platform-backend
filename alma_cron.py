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

Además de los recordatorios, la corrida saluda a quien cumple años hoy
(run_cumpleanos / tabla birthday_sent_log, ver sql/30_cumpleanos.sql). Va
última a propósito: es lo único que no avisa de nada que haya que hacer.

Todo queda logueado en logs/cron/alma_cron.log y en stdout.
"""

from __future__ import annotations

import html
import json
import os
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

# Cómo se nombra el evento en la primera línea del mail. Varía por DOS ejes:
#
#   • quién lo recibe: al voluntario se lo asignaron, el participante se anotó
#     solo. Decirle "asignada" a quien se inscribió por su cuenta es falso.
#   • el tipo: en castellano el género manda. "un taller asignado" pero "una
#     actividad asignada". Por eso varía la frase COMPLETA (artículo +
#     sustantivo + participio) y no solo la palabra "asignada": partirla en
#     pedazos garantiza que tarde o temprano concuerden mal.
EVENT_PHRASES: dict[str, dict[str, str]] = {
    "grupo":     {"voluntario": "un grupo de apoyo asignado", "participante": "un grupo de apoyo"},
    "taller":    {"voluntario": "un taller asignado",         "participante": "un taller"},
    "actividad": {"voluntario": "una actividad asignada",     "participante": "una actividad"},
}

# Audiencias válidas para EVENT_PHRASES.
VOLUNTARIO = "voluntario"
PARTICIPANTE = "participante"

EMAIL_ENDPOINT = f"http://{settings.API_HOST}:{settings.API_PORT}/emails/send"
NOTIFY_ENDPOINT = f"http://{settings.API_HOST}:{settings.API_PORT}/notifications/notify"
EMAIL_TEMPLATE = "event_assignment"
BIRTHDAY_TEMPLATE = "birthday"

# A quién se saluda. Un "pendiente" todavía no fue aprobado por nadie y un
# "inactivo" dejó de participar: a los dos, un saludo del equipo les llegaría
# como un mail de una organización en la que no están.
BIRTHDAY_STATUSES = ("activo",)

# ── Modo prueba ────────────────────────────────────────────────────────────
#
# Con CRON_EMAIL_PRUEBA puesto, TODOS los mails de la corrida van a esa
# dirección en lugar de a su destinatario real.
#
# Para qué: la única forma de probar el camino completo —consulta, reserva,
# Resend, campanita— es con una corrida de verdad, y una corrida de verdad en
# producción le escribe a voluntarias reales. Con un cumpleaños de prueba mal
# cargado, el saludo le llega a alguien que no cumple años.
#
# No tiene valor por defecto a propósito: si se activara solo, la corrida de
# todos los días dejaría de avisarle a nadie y nadie se enteraría, porque el
# log seguiría diciendo "enviado". Se pone a mano para una corrida y se saca:
#
#     CRON_EMAIL_PRUEBA=manunovo@gmail.com ./venv/bin/python alma_cron.py
#
# La campanita NO se redirige: no manda nada hacia afuera y se lee en la app
# de quien corresponda, así que no hay nada de qué protegerse.
EMAIL_PRUEBA = (os.getenv("CRON_EMAIL_PRUEBA") or "").strip()


def destinatario(email_real: str) -> list[str]:
    """A quién se le manda de verdad. Ver EMAIL_PRUEBA."""
    return [EMAIL_PRUEBA] if EMAIL_PRUEBA else [email_real]
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


#: Ancho del separador de día. 68 columnas entran en una terminal de 100
#: contando el prefijo del log (fecha + nivel), que son unos 30 caracteres.
REGLA = "=" * 68


def _human_date(d: date) -> str:
    """'lunes 28/07/2026'."""
    return f"{DAY_NAMES[d.weekday()]} {d.strftime('%d/%m/%Y')}"


def _event_name(row: dict) -> str:
    """Cómo se llama el encuentro, para mostrárselo a una persona.

    El nombre sale del programa de origen (grupo/taller/actividad) y lo resuelve
    la query. Cuando no hay ninguno —evento suelto, sin `source_id` ni `title`—
    se cae al tipo ("Taller"), que es lo único cierto que sabemos. Nunca
    devuelve vacío: este texto va al asunto del mail.
    """
    return (row.get("event_name") or "").strip() or TYPE_LABELS.get(
        row["event_type"], row["event_type"]
    )


def _event_phrase(row: dict, audience: str) -> str:
    """"un taller asignado" / "una actividad" — ver EVENT_PHRASES."""
    por_tipo = EVENT_PHRASES.get(row["event_type"], EVENT_PHRASES["actividad"])
    return por_tipo.get(audience, por_tipo[VOLUNTARIO])


def _when_sentence(row: dict, offset: int) -> str:
    """'Es mañana (29/07/2026 a las 18:00 hs).' — cuerpo de la campanita.

    Mismo formato que arma el recordatorio manual del calendario, así el aviso
    se lee igual lo dispare el cron o una persona.
    """
    when_label = OFFSET_LABELS.get(offset, f"en {offset} días")
    cuando = row["event_date"].strftime("%d/%m/%Y")
    start_time = str(row["start_time"])[:5] if row["start_time"] is not None else ""
    if start_time:
        cuando += f" a las {start_time} hs"
    return f"Es {when_label} ({cuando})."


def _describe(row: dict, offset: int) -> str:
    """Una línea que identifica el recordatorio sin jerga ni IDs.

    Ej.: 'Victoria Rébori — Estimulación cognitiva 29/07 (es mañana)'. Si el
    evento no tiene nombre propio, queda el tipo: 'Victoria Rébori — Taller
    29/07 (es mañana)'.
    """
    full_name = f"{row['name']} {row['last_name'] or ''}".strip()
    when_label = OFFSET_LABELS.get(offset, f"en {offset} días")
    return f"{full_name} — {_event_name(row)} {row['event_date'].strftime('%d/%m')} (es {when_label})"


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

    `event_name` sale del programa de origen: `ci.title` casi siempre viene NULL,
    así que el nombre real está en grupos/talleres/actividades y se alcanza por
    `source_id`. Mismo COALESCE que usa el calendario y el recordatorio manual
    (`/instances/{id}/notify`), para que el evento se llame igual en todos lados.
    Los LEFT JOIN no multiplican filas: los tres van contra la PK de su tabla.
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
            COALESCE(ci.title, g.name, t.name, a.name) AS event_name,
            ca.role          AS role,
            v.id             AS volunteer_id,
            v.name           AS name,
            v.last_name      AS last_name,
            v.email          AS email
        FROM calendar_instances ci
        JOIN calendar_assignments ca ON ca.instance_id = ci.id
        JOIN voluntarios v           ON v.id = ca.volunteer_id
        LEFT JOIN grupos      g ON ci.type = 'grupo'     AND g.id = ci.source_id
        LEFT JOIN talleres    t ON ci.type = 'taller'    AND t.id = ci.source_id
        LEFT JOIN actividades a ON ci.type = 'actividad' AND a.id = ci.source_id
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


def send_reminder(row: dict, offset: int, audience: str = VOLUNTARIO) -> tuple[str, str]:
    """Manda el recordatorio. Devuelve (resultado, motivo).

    resultado es ACCEPTED / REJECTED / UNKNOWN; motivo es un texto corto para el
    log cuando algo salió mal (vacío si salió bien). Los tres estados existen
    para no mandar nunca un recordatorio duplicado: la reserva de
    reminder_sent_log solo se libera con REJECTED, el único caso donde SABEMOS
    que no salió ningún mail.

    `audience` decide cómo se nombra el evento en la primera línea (ver
    EVENT_PHRASES): al voluntario se lo asignaron, el participante se anotó.
    Es lo ÚNICO que cambia entre las dos tandas; el resto del mail es idéntico
    a propósito, para que no se desincronicen.

    Quien loguea el resultado es run(), para que todas las líneas salgan con el
    mismo formato.
    """
    event_date: date = row["event_date"]
    type_label = TYPE_LABELS.get(row["event_type"], row["event_type"])
    event_name = _event_name(row)
    when_label = OFFSET_LABELS.get(offset, f"en {offset} días")
    start_time = str(row["start_time"])[:5] if row["start_time"] is not None else ""

    # Cuando el evento no tiene nombre propio, _event_name ya devolvió el tipo:
    # repetirlo arriba dejaría un recuadro que dice "TALLER / Taller". En ese
    # caso la etiqueta se manda vacía y el recuadro arranca directo en el nombre.
    has_own_name = bool((row.get("event_name") or "").strip())

    # _render() hace un str.replace pelado, sin escapar: un "&" o un "<" en un
    # nombre cargado a mano llegaría crudo al HTML del mail. Se escapa acá todo
    # lo que sea texto libre. El asunto NO se escapa: es una cabecera, no HTML,
    # y ahí "&amp;" se leería literal.
    variables = {
        "name": html.escape(row["name"] or ""),
        "event_label": type_label if has_own_name else "",
        "event_name": html.escape(event_name),
        "event_phrase": _event_phrase(row, audience),
        "event_date": event_date.strftime("%d/%m/%Y"),
        "event_time": start_time,
        "when_label": when_label,
        "notes": html.escape(row["notes"] or ""),
        "app_url": settings.APP_BASE_URL,
    }

    # El nombre va PRIMERO: en el celular el asunto se corta a los ~35
    # caracteres, y lo que tiene que sobrevivir al corte es qué es, no cuándo.
    subject = f"Recordatorio: {event_name} — {when_label} ({event_date.strftime('%d/%m')})"

    payload = {
        "to": destinatario(row["email"]),
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
    payload = {
        "user_type": "voluntario",
        "user_id": row["volunteer_id"],
        "title": f"Recordatorio: {_event_name(row)}",
        "body": _when_sentence(row, offset),
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
            COALESCE(ci.title, g.name, t.name, a.name) AS event_name,
            r.offsets        AS reminder_offsets,
            p.id             AS person_id,
            p.participant_id AS participant_id,
            p.name           AS name,
            p.last_name      AS last_name,
            p.email          AS email
        FROM participant_event_reminders r
        JOIN calendar_instances ci    ON ci.id = r.calendar_instance_id
        JOIN participant_profiles p   ON p.id = r.person_id
        LEFT JOIN grupos      g ON ci.type = 'grupo'     AND g.id = ci.source_id
        LEFT JOIN talleres    t ON ci.type = 'taller'    AND t.id = ci.source_id
        LEFT JOIN actividades a ON ci.type = 'actividad' AND a.id = ci.source_id
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
    desincroniza. Lo único que cambia es la audiencia (PARTICIPANTE), que decide
    si el evento está "asignado" o no.
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

            outcome, motivo = send_reminder(row, offset, PARTICIPANTE)
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

    payload = {
        "user_type": "participante",
        "user_id": int(participant_id),
        "title": f"Recordatorio: {_event_name(row)}",
        "body": f"Te anotaste. {_when_sentence(row, offset)}",
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



# ── Cumpleaños ─────────────────────────────────────────────────────────────
#
# El único mail del sistema que no avisa de una obligación. No se puede
# desactivar por evento como los recordatorios, porque no cuelga de ningún
# evento: cuelga de una fecha que la persona cargó en su perfil. Quien no
# tenga fecha de nacimiento, simplemente no entra en la consulta.

def fetch_birthdays(conn, today: date) -> list[dict]:
    """Quiénes cumplen años hoy.

    ── El 29 de febrero ───────────────────────────────────────────────
    Tres de cada cuatro años esa fecha no existe, y la comparación directa
    (mes y día iguales a los de hoy) dejaría a esa persona sin saludo salvo
    en los bisiestos. Se la saluda el 28 de febrero, que es el último día que
    el calendario le ofrece: antes y no después, porque un saludo de
    cumpleaños llega tarde el 1 de marzo y no llega tarde el 28.

    La condición extra se arma en Python y no en SQL a propósito: `today` ya
    viene en hora de Argentina (today_ar), así que la decisión de qué día es
    hoy se toma una sola vez y en un solo lugar. Preguntarle el día a MySQL
    la tomaría de nuevo, con el reloj del servidor, que está en otra zona.
    """
    condiciones = ["(MONTH(v.birth_date) = %s AND DAY(v.birth_date) = %s)"]
    params: list = [today.month, today.day]

    es_bisiesto = (today.year % 4 == 0 and today.year % 100 != 0) or today.year % 400 == 0
    if today.month == 2 and today.day == 28 and not es_bisiesto:
        condiciones.append("(MONTH(v.birth_date) = 2 AND DAY(v.birth_date) = 29)")

    marcadores = ", ".join(["%s"] * len(BIRTHDAY_STATUSES))
    sql = f"""
        SELECT
            v.id         AS volunteer_id,
            v.name       AS name,
            v.last_name  AS last_name,
            v.email      AS email,
            v.birth_date AS birth_date
        FROM voluntarios v
        WHERE v.birth_date IS NOT NULL
          AND ({" OR ".join(condiciones)})
          AND v.status IN ({marcadores})
          AND v.email IS NOT NULL
          AND v.email <> ''
        ORDER BY v.id ASC
    """
    with conn.cursor() as cur:
        cur.execute(sql, (*params, *BIRTHDAY_STATUSES))
        return cur.fetchall()


def already_greeted(conn, volunteer_id: int, year: int, today: date) -> bool:
    """Reserva el saludo. True si ya estaba reservado (o sea: no mandar).

    Igual que already_sent: el INSERT IGNORE es la reserva, y se hace ANTES de
    mandar nada. Si dos corridas se pisan, la segunda encuentra la fila puesta
    y no manda. Ver sql/30_cumpleanos.sql.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT IGNORE INTO birthday_sent_log (volunteer_id, year, sent_on)
            VALUES (%s, %s, %s)
            """,
            (volunteer_id, year, today),
        )
        reservado_ahora = cur.rowcount == 1
    conn.commit()
    return not reservado_ahora


def release_greeting(conn, volunteer_id: int, year: int) -> None:
    """Suelta la reserva. SOLO cuando sabemos que el mail no salió."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM birthday_sent_log WHERE volunteer_id=%s AND year=%s",
            (volunteer_id, year),
        )
    conn.commit()


def send_birthday(row: dict) -> tuple[str, str]:
    """Manda el saludo. Devuelve (resultado, motivo), igual que send_reminder.

    Comparte con send_reminder el endpoint, el timeout largo y los tres
    estados. La única diferencia es la plantilla y que acá la única variable
    es el nombre: el mail no tiene nada que informar.
    """
    nombre = (row["name"] or "").strip()

    payload = {
        "to": destinatario(row["email"]),
        # Sin la fecha ni el apellido: el asunto se corta a los ~35 caracteres
        # en el celular y lo único que tiene que sobrevivir es el saludo.
        "subject": "¡Feliz cumpleaños!",
        "template": BIRTHDAY_TEMPLATE,
        # html.escape porque _render hace un replace pelado: un apóstrofo o un
        # "&" en el nombre llegaría crudo al HTML.
        "variables": {"name": html.escape(nombre)},
        "sent_by_volunteer_id": None,
    }

    request = urllib.request.Request(
        EMAIL_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
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
        detail = exc.read().decode("utf-8", "replace")[:200].strip()
        return REJECTED, f"el servidor respondió {exc.code}" + (f" ({detail})" if detail else "")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            return UNKNOWN, "el servidor no respondió a tiempo al conectar"
        return REJECTED, f"no me pude conectar al servidor ({exc.reason})"
    except TimeoutError:
        # Ver la nota larga en send_reminder: puede haber salido igual, así que
        # la reserva NO se libera.
        return UNKNOWN, f"el servidor no respondió en {EMAIL_REQUEST_TIMEOUT}s"

    if status_code != 201:
        return REJECTED, f"el servidor respondió {status_code}"

    # 201 significa "quedó registrado", no "llegó": el resultado real de Resend
    # viene en el campo status del cuerpo.
    try:
        body = json.loads(raw_body)
    except (ValueError, TypeError):
        body = {}

    if body.get("status") == "failed":
        motivo = (body.get("error_message") or "").strip()[:200]
        return REJECTED, "Resend lo rechazó" + (f": {motivo}" if motivo else "")

    return ACCEPTED, ""



def _notificar(payload: dict, contexto: str) -> None:
    """Un POST a /notifications/notify. Best-effort: nunca rompe la corrida.

    El endpoint manda el webpush en BackgroundTasks, así que contesta sin
    esperar al navegador de nadie: llamarlo varias veces seguidas no acumula
    demora. (En serie y esperando el push, tres destinatarios ya daban
    timeout; por eso el envío quedó del otro lado.)
    """
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
                log.warning("  (no pude avisar por la campanita a %s: respondió %s)", contexto, resp.status)
    except Exception as exc:  # noqa: BLE001 — la campanita nunca rompe el cron
        log.warning("  (no pude avisar por la campanita a %s: %s)", contexto, exc)


def fetch_admins(conn) -> list[dict]:
    """Los admins activos, para contarles quién cumple años.

    Solo admins y no todo el equipo: con veinte voluntarios, avisarle a todos
    de cada cumpleaños convierte la campanita en algo que se ignora, y una
    campanita que se ignora también se pierde los avisos que importan. El
    admin es quien puede llevar el saludo al grupo, que es donde sirve.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, last_name
            FROM voluntarios
            WHERE is_admin = 1 AND status = 'activo'
            ORDER BY id ASC
            """
        )
        return cur.fetchall()


def send_birthday_push(conn, row: dict) -> None:
    """La campanita del día: al que cumple, y a los admins para que lo saluden.

    Va DESPUÉS del mail y aislada: si esto falla, el saludo por mail ya salió
    y quedó registrado igual. Tampoco toca los contadores — el cumpleaños se
    cuenta como saludado cuando salió el mail, que es el saludo de verdad.
    """
    nombre = (row["name"] or "").strip()
    completo = f"{nombre} {row['last_name'] or ''}".strip()

    # 1) Al cumpleañero.
    _notificar(
        {
            "user_type": "voluntario",
            "user_id": row["volunteer_id"],
            "title": "¡Feliz cumpleaños! 🎂",
            "body": "Hoy es tu día. Que lo pases muy lindo.",
            "kind": "birthday",
            "url": "/inicio",
        },
        completo,
    )

    # 2) A los admins.
    try:
        admins = fetch_admins(conn)
    except Exception as exc:  # noqa: BLE001
        log.warning("  (no pude buscar a los admins para avisarles del cumpleaños: %s)", exc)
        return

    for admin in admins:
        # Si el que cumple años ES admin, ya recibió el saludo de arriba.
        # Mandarle además "hoy cumple años Fulano" —siendo él Fulano— es
        # una notificación que se lee como un error del sistema.
        if admin["id"] == row["volunteer_id"]:
            continue

        _notificar(
            {
                "user_type": "voluntario",
                "user_id": admin["id"],
                "title": f"Hoy cumple años {completo}",
                "body": "Un saludo del equipo le va a alegrar el día.",
                "kind": "birthday",
                "url": "/voluntarios",
            },
            f"admin {admin['name']}",
        )


def run_cumpleanos(conn, today: date) -> dict:
    """Saluda a quien cumpla años hoy. Devuelve los contadores para el resumen."""
    contadores = {"sent": 0, "skipped": 0, "failed": 0, "unknown": 0}

    try:
        cumplen = fetch_birthdays(conn, today)
    except Exception as exc:  # noqa: BLE001
        # Si la tabla todavía no existe (falta correr 30_cumpleanos.sql), esto
        # NO puede tumbar los recordatorios, que son lo importante del día.
        log.error("Cumpleaños: no pude consultarlos (%s). Sigo con el resto.", exc)
        return contadores

    if not cumplen:
        log.info("Cumpleaños: hoy no cumple años nadie.")
        return contadores

    for row in cumplen:
        quien = f"{row['name']} {row['last_name'] or ''}".strip()
        try:
            if already_greeted(conn, row["volunteer_id"], today.year, today):
                contadores["skipped"] += 1
                continue

            resultado, motivo = send_birthday(row)

            if resultado == ACCEPTED:
                contadores["sent"] += 1
                log.info("🎂 %s — ¡feliz cumpleaños!", quien)
                send_birthday_push(conn, row)
            elif resultado == UNKNOWN:
                contadores["unknown"] += 1
                log.error(
                    "? Cumpleaños SIN CONFIRMAR: %s. Motivo: %s. Puede haber salido "
                    "igual, así que NO se reintenta.", quien, motivo,
                )
            else:
                release_greeting(conn, row["volunteer_id"], today.year)
                contadores["failed"] += 1
                log.error("✗ Cumpleaños de %s: %s. Se reintenta mañana.", quien, motivo)
        except Exception as exc:  # noqa: BLE001
            # Un saludo que explota no se puede llevar puestos a los demás.
            contadores["failed"] += 1
            log.error("✗ Cumpleaños de %s: error inesperado (%s).", quien, exc)

    return contadores


# ── Orquestación ───────────────────────────────────────────────────────────

# ── Tercera tanda: compras que quedaron sin pagar ──────────────────────

SQL_COMPRAS_PENDIENTES = """
SELECT pi.id            AS intent_id,
       pi.person_id     AS person_id,
       pp.name          AS name,
       pp.last_name     AS last_name,
       pp.email         AS email,
       t.id             AS training_id,
       t.title          AS title,
       t.slug           AS slug,
       t.payment_url    AS payment_url
  FROM purchase_intents pi
  JOIN participant_profiles pp ON pp.id = pi.person_id
  JOIN trainings           t  ON t.id  = pi.training_id
 WHERE pi.reminded_at IS NULL
   AND pi.created_at <= (NOW() - INTERVAL 24 HOUR)
   AND pp.email IS NOT NULL AND pp.email <> ''
   -- Ya lo habilitaron: no hay nada que recordar.
   AND NOT EXISTS (
         SELECT 1 FROM person_access_grants g
          WHERE g.person_id  = pi.person_id
            AND g.module_key = 'capacitaciones'
            AND g.resource_id = pi.training_id
            AND g.is_active = 1
            AND g.revoked_at IS NULL)
   -- Ya quedó registrado el pago, aunque todavía no se haya habilitado.
   AND NOT EXISTS (
         SELECT 1 FROM person_payments pay
          WHERE pay.person_id    = pi.person_id
            AND pay.concept_type = 'capacitacion'
            AND pay.concept_id   = pi.training_id)
   -- Ya avisó que pagó y está esperando que lo revisen. Apurarlo sería
   -- exactamente el mail que no hay que mandar.
   AND NOT EXISTS (
         SELECT 1 FROM payment_claims pc
          WHERE pc.person_id = pi.person_id
            AND pc.concept_id = pi.training_id
            AND pc.status = 'pendiente')
 ORDER BY pi.created_at
"""


def _boton_pago(payment_url: str | None) -> str:
    """El botón de pagar, o nada si esa capacitación no tiene link cargado."""
    if not payment_url:
        return ""
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" style="margin:24px auto 0;">'
        '<tr><td style="border-radius:7px;background:#5EC0CF;">'
        f'<a href="{payment_url}" style="display:inline-block;padding:11px 28px;color:#ffffff;'
        'text-decoration:none;font-size:14px;font-weight:600;border-radius:7px;'
        """font-family:'Nunito Sans',Arial,sans-serif;">Pagar ahora</a>"""
        "</td></tr></table>"
    )


def enviar_compra_pendiente(row: dict) -> tuple[str, str]:
    """Manda UN recordatorio de compra pendiente. Devuelve (estado, motivo)."""
    nombre = (row.get("name") or "").strip() or (row.get("email") or "").split("@")[0]
    base = settings.APP_BASE_URL.rstrip("/")

    payload = {
        "to": destinatario(row["email"]),
        "subject": f"Tu inscripción a {row['title']} quedó pendiente",
        "template": "compra_pendiente",
        "variables": {
            "name": nombre,
            "capacitacion": row["title"],
            "boton_pago_html": _boton_pago(row.get("payment_url")),
            # Cae DENTRO de la capacitación, que es donde está el botón de
            # "ya pagué". Mandarlo a la vidriera lo obligaría a buscarla.
            "avisar_url": f"{base}/academia?c={row['slug']}",
            "app_url": base,
        },
        "sent_by_volunteer_id": None,
    }

    request = urllib.request.Request(
        EMAIL_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": settings.INTERNAL_API_KEY,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=EMAIL_REQUEST_TIMEOUT) as resp:
            if resp.status != 201:
                return REJECTED, f"el servidor respondió {resp.status}"
    except urllib.error.HTTPError as exc:
        return REJECTED, f"el servidor respondió {exc.code}"
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            return UNKNOWN, "el servidor no respondió a tiempo al conectar"
        return REJECTED, f"no me pude conectar al servidor ({exc.reason})"
    except TimeoutError:
        # Mismo criterio que los recordatorios del calendario: el mail pudo
        # haber salido igual, así que se marca como enviado y NO se reintenta.
        # Preferimos que falte uno antes que mandar dos veces "pagá".
        return UNKNOWN, f"el servidor no respondió en {EMAIL_REQUEST_TIMEOUT}s"

    return ACCEPTED, ""


def run_compras_pendientes(conn, today: date) -> dict:
    """Le recuerda a quien se inscribió para comprar y no pagó.

    Se manda UNA sola vez por intención: `reminded_at` se marca aunque el
    envío quede sin confirmar. Insistir con un cobro es la clase de error que
    hace que alguien se enoje con ALMA, y el costo de no insistir es que ese
    cobro se pierda — mucho más barato.

    Y NO se manda si ya pagó, ya está habilitado, o ya avisó que pagó. Esa
    última condición es la que hace que esto conviva con el botón de "ya
    pagué": quien ya levantó la mano no recibe un mail apurándolo.
    """
    contadores = {"sent": 0, "skipped": 0, "failed": 0, "unknown": 0}

    with conn.cursor() as cur:
        cur.execute(SQL_COMPRAS_PENDIENTES)
        filas = cur.fetchall()

    if not filas:
        log.info("Compras pendientes: no hay nadie a quien recordarle.")
        return contadores

    log.info("Compras pendientes: %s.", _plural(len(filas), "recordatorio", "recordatorios"))

    for row in filas:
        estado, motivo = enviar_compra_pendiente(row)
        etiqueta = f"{row.get('email')} · {row.get('title')}"

        if estado == ACCEPTED:
            contadores["sent"] += 1
            log.info("Enviado: %s", etiqueta)
        elif estado == UNKNOWN:
            contadores["unknown"] += 1
            log.error("? SIN CONFIRMAR: %s. Motivo: %s. NO se reintenta.", etiqueta, motivo)
        else:
            contadores["failed"] += 1
            log.error("No se pudo enviar: %s. Motivo: %s", etiqueta, motivo)

        # Se marca salvo que haya fallado de forma limpia (ahí sí se reintenta
        # mañana, porque tenemos la certeza de que no salió nada).
        if estado in (ACCEPTED, UNKNOWN):
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE purchase_intents SET reminded_at = NOW() WHERE id = %s",
                    (row["intent_id"],),
                )
            conn.commit()

    return contadores


def run() -> int:
    today = today_ar()
    # El corte de día, en bloque y no en una línea.
    #
    # El archivo es una sola tira: cada corrida agrega entre tres y veinte
    # renglones, y todos empiezan igual —fecha, nivel, barra—. Con "──" de un
    # lado y del otro, el comienzo de un día pesaba lo mismo que cualquier
    # "✓ Fulano" del medio, así que para encontrar la corrida de ayer había
    # que leer las fechas una por una.
    #
    # Tres renglones y la fecha en mayúscula: la regla de "=" no aparece en
    # ningún otro lado del log, así que el ojo la encuentra pasando rápido y
    # `grep -n "^=" ` lista todos los días de un saque.
    log.info("")
    log.info(REGLA)
    log.info("   RECORDATORIOS ALMA   ·   %s", _human_date(today).upper())
    log.info(REGLA)
    if EMAIL_PRUEBA:
        # Bien arriba y en WARNING: una corrida en modo prueba que pase
        # desapercibida se lee como una corrida normal —el log dice
        # "enviado" igual— y nadie se entera de que no le llegó a nadie.
        log.warning("   ⚠ MODO PRUEBA: todos los mails van a %s", EMAIL_PRUEBA)
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

                outcome, motivo = send_reminder(row, offset, VOLUNTARIO)
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

        # Tercera tanda: compras que quedaron sin pagar. Misma conexión y mismo
        # try, así un error acá también dispara la alerta.
        compras = run_compras_pendientes(conn, today)
        sent += compras["sent"]
        skipped += compras["skipped"]
        failed += compras["failed"]
        unknown += compras["unknown"]

        # Cuarta tanda: los cumpleaños del día. Va última porque es la única
        # que no avisa de nada que haya que hacer: si algo se rompe antes,
        # que se rompa después de los recordatorios y no en vez de ellos.
        cumples = run_cumpleanos(conn, today)
        sent += cumples["sent"]
        skipped += cumples["skipped"]
        failed += cumples["failed"]
        unknown += cumples["unknown"]

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

    # El pie cierra con una regla más liviana que la de arriba: marca el final
    # de la corrida sin competir con el comienzo del día siguiente.
    log.info("-" * len(REGLA))
    log.info("   LISTO · %s", ", ".join(resumen))
    return 0


if __name__ == "__main__":
    sys.exit(run())

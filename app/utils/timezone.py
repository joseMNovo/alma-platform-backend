"""
app/utils/timezone.py — ALMA Backend — Hora/fecha de Argentina
================================================================
El servidor (VPS) corre en UTC. Argentina es UTC-3, así que entre las 21:00
y las 23:59 hora local el servidor ya "vive" en el día siguiente. Cualquier
cálculo de "cuántos días faltan para el evento" (recordatorios de calendario,
cron) que use date.today()/datetime.now() se corre un día durante esa
ventana. Usar SIEMPRE estas funciones para ese tipo de cálculo.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def now_ar() -> datetime:
    return datetime.now(AR_TZ)


def today_ar() -> date:
    return now_ar().date()


def to_ar_date(server_dt: datetime) -> date:
    """Convierte un datetime naive tal como lo devuelve MySQL (columna
    TIMESTAMP, hora del servidor = UTC) a la fecha calendario en Argentina.

    Sin esto, `server_dt.date()` da la fecha UTC: un evento cargado entre las
    21:00 y las 23:59 hora Argentina queda con "fecha de creación" un día
    adelantada, y comparaciones tipo `send_on < created_date` pueden
    descartar recordatorios válidos por error.
    """
    return server_dt.replace(tzinfo=timezone.utc).astimezone(AR_TZ).date()

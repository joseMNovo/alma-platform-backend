"""
app/utils/timezone.py — ALMA Backend — Hora/fecha de Argentina
================================================================
El servidor (VPS) NO corre en UTC (confirmado: corre en Europe/Berlin,
CEST/CET). Argentina está 5 horas atrás de CEST, así que buena parte de la
tarde/noche en Argentina el servidor ya "vive" en el día siguiente. Cualquier
cálculo de "cuántos días faltan para el evento" (recordatorios de calendario,
cron) que use date.today()/datetime.now() se corre un día durante esa
ventana. Usar SIEMPRE estas funciones para ese tipo de cálculo — no dependen
de en qué zona esté el servidor, así que quedan correctas aunque el server
cambie de zona en el futuro.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def now_ar() -> datetime:
    return datetime.now(AR_TZ)


def today_ar() -> date:
    return now_ar().date()


def to_ar_date(server_dt: datetime) -> date:
    """Convierte un datetime naive tal como lo devuelve MySQL (columna
    TIMESTAMP con DEFAULT CURRENT_TIMESTAMP) a la fecha calendario en
    Argentina.

    MySQL calcula ese valor con SU PROPIO reloj — normalmente `time_zone =
    SYSTEM`, es decir la zona del sistema operativo del servidor (hoy CEST,
    puede cambiar). `datetime.astimezone()` sin argumentos interpreta un
    datetime naive como "hora local del sistema operativo actual", que es
    exactamente ese mismo reloj — por eso NO hardcodeamos UTC acá: si
    asumiéramos UTC a la fuerza y el servidor no lo fuera (como es el caso
    hoy), la conversión quedaría mal por el offset entero de esa zona.

    Ojo: esto asume que la sesión de MySQL usa `time_zone = SYSTEM` (el
    default de fábrica). Si alguien configuró explícitamente el time_zone de
    MySQL a otra cosa (p. ej. UTC fijo, independiente del SO), hay que
    ajustar esta función para reflejarlo.
    """
    return server_dt.astimezone(AR_TZ).date()

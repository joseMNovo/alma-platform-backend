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
from datetime import date, datetime, time, timedelta
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


def ar_date_to_server(d: date, *, dia_siguiente: bool = False) -> datetime:
    """La operación inversa de `to_ar_date`: una fecha de Argentina expresada
    en el reloj del servidor, naive, lista para comparar contra una columna
    TIMESTAMP.

    Hace falta para FILTRAR por fecha. Si uno compara `created_at >= 2026-10-04
    00:00` a secas, está mezclando dos relojes: la fecha la eligió alguien en
    Argentina y la columna la escribió MySQL con la hora de Berlín. Una venta
    de las 22:00 en Rosario quedó guardada como las 03:00 del día siguiente,
    así que un filtro "hasta el 4" la dejaba afuera — justo las ventas del
    final de una jornada, que son las que más importan.

    `dia_siguiente=True` devuelve el comienzo del día de después: es el borde
    superior de un rango, y va con `<`, nunca con `<=`. Con `<=` sobre una
    medianoche exacta entraría un segundo de más.
    """
    arranque = datetime.combine(
        d + timedelta(days=1) if dia_siguiente else d, time.min, tzinfo=AR_TZ
    )
    # `.astimezone()` sin argumento = hora local del sistema, que es el mismo
    # reloj con el que MySQL escribió la columna (ver la nota de to_ar_date).
    return arranque.astimezone().replace(tzinfo=None)

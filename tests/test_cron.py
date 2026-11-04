"""
Tests del cron de recordatorios (alma_cron.py).

Lo que más importa acá es que NUNCA mande un recordatorio duplicado. Esa garantía
depende de una sola decisión: cuándo se libera la reserva de reminder_sent_log.
Se libera solo cuando SABEMOS que no salió ningún mail; ante la duda, no.

El cron habla con el backend por HTTP, así que los tests reemplazan `urlopen`.
El candado de red del conftest garantiza que, si algún camino se escapara del
doble, el test falla en vez de mandar un mail de verdad.
"""
import json
import urllib.error
from datetime import date

import pytest

import alma_cron


# ── Doble de la respuesta HTTP ─────────────────────────────────────────────

class RespuestaFalsa:
    """Imita lo que devuelve urlopen: context manager con .status y .read()."""

    def __init__(self, status=201, cuerpo=None):
        self.status = status
        self._cuerpo = json.dumps(cuerpo if cuerpo is not None else {"status": "sent"}).encode()

    def read(self):
        return self._cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def fila():
    """Una fila como la que devuelve fetch_candidates."""
    return {
        "event_id": 113,
        "event_type": "taller",
        "event_date": date(2026, 7, 29),
        "start_time": "18:00:00",
        "notes": "Sala 2",
        "volunteer_id": 33,
        "name": "Victoria",
        "last_name": "Rébori",
        "email": "victoria@tests.local",
    }


def _responder_con(monkeypatch, respuesta):
    """Hace que la próxima llamada HTTP del cron devuelva `respuesta`.

    Si `respuesta` es una excepción, se lanza.
    """
    def _falso(request, timeout=None):
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    monkeypatch.setattr(alma_cron.urllib.request, "urlopen", _falso)


# ── La decisión que evita duplicados ───────────────────────────────────────

def test_201_con_status_sent_es_aceptado(monkeypatch, fila):
    _responder_con(monkeypatch, RespuestaFalsa(201, {"status": "sent"}))

    resultado, motivo = alma_cron.send_reminder(fila, 1)

    assert resultado == alma_cron.ACCEPTED
    assert motivo == ""


def test_resend_rechaza_el_mail_aunque_el_http_sea_201(monkeypatch, fila):
    """El agujero que teníamos: /emails/send devuelve 201 aunque Resend falle.

    Si el cron solo mirara el código HTTP, cantaría "enviado" un mail que nunca
    salió. Tiene que mirar el `status` del cuerpo.
    """
    _responder_con(monkeypatch, RespuestaFalsa(201, {
        "status": "failed",
        "error_message": "invalid email address",
    }))

    resultado, motivo = alma_cron.send_reminder(fila, 1)

    assert resultado == alma_cron.REJECTED, "Un mail rechazado por Resend no puede contar como enviado"
    assert "invalid email address" in motivo


def test_timeout_no_se_reintenta_para_no_duplicar(monkeypatch, fila):
    """EL test importante. Un timeout NO significa que el mail no salió: puede
    haber salido igual. Reintentarlo mañana sería mandarlo dos veces."""
    _responder_con(monkeypatch, TimeoutError("se acabó el tiempo"))

    resultado, motivo = alma_cron.send_reminder(fila, 1)

    assert resultado == alma_cron.UNKNOWN, (
        "Un timeout debe quedar SIN CONFIRMAR. Si vuelve a ser un fallo común, "
        "el cron liberaría la reserva y mandaría el recordatorio duplicado."
    )
    assert "no respondió" in motivo


def test_timeout_de_conexion_tampoco_se_reintenta(monkeypatch, fila):
    _responder_con(monkeypatch, urllib.error.URLError(TimeoutError("timeout al conectar")))

    resultado, _ = alma_cron.send_reminder(fila, 1)

    assert resultado == alma_cron.UNKNOWN


def test_error_http_si_se_reintenta(monkeypatch, fila):
    """Acá sí sabemos que no salió nada, así que reintentar es seguro."""
    error = urllib.error.HTTPError(
        url="http://x", code=500, msg="Server Error", hdrs=None, fp=None,
    )
    error.read = lambda: b"boom"
    _responder_con(monkeypatch, error)

    resultado, motivo = alma_cron.send_reminder(fila, 1)

    assert resultado == alma_cron.REJECTED
    assert "500" in motivo


def test_conexion_rechazada_si_se_reintenta(monkeypatch, fila):
    """El backend está caído: no se procesó nada, se reintenta sin riesgo."""
    _responder_con(monkeypatch, urllib.error.URLError("Connection refused"))

    resultado, _ = alma_cron.send_reminder(fila, 1)

    assert resultado == alma_cron.REJECTED


def test_respuesta_sin_json_valido_no_rompe(monkeypatch, fila):
    """Si el cuerpo no se puede parsear, se asume aceptado (el 201 ya llegó) en
    vez de reventar la corrida entera."""
    respuesta = RespuestaFalsa(201)
    respuesta._cuerpo = b"no soy json"
    _responder_con(monkeypatch, respuesta)

    resultado, _ = alma_cron.send_reminder(fila, 1)

    assert resultado == alma_cron.ACCEPTED


# ── Lo que se le manda al backend ──────────────────────────────────────────

def test_manda_el_template_y_los_datos_correctos(monkeypatch, fila):
    capturado = {}

    def _falso(request, timeout=None):
        capturado["url"] = request.full_url
        capturado["payload"] = json.loads(request.data)
        capturado["headers"] = request.headers
        return RespuestaFalsa()

    monkeypatch.setattr(alma_cron.urllib.request, "urlopen", _falso)

    alma_cron.send_reminder(fila, 7)

    payload = capturado["payload"]
    assert payload["to"] == ["victoria@tests.local"]
    assert payload["template"] == "event_assignment"
    assert payload["variables"]["name"] == "Victoria"
    assert payload["variables"]["event_date"] == "29/07/2026"
    assert payload["variables"]["when_label"] == "en una semana"
    assert "en una semana" in payload["subject"]
    # La API key viaja en el header (urllib capitaliza los nombres).
    assert capturado["headers"]["X-api-key"]


def _capturar_payload(monkeypatch) -> dict:
    """Devuelve un dict que, tras llamar al cron, contiene el payload enviado."""
    capturado: dict = {}

    def _falso(request, timeout=None):
        capturado.update(json.loads(request.data))
        return RespuestaFalsa()

    monkeypatch.setattr(alma_cron.urllib.request, "urlopen", _falso)
    return capturado


# ── Cómo se llama el encuentro ─────────────────────────────────────────────
# Lo pidió una usuaria: el mail decía solo "Actividad" y había que abrirlo para
# saber de qué se trataba.

def test_el_asunto_dice_como_se_llama_el_encuentro(monkeypatch, fila):
    fila["event_name"] = "Estimulación cognitiva"
    payload = _capturar_payload(monkeypatch)

    alma_cron.send_reminder(fila, 7)

    assert payload["subject"] == "Recordatorio: Estimulación cognitiva — en una semana (29/07)"
    assert payload["variables"]["event_name"] == "Estimulación cognitiva"
    assert payload["variables"]["event_label"] == "Taller"


def test_el_nombre_va_antes_que_el_cuando(monkeypatch, fila):
    """En el celular el asunto se corta a los ~35 caracteres. Lo que tiene que
    sobrevivir al corte es QUÉ es, no cuándo."""
    fila["event_name"] = "Estimulación cognitiva"
    payload = _capturar_payload(monkeypatch)

    alma_cron.send_reminder(fila, 7)

    asunto = payload["subject"]
    assert asunto.index("Estimulación cognitiva") < asunto.index("en una semana")


def test_evento_sin_nombre_propio_cae_al_tipo(monkeypatch, fila):
    """Evento suelto, sin source_id ni title: el mail sale igual, con el tipo
    como nombre. La etiqueta de arriba va VACÍA para que no se lea
    'TALLER / Taller'."""
    fila["event_name"] = None
    payload = _capturar_payload(monkeypatch)

    alma_cron.send_reminder(fila, 1)

    assert payload["variables"]["event_name"] == "Taller"
    assert payload["variables"]["event_label"] == ""
    assert payload["subject"] == "Recordatorio: Taller — mañana (29/07)"


@pytest.mark.parametrize("tipo,audiencia,esperado", [
    ("taller",    alma_cron.VOLUNTARIO,   "un taller asignado"),
    ("taller",    alma_cron.PARTICIPANTE, "un taller"),
    ("actividad", alma_cron.VOLUNTARIO,   "una actividad asignada"),
    ("actividad", alma_cron.PARTICIPANTE, "una actividad"),
    ("grupo",     alma_cron.VOLUNTARIO,   "un grupo de apoyo asignado"),
    ("grupo",     alma_cron.PARTICIPANTE, "un grupo de apoyo"),
])
def test_la_frase_concuerda_con_quien_recibe_y_con_el_genero(
    monkeypatch, fila, tipo, audiencia, esperado,
):
    """Dos ejes a la vez: al voluntario se lo asignaron y el participante se
    anotó solo; y el género lo manda el tipo ("un taller asignado" pero "una
    actividad asignada"). Por eso varía la frase entera y no la palabra suelta.
    """
    fila["event_type"] = tipo
    payload = _capturar_payload(monkeypatch)

    alma_cron.send_reminder(fila, 1, audiencia)

    assert payload["variables"]["event_phrase"] == esperado


def test_el_texto_libre_se_escapa(monkeypatch, fila):
    """_render() del backend hace un str.replace pelado, sin escapar: un '&' o
    un '<' cargado a mano llegaría crudo al HTML del mail."""
    fila["event_name"] = "Memoria & Movimiento"
    fila["notes"] = "Sala <2>"
    payload = _capturar_payload(monkeypatch)

    alma_cron.send_reminder(fila, 1)

    assert payload["variables"]["event_name"] == "Memoria &amp; Movimiento"
    assert payload["variables"]["notes"] == "Sala &lt;2&gt;"
    # El asunto es una cabecera, no HTML: ahí "&amp;" se leería literal.
    assert "Memoria & Movimiento" in payload["subject"]


def test_el_timeout_es_generoso():
    """Con 20s se pasaba mientras Resend todavía respondía, y eso disparaba el
    ciclo de alerta falsa + duplicado. No bajarlo sin releer send_reminder()."""
    assert alma_cron.EMAIL_REQUEST_TIMEOUT >= 60


# ── Parseo de offsets ──────────────────────────────────────────────────────

@pytest.mark.parametrize("entrada,esperado", [
    ("[7, 1, 0]", [7, 1, 0]),      # viene como string JSON
    ([7, 1, 0], [7, 1, 0]),        # o ya parseado, según el driver
    ("[]", []),
    (None, []),
    ("no soy json", []),           # no debe romper la corrida
    ('[7, "1", "x"]', [7, 1]),     # descarta lo que no sea número
])
def test_parse_offsets(entrada, esperado):
    assert alma_cron.parse_offsets(entrada) == esperado


# ── Formato de los logs ────────────────────────────────────────────────────
# Los lee una persona a la mañana: si dejan de entenderse, el cron pierde la
# mitad de su valor.

def test_la_descripcion_se_entiende_sola(fila):
    assert alma_cron._describe(fila, 1) == "Victoria Rébori — Taller 29/07 (es mañana)"
    assert alma_cron._describe(fila, 7) == "Victoria Rébori — Taller 29/07 (es en una semana)"
    assert alma_cron._describe(fila, 0) == "Victoria Rébori — Taller 29/07 (es hoy)"


def test_la_descripcion_usa_el_nombre_del_encuentro(fila):
    """El log de la mañana también gana con el nombre: 'Taller 29/07' no dice
    cuál de los talleres era."""
    fila["event_name"] = "Estimulación cognitiva"
    assert alma_cron._describe(fila, 1) == "Victoria Rébori — Estimulación cognitiva 29/07 (es mañana)"


def test_la_descripcion_aguanta_apellido_vacio(fila):
    fila["last_name"] = None
    assert alma_cron._describe(fila, 0) == "Victoria — Taller 29/07 (es hoy)"


def test_la_fecha_va_en_castellano():
    assert alma_cron._human_date(date(2026, 7, 28)) == "martes 28/07/2026"
    assert alma_cron._human_date(date(2026, 8, 2)) == "domingo 02/08/2026"


def test_los_plurales_concuerdan():
    assert alma_cron._plural(1, "aviso enviado", "avisos enviados") == "1 aviso enviado"
    assert alma_cron._plural(3, "aviso enviado", "avisos enviados") == "3 avisos enviados"
    assert alma_cron._plural(0, "aviso enviado", "avisos enviados") == "0 avisos enviados"


# ── Cumpleaños ─────────────────────────────────────────────────────────────
#
# Lo que importa acá es lo mismo que en los recordatorios —no mandar dos veces—
# más dos reglas propias: a quién se saluda y qué día.

@pytest.fixture()
def cumpleanero():
    """Una fila como la que devuelve fetch_birthdays."""
    return {
        "volunteer_id": 33,
        "name": "Victoria",
        "last_name": "Rébori",
        "email": "victoria@tests.local",
        "birth_date": date(1985, 7, 28),
    }


def test_el_saludo_usa_la_plantilla_de_cumpleanos(monkeypatch, cumpleanero):
    payload = {}

    def _falso(request, timeout=None):
        payload.update(json.loads(request.data))
        return RespuestaFalsa(201, {"status": "sent"})

    monkeypatch.setattr(alma_cron.urllib.request, "urlopen", _falso)
    resultado, _ = alma_cron.send_birthday(cumpleanero)

    assert resultado == alma_cron.ACCEPTED
    assert payload["template"] == "birthday"
    assert payload["to"] == ["victoria@tests.local"]
    assert payload["variables"]["name"] == "Victoria"
    # Sin fecha ni apellido: en el celular el asunto se corta a los ~35.
    assert payload["subject"] == "¡Feliz cumpleaños!"


def test_el_nombre_se_escapa_para_el_html(monkeypatch):
    """_render hace un replace pelado: un & crudo rompería el HTML del mail."""
    payload = {}

    def _falso(request, timeout=None):
        payload.update(json.loads(request.data))
        return RespuestaFalsa(201, {"status": "sent"})

    monkeypatch.setattr(alma_cron.urllib.request, "urlopen", _falso)
    alma_cron.send_birthday({
        "volunteer_id": 1, "name": "Ana & Luis", "last_name": "", "email": "a@tests.local",
    })

    assert payload["variables"]["name"] == "Ana &amp; Luis"


def test_el_timeout_del_saludo_no_se_reintenta(monkeypatch, cumpleanero):
    """Mismo criterio que los recordatorios: ante la duda, no mandar de nuevo.

    Un "¡Feliz cumpleaños!" duplicado el mismo día es peor que uno que falta:
    se lee como que nadie está mirando el sistema.
    """
    _responder_con(monkeypatch, TimeoutError("se acabó el tiempo"))

    resultado, motivo = alma_cron.send_birthday(cumpleanero)

    assert resultado == alma_cron.UNKNOWN
    assert "no respondió" in motivo


def test_si_resend_lo_rechaza_se_puede_reintentar(monkeypatch, cumpleanero):
    _responder_con(monkeypatch, RespuestaFalsa(201, {
        "status": "failed", "error_message": "destinatario inválido",
    }))

    resultado, motivo = alma_cron.send_birthday(cumpleanero)

    assert resultado == alma_cron.REJECTED
    assert "destinatario inválido" in motivo


# ── Qué día se saluda ──────────────────────────────────────────────────────

class ConsultaFalsa:
    """Cursor de pymysql de mentira: guarda el SQL y los parámetros."""

    def __init__(self):
        self.sql = ""
        self.params = ()

    def execute(self, sql, params=()):
        self.sql, self.params = sql, params

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ConexionFalsa:
    def __init__(self):
        self.cur = ConsultaFalsa()

    def cursor(self):
        return self.cur

    def commit(self):
        pass


def _consulta_del_dia(hoy: date) -> ConsultaFalsa:
    conn = ConexionFalsa()
    alma_cron.fetch_birthdays(conn, hoy)
    return conn.cur


def test_busca_a_los_que_cumplen_hoy():
    cur = _consulta_del_dia(date(2026, 7, 28))

    assert cur.params[:2] == (7, 28)
    assert "MONTH(v.birth_date)" in cur.sql
    assert "29" not in cur.sql  # sin la excepción del bisiesto


def test_solo_saluda_a_los_activos():
    cur = _consulta_del_dia(date(2026, 7, 28))

    # Un pendiente no fue aprobado todavía y un inactivo se fue: a los dos el
    # saludo les llegaría de una organización en la que no están.
    assert "activo" in cur.params
    assert "pendiente" not in cur.params
    assert "inactivo" not in cur.params
    assert "v.birth_date IS NOT NULL" in cur.sql


def test_al_que_nacio_un_29_de_febrero_se_lo_saluda_el_28():
    """2026 no es bisiesto: el 28 entra también el 29/02."""
    cur = _consulta_del_dia(date(2026, 2, 28))

    assert "DAY(v.birth_date) = 29" in cur.sql


def test_en_un_bisiesto_el_28_no_arrastra_a_los_del_29():
    """2028 sí es bisiesto: los del 29 tienen su propio día al día siguiente."""
    cur = _consulta_del_dia(date(2028, 2, 28))

    assert "DAY(v.birth_date) = 29" not in cur.sql


def test_el_año_2100_no_es_bisiesto_aunque_sea_multiplo_de_100():
    cur = _consulta_del_dia(date(2100, 2, 28))

    assert "DAY(v.birth_date) = 29" in cur.sql


def test_el_año_2000_si_es_bisiesto_por_ser_multiplo_de_400():
    cur = _consulta_del_dia(date(2000, 2, 28))

    assert "DAY(v.birth_date) = 29" not in cur.sql


# ── El modo prueba ─────────────────────────────────────────────────────────

def test_sin_la_variable_el_mail_va_a_su_destinatario():
    assert alma_cron.destinatario("real@alma.org.ar") == ["real@alma.org.ar"]


def test_con_la_variable_todos_los_mails_se_desvian(monkeypatch):
    """CRON_EMAIL_PRUEBA existe para poder correr el cron de verdad sin
    escribirle a voluntarias reales."""
    monkeypatch.setattr(alma_cron, "EMAIL_PRUEBA", "manunovo@gmail.com")

    assert alma_cron.destinatario("real@alma.org.ar") == ["manunovo@gmail.com"]

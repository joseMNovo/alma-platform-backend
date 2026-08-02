"""
Tests de los cimientos: verifican que el AISLAMIENTO funcione.

Si algo de este archivo falla, no confíes en el resto de la suite: significa que
los tests podrían estar tocando la base real o mandando mails de verdad.
"""
import socket

import pytest

from app.schemas.email_log import SendEmailRequest
from app.services import email_service


def test_la_base_es_sqlite_en_memoria(db):
    """Ni por accidente MySQL."""
    url = db.get_bind().url
    assert url.drivername.startswith("sqlite"), f"La base de tests no es SQLite: {url}"
    assert url.database in (None, ":memory:"), "La base de tests no está en memoria"


def test_cada_test_arranca_con_la_base_vacia(db):
    """El test de al lado crea un voluntario; este no debe verlo."""
    from app.models.voluntario import Voluntario

    assert db.query(Voluntario).count() == 0


def test_cada_test_arranca_con_la_base_vacia_bis(db, voluntario):
    from app.models.voluntario import Voluntario

    assert db.query(Voluntario).count() == 1


def test_no_se_puede_salir_a_internet():
    """El candado de red tiene que cortar cualquier conexión de salida."""
    with pytest.raises(RuntimeError, match="BLOQUEADO"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("api.resend.com", 443))


def test_los_mails_no_salen_pero_quedan_registrados(db, buzon):
    """El envío se intercepta: nadie recibe nada, pero el test puede verificarlo."""
    email_service.send_email(db, SendEmailRequest(
        to=["destinatario@ejemplo.com"],
        subject="Hola",
        template="verification",
        variables={"name": "Ana"},
    ))

    assert buzon.destinatarios == ["destinatario@ejemplo.com"]
    assert buzon.ultimo_email()["subject"] == "Hola"
    assert buzon.ultimo_email()["variables"]["name"] == "Ana"


def test_el_envio_queda_registrado_en_email_logs(db, buzon):
    from app.models.email_log import EmailLog

    email_service.send_email(db, SendEmailRequest(
        to=["a@ejemplo.com"], subject="Asunto", template="verification",
    ))

    log = db.query(EmailLog).one()
    assert log.status == "sent"
    assert log.to_addresses == ["a@ejemplo.com"]
    assert log.resend_id is not None


def test_se_puede_simular_que_resend_rechaza(db, buzon):
    """Para poder testear los caminos de error sin depender de Resend."""
    from app.models.email_log import EmailLog

    buzon.fallar_emails = True
    email_service.send_email(db, SendEmailRequest(to=["a@ejemplo.com"], subject="x"))

    log = db.query(EmailLog).one()
    assert log.status == "failed"
    assert "simulado" in log.error_message


def test_la_config_es_la_de_test_no_la_real():
    """Si esto falla, los tests están leyendo el .env de producción."""
    from config import settings

    assert settings.RESEND_API_KEY == "", "¡La API key real de Resend está cargada en los tests!"
    assert settings.INTERNAL_API_KEY == "clave-solo-para-tests"
    assert settings.DB_NAME == "no_existe_a_proposito"


def test_la_api_responde(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_sin_api_key_no_se_entra(client):
    r = client.get("/voluntarios/", headers={"X-API-Key": "clave-equivocada"})
    assert r.status_code == 403

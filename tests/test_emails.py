"""
Tests del envío de emails: el endpoint /emails/send y el servicio que hay debajo.

Todo lo de acá pasa por el doble de Resend (ver conftest): se verifica QUÉ se
habría mandado y qué quedó registrado en email_logs, sin que salga nada.
"""
from app.models.email_log import EmailLog
from app.schemas.email_log import SendEmailRequest
from app.services import email_service


# ── El endpoint ────────────────────────────────────────────────────────────

def test_send_manda_el_mail_y_devuelve_el_log(client, buzon):
    r = client.post("/emails/send", json={
        "to": ["persona@ejemplo.com"],
        "subject": "Bienvenida",
        "template": "verification",
        "variables": {"name": "Ana", "verification_url": "http://x", "expiry": "30 minutos"},
    })

    assert r.status_code == 201
    cuerpo = r.json()
    assert cuerpo["status"] == "sent"
    assert cuerpo["to_addresses"] == ["persona@ejemplo.com"]
    assert cuerpo["resend_id"]
    assert buzon.destinatarios == ["persona@ejemplo.com"]


def test_send_registra_el_fallo_cuando_resend_rechaza(client, db, buzon):
    """Un rechazo de Resend NO es un error HTTP: queda como 'failed' en el log.

    Es exactamente por esto que el cron mira el `status` del cuerpo y no solo el
    código HTTP (ver test_cron.py).
    """
    buzon.fallar_emails = True

    r = client.post("/emails/send", json={"to": ["a@ejemplo.com"], "subject": "Hola"})

    assert r.status_code == 201, "El endpoint responde 201 aunque Resend falle"
    assert r.json()["status"] == "failed"
    assert db.query(EmailLog).one().status == "failed"


def test_send_acepta_varios_destinatarios_y_copias(client, buzon):
    r = client.post("/emails/send", json={
        "to": ["uno@ejemplo.com", "dos@ejemplo.com"],
        "cc": ["copia@ejemplo.com"],
        "subject": "Aviso",
    })

    assert r.status_code == 201
    assert buzon.ultimo_email()["to"] == ["uno@ejemplo.com", "dos@ejemplo.com"]
    assert buzon.ultimo_email()["cc"] == ["copia@ejemplo.com"]


def test_send_exige_destinatario_y_asunto(client):
    assert client.post("/emails/send", json={"subject": "Sin destinatario"}).status_code == 422
    assert client.post("/emails/send", json={"to": ["a@b.com"]}).status_code == 422


# ── El log ─────────────────────────────────────────────────────────────────

def test_logs_devuelve_lo_enviado_con_lo_mas_nuevo_primero(client):
    for i in range(3):
        client.post("/emails/send", json={"to": [f"{i}@ejemplo.com"], "subject": f"Mail {i}"})

    logs = client.get("/emails/logs").json()

    assert len(logs) == 3
    assert [l["subject"] for l in logs] == ["Mail 2", "Mail 1", "Mail 0"]


def test_logs_se_pueden_filtrar(client, buzon):
    client.post("/emails/send", json={"to": ["a@b.com"], "subject": "O", "template": "verification"})
    buzon.fallar_emails = True
    client.post("/emails/send", json={"to": ["c@d.com"], "subject": "K", "template": "pin_reset"})

    assert len(client.get("/emails/logs?status=sent").json()) == 1
    assert len(client.get("/emails/logs?status=failed").json()) == 1
    assert len(client.get("/emails/logs?template=pin_reset").json()) == 1


# ── El armado del HTML ─────────────────────────────────────────────────────

def test_las_variables_se_reemplazan_en_la_plantilla():
    html = email_service._render("verification", {"name": "Ana", "expiry": "30 minutos"}, None)

    assert "Ana" in html
    assert "30 minutos" in html
    assert "{{name}}" not in html, "Quedó un placeholder sin reemplazar"


def test_toda_plantilla_conocida_se_renderiza():
    """Si alguien agrega una plantilla rota, este test la agarra."""
    for nombre in email_service._BODIES:
        html = email_service._render(nombre, {}, None)
        assert "<html" in html.lower()
        assert "almarosario.org.ar" in html, f"La plantilla {nombre} perdió el pie de página"


def test_sin_plantilla_usa_el_cuerpo_libre():
    html = email_service._render(None, {}, "<p>Texto suelto</p>")
    assert "<p>Texto suelto</p>" in html


def test_sin_plantilla_ni_cuerpo_no_rompe():
    assert "Sin contenido" in email_service._render(None, {}, None)


# ── El envío en background ─────────────────────────────────────────────────

def test_send_email_bg_abre_su_propia_sesion(db, buzon):
    """send_email_bg corre cuando la sesión del request ya se cerró, así que
    tiene que abrir la suya. Si volviera a depender de la del request, esto
    fallaría."""
    email_service.send_email_bg(SendEmailRequest(to=["fondo@ejemplo.com"], subject="En background"))

    assert buzon.destinatarios == ["fondo@ejemplo.com"]


def test_send_email_bg_nunca_lanza(monkeypatch, buzon):
    """Una tarea de background que revienta no tiene a nadie que la escuche: el
    fallo se loguea, no se propaga."""
    def _explota(req):
        raise RuntimeError("Resend se cayó")

    monkeypatch.setattr(email_service, "_deliver_to_resend", _explota)

    email_service.send_email_bg(SendEmailRequest(to=["a@b.com"], subject="x"))  # no debe lanzar

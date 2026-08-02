"""
Tests de la campanita y el push.

La regla del módulo: la notificación in-app se guarda SIEMPRE, y el push es
best-effort. Nadie se pierde un aviso porque FCM o Apple estén caídos.
"""
import pytest

from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.services import notification_service


@pytest.fixture()
def con_dispositivo(db):
    """Le registra un dispositivo al voluntario 1, para que haya adónde mandar push."""
    def _registrar(user_type="voluntario", user_id=1, endpoint="https://fcm.example/abc"):
        sub = PushSubscription(
            user_type=user_type, user_id=user_id,
            endpoint=endpoint, p256dh="clave-p256", auth="clave-auth",
        )
        db.add(sub)
        db.commit()
        return sub
    return _registrar


# ── /notifications/notify ──────────────────────────────────────────────────

def test_notify_guarda_la_campanita(client, db):
    r = client.post("/notifications/notify", json={
        "user_type": "voluntario", "user_id": 1,
        "title": "Recordatorio", "body": "Es mañana", "kind": "calendar_reminder",
        "url": "/calendarios",
    })

    assert r.status_code == 201
    assert r.json()["title"] == "Recordatorio"
    assert r.json()["is_read"] is False

    notif = db.query(Notification).one()
    assert notif.user_id == 1
    assert notif.kind == "calendar_reminder"


def test_notify_manda_el_push(client, buzon, con_dispositivo):
    """El push sale en background (TestClient corre los background tasks al
    terminar la respuesta), pero sale."""
    con_dispositivo()

    client.post("/notifications/notify", json={
        "user_type": "voluntario", "user_id": 1, "title": "Hola",
    })

    assert len(buzon.push) == 1, "No se disparó el push al dispositivo registrado"


def test_notify_sin_dispositivos_no_rompe(client, buzon):
    r = client.post("/notifications/notify", json={
        "user_type": "voluntario", "user_id": 99, "title": "Nadie escucha",
    })

    assert r.status_code == 201
    assert buzon.push == []


def test_si_el_push_falla_la_campanita_igual_queda(client, db, monkeypatch, con_dispositivo):
    """Lo que garantiza que un problema con FCM no haga perder el aviso."""
    con_dispositivo()

    def _push_roto(sub_info, payload):
        raise RuntimeError("FCM caído")

    monkeypatch.setattr("app.services.push_service._send_one", _push_roto)

    r = client.post("/notifications/notify", json={
        "user_type": "voluntario", "user_id": 1, "title": "Igual llega",
    })

    assert r.status_code == 201
    assert db.query(Notification).count() == 1


def test_notify_valida_los_datos(client):
    # user_type que no existe
    assert client.post("/notifications/notify", json={
        "user_type": "marciano", "user_id": 1, "title": "x"}).status_code == 422
    # kind que no existe
    assert client.post("/notifications/notify", json={
        "user_type": "voluntario", "user_id": 1, "title": "x", "kind": "inventado"}).status_code == 422
    # título vacío
    assert client.post("/notifications/notify", json={
        "user_type": "voluntario", "user_id": 1, "title": "   "}).status_code == 422


# ── Broadcast ──────────────────────────────────────────────────────────────

def test_broadcast_le_llega_a_los_voluntarios(client, db, crear_voluntario):
    crear_voluntario()
    crear_voluntario()

    r = client.post("/notifications/broadcast", json={
        "title": "Aviso general", "body": "Reunión el viernes", "audience": "voluntario",
    })

    assert r.status_code == 201
    assert r.json()["recipients"] == 2
    assert db.query(Notification).count() == 2


def test_broadcast_a_voluntarios_puntuales(client, db, crear_voluntario):
    v1 = crear_voluntario()
    crear_voluntario()

    r = client.post("/notifications/broadcast", json={
        "title": "Solo para vos", "volunteer_ids": [v1.id],
    })

    assert r.json()["recipients"] == 1
    assert db.query(Notification).one().user_id == v1.id


def test_broadcast_no_repite_destinatarios(client, db, crear_voluntario):
    v1 = crear_voluntario()

    client.post("/notifications/broadcast", json={
        "title": "Una sola vez", "volunteer_ids": [v1.id, v1.id, v1.id],
    })

    assert db.query(Notification).count() == 1


def test_broadcast_exige_titulo(client):
    assert client.post("/notifications/broadcast", json={"title": "  "}).status_code == 422


# ── Leído / no leído ───────────────────────────────────────────────────────

def test_contador_de_no_leidas(client, crear_voluntario):
    v = crear_voluntario()
    for i in range(3):
        client.post("/notifications/notify", json={
            "user_type": "voluntario", "user_id": v.id, "title": f"Aviso {i}"})

    r = client.get(f"/notifications/unread-count?user_type=voluntario&user_id={v.id}")

    assert r.status_code == 200
    assert r.json()["unread"] == 3


def test_marcar_como_leidas(client, crear_voluntario):
    v = crear_voluntario()
    client.post("/notifications/notify", json={
        "user_type": "voluntario", "user_id": v.id, "title": "Leeme"})

    client.post(f"/notifications/mark-read?user_type=voluntario&user_id={v.id}")

    r = client.get(f"/notifications/unread-count?user_type=voluntario&user_id={v.id}")
    assert r.json()["unread"] == 0


def test_cada_uno_ve_solo_lo_suyo(client, crear_voluntario):
    """Que las notificaciones no se crucen entre usuarios."""
    v1, v2 = crear_voluntario(), crear_voluntario()
    client.post("/notifications/notify", json={
        "user_type": "voluntario", "user_id": v1.id, "title": "Para v1"})

    assert client.get(f"/notifications/unread-count?user_type=voluntario&user_id={v1.id}").json()["unread"] == 1
    assert client.get(f"/notifications/unread-count?user_type=voluntario&user_id={v2.id}").json()["unread"] == 0


# ── El helper de background ────────────────────────────────────────────────

def test_send_broadcast_push_abre_su_propia_sesion(buzon, con_dispositivo):
    """Corre después de que la sesión del request se cerró, así que abre la suya.
    Es el mismo helper que ahora usa /notify para un solo destinatario."""
    con_dispositivo()

    enviados = notification_service.send_broadcast_push(
        [("voluntario", 1)], "Título", "Cuerpo", "/calendarios",
    )

    assert enviados == 1
    assert len(buzon.push) == 1

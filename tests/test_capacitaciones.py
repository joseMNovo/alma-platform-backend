"""
Capacitaciones: el catálogo, su contenido y —sobre todo— quién puede verlo.

Este módulo vende acceso, así que el test más importante no es el CRUD sino el
candado: una persona SIN habilitación no puede recibir el `video_ref`, el `body`
ni el `file_url`. Esa frontera está en `_serialize_item()`, no en la UI: aunque
alguien llame la API a mano, el contenido no tiene que viajar.

Reglas de acceso (de `_access_for`):
  - staff (admin/voluntario)     → ve todo, gratis
  - access_mode = "abierta"      → cualquier autenticado ve el contenido
  - access_mode = "grant"        → solo con una habilitación vigente
  - sin sesión                   → nada
"""
from datetime import datetime, timedelta

import pytest

from app.models.access import PersonAccessGrant
from app.models.participant import Participant, ParticipantProfile
from app.routers.capacitaciones import MODULE_KEY


# El video_ref tiene que ser un ID de YouTube válido (11 caracteres): el router
# lo valida al guardar y rechaza cualquier otra cosa con un 422.
VIDEO_ID = "aBcDeFgHiJk"

CONTENIDO = {
    "kind": "video",
    "title": "Clase 1",
    "provider": "youtube",
    "video_ref": VIDEO_ID,
    "body": "Texto pago de la clase",
    "file_url": "https://ejemplo.com/apunte-pago.pdf",
}


@pytest.fixture()
def capacitacion(client):
    """Una capacitación publicada, paga, con una clase adentro."""
    t = client.post("/capacitaciones/", json={
        "title": "Cuidado del adulto mayor",
        "description": "Curso completo",
        "price": "15000.00",
        "status": "publicada",
        "access_mode": "grant",
    }).json()
    client.post(f"/capacitaciones/{t['id']}/items", json=CONTENIDO)
    return t


@pytest.fixture()
def persona_con_login(db):
    """Una persona del registro maestro que además tiene cuenta para entrar."""
    def _crear(email="alumna@ejemplo.com"):
        login = Participant(email=email, is_active=True, email_verified=True)
        db.add(login)
        db.commit()
        db.refresh(login)

        perfil = ParticipantProfile(name="Alumna", email=email, participant_id=login.id)
        db.add(perfil)
        db.commit()
        db.refresh(perfil)
        return login, perfil
    return _crear


@pytest.fixture()
def habilitar(db):
    """Le da acceso vigente a una persona sobre una capacitación."""
    def _habilitar(person_id: int, training_id: int, expires_at=None):
        grant = PersonAccessGrant(
            person_id=person_id,
            module_key=MODULE_KEY,
            resource_id=training_id,
            is_active=True,
            expires_at=expires_at,
        )
        db.add(grant)
        db.commit()
        return grant
    return _habilitar


# ── El candado del contenido pago ──────────────────────────────────────────

def _clase(respuesta_json):
    return respuesta_json["items"][0]


def test_sin_sesion_el_contenido_viene_bloqueado(client, capacitacion):
    r = client.get(f"/capacitaciones/{capacitacion['id']}")

    clase = _clase(r.json())
    assert clase["locked"] is True
    assert clase["video_ref"] is None, "¡Se filtró el ID del video sin sesión!"
    assert clase["body"] is None
    assert clase["file_url"] is None


def test_sin_sesion_el_temario_igual_se_ve(client, capacitacion):
    """La landing tiene que poder mostrar QUÉ se aprende, sin dar el contenido."""
    clase = _clase(client.get(f"/capacitaciones/{capacitacion['id']}").json())

    assert clase["title"] == "Clase 1"
    assert _clase(client.get(f"/capacitaciones/{capacitacion['id']}").json())["kind"] == "video"


def test_un_participante_sin_habilitacion_no_ve_el_contenido(
    client, capacitacion, persona_con_login,
):
    """El caso que importa: alguien logueado pero que no pagó."""
    login, _perfil = persona_con_login()

    r = client.get(
        f"/capacitaciones/{capacitacion['id']}"
        f"?user_type=participante&user_id={login.id}"
    )

    clase = _clase(r.json())
    assert r.json()["has_access"] is False
    assert clase["locked"] is True
    assert clase["video_ref"] is None, "¡Un participante sin pagar recibió el video!"


def test_un_participante_habilitado_si_ve_el_contenido(
    client, capacitacion, persona_con_login, habilitar,
):
    login, perfil = persona_con_login()
    habilitar(perfil.id, capacitacion["id"])

    r = client.get(
        f"/capacitaciones/{capacitacion['id']}"
        f"?user_type=participante&user_id={login.id}"
    )

    clase = _clase(r.json())
    assert r.json()["has_access"] is True
    assert clase["locked"] is False
    assert clase["video_ref"] == CONTENIDO["video_ref"]
    assert clase["body"] == CONTENIDO["body"]


def test_una_habilitacion_vencida_ya_no_da_acceso(
    client, capacitacion, persona_con_login, habilitar,
):
    """El vencimiento se evalúa al consultar, sin cron: una habilitación vencida
    deja de servir sola."""
    login, perfil = persona_con_login()
    habilitar(perfil.id, capacitacion["id"], expires_at=datetime.now() - timedelta(days=1))

    r = client.get(
        f"/capacitaciones/{capacitacion['id']}"
        f"?user_type=participante&user_id={login.id}"
    )

    assert r.json()["has_access"] is False
    assert _clase(r.json())["video_ref"] is None


def test_una_habilitacion_que_vence_mañana_todavia_sirve(
    client, capacitacion, persona_con_login, habilitar,
):
    login, perfil = persona_con_login()
    habilitar(perfil.id, capacitacion["id"], expires_at=datetime.now() + timedelta(days=1))

    r = client.get(
        f"/capacitaciones/{capacitacion['id']}"
        f"?user_type=participante&user_id={login.id}"
    )

    assert r.json()["has_access"] is True


def test_la_habilitacion_es_por_capacitacion_no_global(
    client, capacitacion, persona_con_login, habilitar,
):
    """Pagar un curso no abre los demás."""
    otra = client.post("/capacitaciones/", json={
        "title": "Otro curso", "status": "publicada", "access_mode": "grant"}).json()
    client.post(f"/capacitaciones/{otra['id']}/items", json=CONTENIDO)

    login, perfil = persona_con_login()
    habilitar(perfil.id, capacitacion["id"])

    r = client.get(f"/capacitaciones/{otra['id']}?user_type=participante&user_id={login.id}")

    assert r.json()["has_access"] is False
    assert _clase(r.json())["video_ref"] is None


def test_el_staff_ve_todo_sin_pagar(client, capacitacion, voluntario):
    """Voluntarios y admin entran gratis: el rol ya los habilita."""
    r = client.get(
        f"/capacitaciones/{capacitacion['id']}"
        f"?user_type=voluntario&user_id={voluntario.id}"
    )

    assert r.json()["has_access"] is True
    assert _clase(r.json())["video_ref"] == CONTENIDO["video_ref"]


def test_una_capacitacion_abierta_la_ve_cualquier_autenticado(
    client, persona_con_login,
):
    abierta = client.post("/capacitaciones/", json={
        "title": "Charla gratuita", "status": "publicada", "access_mode": "abierta"}).json()
    client.post(f"/capacitaciones/{abierta['id']}/items", json=CONTENIDO)
    login, _ = persona_con_login()

    r = client.get(f"/capacitaciones/{abierta['id']}?user_type=participante&user_id={login.id}")

    assert r.json()["has_access"] is True
    assert _clase(r.json())["video_ref"] == CONTENIDO["video_ref"]


# ── Landing pública ────────────────────────────────────────────────────────

def test_la_landing_publica_muestra_el_temario_sin_el_contenido(client, capacitacion):
    r = client.get(f"/capacitaciones/publica/{capacitacion['slug']}")

    assert r.status_code == 200
    assert r.json()["title"] == "Cuidado del adulto mayor"
    clase = _clase(r.json())
    assert clase["title"] == "Clase 1", "El temario tiene que verse para poder vender"
    assert clase["locked"] is True
    assert clase["video_ref"] is None, "¡La landing pública filtró el contenido pago!"


def test_la_landing_publica_no_muestra_borradores(client):
    borrador = client.post("/capacitaciones/", json={
        "title": "Todavía no sale", "status": "borrador"}).json()

    assert client.get(f"/capacitaciones/publica/{borrador['slug']}").status_code == 404


def test_la_landing_de_un_slug_inventado_da_404(client):
    assert client.get("/capacitaciones/publica/no-existe").status_code == 404


# ── ABM ────────────────────────────────────────────────────────────────────

def test_crear_capacitacion(client):
    r = client.post("/capacitaciones/", json={"title": "Curso nuevo"})

    assert r.status_code == 201
    assert r.json()["title"] == "Curso nuevo"
    assert r.json()["status"] == "borrador", "Una capacitación nace en borrador"


def test_el_slug_se_genera_del_titulo(client):
    r = client.post("/capacitaciones/", json={"title": "Cuidado del Adulto Mayor"})

    assert r.json()["slug"] == "cuidado-del-adulto-mayor"


def test_dos_titulos_iguales_no_chocan_de_slug(client):
    """El slug es único (va en la URL pública): el segundo se desambigua solo."""
    primero = client.post("/capacitaciones/", json={"title": "Mismo título"}).json()
    segundo = client.post("/capacitaciones/", json={"title": "Mismo título"}).json()

    assert primero["slug"] != segundo["slug"]


def test_editar_capacitacion(client, capacitacion):
    r = client.put(f"/capacitaciones/{capacitacion['id']}", json={"title": "Título corregido"})

    assert r.status_code == 200
    assert r.json()["title"] == "Título corregido"


def test_borrar_capacitacion_se_lleva_su_contenido(client, db, capacitacion):
    """Cascada: los ítems no pueden quedar huérfanos."""
    from app.models.training import TrainingItem

    assert db.query(TrainingItem).count() == 1

    assert client.delete(f"/capacitaciones/{capacitacion['id']}").status_code == 204

    assert client.get(f"/capacitaciones/{capacitacion['id']}").status_code == 404
    assert db.query(TrainingItem).count() == 0


def test_404_en_una_capacitacion_que_no_existe(client):
    assert client.get("/capacitaciones/99999").status_code == 404
    assert client.put("/capacitaciones/99999", json={"title": "X"}).status_code == 404
    assert client.delete("/capacitaciones/99999").status_code == 404


def test_el_catalogo_filtra_por_estado(client):
    client.post("/capacitaciones/", json={"title": "Publicada", "status": "publicada"})
    client.post("/capacitaciones/", json={"title": "Borrador", "status": "borrador"})

    assert len(client.get("/capacitaciones/?status=publicada").json()) == 1
    assert len(client.get("/capacitaciones/").json()) == 2


# ── Contenido ──────────────────────────────────────────────────────────────

def test_agregar_contenido(client, capacitacion):
    r = client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "texto", "title": "Introducción", "body": "Bienvenidos"})

    assert r.status_code == 201
    assert r.json()["title"] == "Introducción"


def test_no_se_puede_agregar_contenido_a_algo_que_no_existe(client):
    r = client.post("/capacitaciones/99999/items", json={"kind": "texto", "title": "X"})

    assert r.status_code == 404


def test_el_contenido_valida_su_tipo(client, capacitacion):
    r = client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "holograma", "title": "X"})

    assert r.status_code == 422


def test_se_guarda_el_id_del_video_aunque_peguen_la_url(client, capacitacion):
    """El admin pega el link de YouTube completo; se guarda solo el ID pelado.
    Así cambiar de proveedor es cambiar provider + ref, sin migrar nada."""
    r = client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "video", "title": "Con URL",
        "video_ref": f"https://www.youtube.com/watch?v={VIDEO_ID}"})

    assert r.status_code == 201
    assert r.json()["video_ref"] == VIDEO_ID


def test_un_link_de_youtube_roto_se_rechaza(client, capacitacion):
    r = client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "video", "title": "Roto", "video_ref": "no-es-un-video"})

    assert r.status_code == 422


def test_el_contenido_sin_publicar_no_se_lista(client, capacitacion, voluntario):
    """Se puede dejar una clase a medio cargar sin que los alumnos la vean."""
    client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "texto", "title": "A medio hacer", "is_published": False})

    r = client.get(
        f"/capacitaciones/{capacitacion['id']}?user_type=voluntario&user_id={voluntario.id}")
    titulos = [i["title"] for i in r.json()["items"]]

    assert "A medio hacer" not in titulos
    assert "Clase 1" in titulos


def test_el_admin_puede_ver_el_contenido_sin_publicar(client, capacitacion, voluntario):
    """Para poder editarlo, claro."""
    client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "texto", "title": "A medio hacer", "is_published": False})

    r = client.get(
        f"/capacitaciones/{capacitacion['id']}"
        f"?user_type=voluntario&user_id={voluntario.id}&include_unpublished=true")

    assert "A medio hacer" in [i["title"] for i in r.json()["items"]]


def test_editar_y_borrar_contenido(client, capacitacion):
    item = client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "texto", "title": "Se va a ir"}).json()

    assert client.put(f"/capacitaciones/items/{item['id']}",
                      json={"title": "Renombrado"}).json()["title"] == "Renombrado"
    assert client.delete(f"/capacitaciones/items/{item['id']}").status_code == 204


def test_el_abm_no_notifica_a_nadie(client, capacitacion, buzon):
    client.put(f"/capacitaciones/{capacitacion['id']}", json={"title": "Otro"})

    assert buzon.emails == []
    assert buzon.push == []

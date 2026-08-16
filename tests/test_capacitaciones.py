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


# ── Introducción gratuita ──────────────────────────────────────────────────
# El único contenido que sale sin habilitación y sin sesión. Es una excepción
# deliberada al candado de más arriba, así que lo que hay que probar no es que
# funcione: es que NO se lleve puesto el resto.

INTRO_ID = "zZyYxXwWvVu"


@pytest.fixture()
def con_intro(client, capacitacion):
    """A la capacitación paga se le agrega una introducción abierta."""
    intro = client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "video",
        "title": "Introducción al curso",
        "provider": "youtube",
        "video_ref": INTRO_ID,
        "is_free_preview": True,
    }).json()
    return capacitacion, intro


def _por_titulo(respuesta_json, titulo):
    return next(i for i in respuesta_json["items"] if i["title"] == titulo)


def test_la_intro_se_ve_sin_sesion(client, con_intro):
    """El punto de la función: alguien que llegó de Instagram, sin cuenta."""
    capacitacion, _ = con_intro

    r = client.get(f"/capacitaciones/{capacitacion['id']}")

    intro = _por_titulo(r.json(), "Introducción al curso")
    assert intro["locked"] is False
    assert intro["video_ref"] == INTRO_ID
    assert intro["is_free_preview"] is True


def test_la_intro_no_destraba_el_resto(client, con_intro):
    """EL test importante. En la MISMA respuesta que trae la intro abierta, el
    contenido pago tiene que seguir bloqueado. Si esto se rompe, marcar una
    intro regala la capacitación entera."""
    capacitacion, _ = con_intro

    r = client.get(f"/capacitaciones/{capacitacion['id']}")

    clase = _por_titulo(r.json(), "Clase 1")
    assert clase["locked"] is True
    assert clase["video_ref"] is None, "¡Marcar una intro abrió el contenido pago!"
    assert clase["body"] is None
    assert clase["file_url"] is None


def test_la_landing_publica_sirve_la_intro(client, con_intro):
    """Es la página donde tiene que verse: la que ALMA reparte por redes."""
    capacitacion, _ = con_intro

    r = client.get(f"/capacitaciones/publica/{capacitacion['slug']}")

    assert _por_titulo(r.json(), "Introducción al curso")["video_ref"] == INTRO_ID
    assert _por_titulo(r.json(), "Clase 1")["video_ref"] is None


def test_una_intro_despublicada_no_se_filtra(client, capacitacion):
    """Marcada como intro pero oculta: la landing no la lista, así que su
    video_ref tampoco sale. Las dos condiciones tienen que darse juntas."""
    client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "video", "title": "Intro a medio hacer", "video_ref": INTRO_ID,
        "is_free_preview": True, "is_published": False})

    r = client.get(f"/capacitaciones/publica/{capacitacion['slug']}")

    assert "Intro a medio hacer" not in [i["title"] for i in r.json()["items"]]


def test_marcar_una_intro_nueva_desmarca_la_anterior(client, con_intro):
    """La base no lo impide (es un flag por fila): lo garantiza el backend, no
    la pantalla. Si esto se rompe, se van acumulando videos gratis sin que
    nadie lo note."""
    capacitacion, primera = con_intro

    segunda = client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "video", "title": "Otra intro", "video_ref": VIDEO_ID,
        "is_free_preview": True}).json()

    r = client.get(f"/capacitaciones/{capacitacion['id']}")
    marcadas = [i["title"] for i in r.json()["items"] if i["is_free_preview"]]

    assert marcadas == ["Otra intro"]
    assert segunda["is_free_preview"] is True
    assert _por_titulo(r.json(), "Introducción al curso")["locked"] is True, (
        "La intro vieja quedó desmarcada pero siguió sirviendo el video"
    )


def test_editar_un_item_para_marcarlo_como_intro_tambien_desmarca(client, con_intro):
    """Mismo cuidado por el otro camino: el PUT, no solo el alta."""
    capacitacion, _ = con_intro
    otra = client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "video", "title": "Clase 2", "video_ref": VIDEO_ID}).json()

    client.put(f"/capacitaciones/items/{otra['id']}", json={"is_free_preview": True})

    r = client.get(f"/capacitaciones/{capacitacion['id']}")
    assert [i["title"] for i in r.json()["items"] if i["is_free_preview"]] == ["Clase 2"]


def test_la_intro_no_se_mezcla_entre_capacitaciones(client, con_intro):
    """Desmarcar es por capacitación: marcar una intro en un curso no puede
    apagar la de otro."""
    capacitacion, _ = con_intro
    otra = client.post("/capacitaciones/", json={
        "title": "Curso aparte", "status": "publicada"}).json()
    client.post(f"/capacitaciones/{otra['id']}/items", json={
        "kind": "video", "title": "Su propia intro", "video_ref": VIDEO_ID,
        "is_free_preview": True})

    r = client.get(f"/capacitaciones/{capacitacion['id']}")

    assert _por_titulo(r.json(), "Introducción al curso")["is_free_preview"] is True


def test_no_se_puede_marcar_como_intro_algo_oculto(client, capacitacion):
    """Quedaría una capacitación que en el panel dice "Intro gratis" y en la
    landing no muestra nada. Mejor frenar que dejar el estado mudo."""
    oculto = client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "video", "title": "Oculto", "video_ref": VIDEO_ID,
        "is_published": False}).json()

    r = client.put(f"/capacitaciones/items/{oculto['id']}", json={"is_free_preview": True})

    assert r.status_code == 422
    assert "oculto" in r.json()["detail"].lower()


def test_no_se_puede_ocultar_la_intro(client, con_intro):
    """El mismo choque por el otro camino: el ojo. No se apaga la marca en
    silencio —la persona no la tocó—, se le pide el clic que falta."""
    _, intro = con_intro

    r = client.put(f"/capacitaciones/items/{intro['id']}", json={"is_published": False})

    assert r.status_code == 422


def test_ocultar_y_desmarcar_a_la_vez_si_se_puede(client, con_intro):
    """Lo que se prohíbe es el estado contradictorio, no cada campo por
    separado: si en el mismo movimiento deja de ser intro, ocultarlo es
    perfectamente válido."""
    _, intro = con_intro

    r = client.put(f"/capacitaciones/items/{intro['id']}",
                   json={"is_published": False, "is_free_preview": False})

    assert r.status_code == 200


def test_nacer_oculto_y_marcado_tampoco_se_permite(client, capacitacion):
    """Mismo control en el alta, no solo en la edición."""
    r = client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "video", "title": "Contradictorio", "video_ref": VIDEO_ID,
        "is_published": False, "is_free_preview": True})

    assert r.status_code == 422


def test_una_intro_sin_video_no_se_permite(client, capacitacion):
    """Marcar como introducción algo que no puede reproducirse deja la landing
    prometiendo un video que no existe."""
    r = client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "video", "title": "Sin video", "is_free_preview": True})

    assert r.status_code == 422


def test_el_catalogo_publico_avisa_que_hay_intro(client, con_intro):
    """La vidriera de /academia lista SIN ítems, así que necesita el dato
    calculado para poder mostrar el cartelito."""
    catalogo = client.get("/capacitaciones/publicas").json()

    con = next(t for t in catalogo if t["title"] == "Cuidado del adulto mayor")
    assert con["has_free_preview"] is True


def test_sin_intro_el_catalogo_no_lo_promete(client, capacitacion):
    catalogo = client.get("/capacitaciones/publicas").json()

    assert next(t for t in catalogo if t["id"] == capacitacion["id"])["has_free_preview"] is False


def test_se_cuenta_la_vista_anonima_de_la_intro(client, db, con_intro):
    """Sirve para saber si el anzuelo pica. person_id queda en NULL.

    Se comprueba LA FILA, no el código HTTP. `log_view` contesta 201 aunque el
    INSERT falle —se traga el error a propósito, para que un problema de
    telemetría no le rompa el video a quien está mirando—, así que un test que
    mire solo el status da verde con la tabla vacía. Pasó exactamente eso.
    """
    from app.models.training import TrainingItemView
    _, intro = con_intro

    r = client.post(f"/capacitaciones/items/{intro['id']}/view",
                    json={"user_type": "anonimo", "user_id": 0})

    assert r.status_code == 201
    fila = db.query(TrainingItemView).filter(
        TrainingItemView.training_item_id == intro["id"]).one()
    assert fila.user_type == "anonimo"
    assert fila.person_id is None


def test_el_admin_ve_cuantas_veces_se_miro_la_vista_previa(client, con_intro, voluntario):
    """Contarlas sin poder verlas no servía de nada: ese era el punto de
    contarlas."""
    capacitacion, intro = con_intro
    for _ in range(3):
        client.post(f"/capacitaciones/items/{intro['id']}/view",
                    json={"user_type": "anonimo", "user_id": 0})

    r = client.get(
        f"/capacitaciones/?user_type=voluntario&user_id={voluntario.id}&include_stats=true")

    curso = next(t for t in r.json() if t["id"] == capacitacion["id"])
    assert curso["free_preview_views"] == 3


def test_sin_pedir_estadisticas_no_se_calculan(client, con_intro, voluntario):
    """None y 0 no son lo mismo: "no se midió" vs "no la miró nadie". Un cero
    inventado en las vistas públicas sería una señal falsa."""
    capacitacion, _ = con_intro

    r = client.get(f"/capacitaciones/?user_type=voluntario&user_id={voluntario.id}")

    curso = next(t for t in r.json() if t["id"] == capacitacion["id"])
    assert curso["free_preview_views"] is None


def test_las_vistas_identificadas_no_cuentan_como_anzuelo(
    client, con_intro, voluntario, persona_con_login,
):
    """Quien está logueado ya entró: su reproducción no dice nada sobre si la
    vista previa atrae desconocidos, que es lo único que se quiere medir."""
    capacitacion, intro = con_intro
    login, _ = persona_con_login()
    client.post(f"/capacitaciones/items/{intro['id']}/view",
                json={"user_type": "participante", "user_id": login.id})

    r = client.get(
        f"/capacitaciones/?user_type=voluntario&user_id={voluntario.id}&include_stats=true")

    curso = next(t for t in r.json() if t["id"] == capacitacion["id"])
    assert curso["free_preview_views"] == 0


def test_un_anonimo_no_puede_registrar_vistas_de_contenido_pago(client, con_intro):
    """El endpoint es alcanzable desde una ruta pública: sin este candado sería
    una forma de escribir filas para cualquier contenido."""
    capacitacion, _ = con_intro
    paga = _por_titulo(client.get(f"/capacitaciones/{capacitacion['id']}").json(), "Clase 1")

    r = client.post(f"/capacitaciones/items/{paga['id']}/view",
                    json={"user_type": "anonimo", "user_id": 0})

    assert r.status_code == 403


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


def test_el_listado_de_administracion_muestra_lo_oculto(client, capacitacion, voluntario):
    """Ocultar un contenido no puede borrarlo del panel de quien administra.

    El ojo es el ÚNICO modo de volver a mostrarlo: si el ítem desaparece de la
    lista, esconder algo se vuelve un viaje de ida. El endpoint de a uno ya lo
    contemplaba, pero el panel se alimenta de ESTE listado.
    """
    client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "texto", "title": "Escondido", "is_published": False})

    r = client.get(
        f"/capacitaciones/?user_type=voluntario&user_id={voluntario.id}"
        f"&include_items=true&include_unpublished=true")

    curso = next(t for t in r.json() if t["id"] == capacitacion["id"])
    assert "Escondido" in [i["title"] for i in curso["items"]]


def test_el_listado_sin_el_flag_sigue_ocultando(client, capacitacion, voluntario):
    """Sin pedirlo, nada cambia: es lo que ve quien cursa."""
    client.post(f"/capacitaciones/{capacitacion['id']}/items", json={
        "kind": "texto", "title": "Escondido", "is_published": False})

    r = client.get(
        f"/capacitaciones/?user_type=voluntario&user_id={voluntario.id}&include_items=true")

    curso = next(t for t in r.json() if t["id"] == capacitacion["id"])
    assert "Escondido" not in [i["title"] for i in curso["items"]]


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

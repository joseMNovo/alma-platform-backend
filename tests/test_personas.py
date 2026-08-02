"""
Base de datos de Personas — el ABM sobre `participant_profiles`.

Es el registro maestro: acá está toda persona vinculada a ALMA, tenga o no
cuenta para entrar a la plataforma. El login (`participant_id`) es opcional y el
email es la clave que la identifica, así que las dos cosas que más importan son
que no se dupliquen emails y que los nombres queden normalizados.
"""
import pytest

from app.utils.text import normalize_name


UNA_PERSONA = {"name": "Cecilia", "last_name": "Cabral", "email": "cecilia@ejemplo.com"}


# ── Alta ───────────────────────────────────────────────────────────────────

def test_crear_persona(client):
    r = client.post("/personas/", json=UNA_PERSONA)

    assert r.status_code == 201
    cuerpo = r.json()
    assert cuerpo["name"] == "Cecilia"
    assert cuerpo["email"] == "cecilia@ejemplo.com"


def test_una_persona_nueva_no_tiene_login(client):
    """Cargarla en la base NO le crea cuenta: eso es un paso aparte (invitación)."""
    r = client.post("/personas/", json=UNA_PERSONA)

    assert r.json()["participant_id"] is None


def test_una_persona_nueva_no_es_voluntaria_ni_socia(client):
    cuerpo = client.post("/personas/", json=UNA_PERSONA).json()

    assert cuerpo["is_volunteer"] is False
    assert cuerpo["volunteer_id"] is None
    assert cuerpo["is_member"] is False


def test_se_puede_marcar_como_socia(client):
    r = client.post("/personas/", json={**UNA_PERSONA, "is_member": True})

    assert r.json()["is_member"] is True


def test_el_email_no_se_puede_repetir(client):
    """El email es la clave que identifica a la persona: dos filas con el mismo
    email serían dos personas que en realidad son una."""
    client.post("/personas/", json=UNA_PERSONA)

    r = client.post("/personas/", json={**UNA_PERSONA, "name": "Otra"})

    assert r.status_code == 409
    assert "ya existe" in r.json()["detail"].lower()


def test_varias_personas_sin_email_conviven(client):
    """Cargar a alguien sin email tiene que seguir siendo posible: mucha gente
    mayor no tiene. El UNIQUE no puede impedirlo."""
    assert client.post("/personas/", json={"name": "Uno"}).status_code == 201
    assert client.post("/personas/", json={"name": "Dos"}).status_code == 201

    assert len(client.get("/personas/").json()) == 2


def test_se_puede_crear_solo_con_el_nombre(client):
    """Todos los campos son opcionales: se carga lo que se sepa en el momento."""
    assert client.post("/personas/", json={"name": "Solo Nombre"}).status_code == 201


# ── Normalización de nombres ───────────────────────────────────────────────
# Se hace en el backend con field_validators, no en la UI: así entra normalizado
# venga de donde venga (formulario, importación, otro endpoint).

@pytest.mark.parametrize("entrada,esperado", [
    ("CECILIA CABRAL", "Cecilia Cabral"),
    ("cecilia cabral", "Cecilia Cabral"),
    ("  cecilia   cabral  ", "Cecilia Cabral"),
    ("ana-maria", "Ana-Maria"),
    ("o'connor", "O'Connor"),
    ("JOSÉ", "José"),
    ("", ""),
    (None, None),
])
def test_normalize_name(entrada, esperado):
    assert normalize_name(entrada) == esperado


def test_el_alta_normaliza_el_nombre(client):
    r = client.post("/personas/", json={
        "name": "  MARIA   sol ", "last_name": "DIMAS ruiz"})

    assert r.json()["name"] == "Maria Sol"
    assert r.json()["last_name"] == "Dimas Ruiz"


def test_la_edicion_tambien_normaliza(client):
    creada = client.post("/personas/", json=UNA_PERSONA).json()

    r = client.put(f"/personas/{creada['id']}", json={"name": "ROBERTO carlos"})

    assert r.json()["name"] == "Roberto Carlos"


# ── Búsqueda ───────────────────────────────────────────────────────────────

@pytest.fixture()
def tres_personas(client):
    client.post("/personas/", json={
        "name": "Cecilia", "last_name": "Cabral", "email": "c@e.com",
        "city": "Rosario", "province": "Santa Fe", "cuit": "27-11111111-4"})
    client.post("/personas/", json={
        "name": "Roberto", "last_name": "Gómez", "email": "r@e.com",
        "city": "Funes", "province": "Santa Fe", "cuit": "20-22222222-3"})
    client.post("/personas/", json={
        "name": "Ana", "last_name": "Cabral", "email": "a@e.com",
        "city": "Córdoba", "province": "Córdoba", "cuit": "27-33333333-9"})


def test_busca_por_nombre(client, tres_personas):
    assert len(client.get("/personas/?name=Cecilia").json()) == 1


def test_busca_por_apellido(client, tres_personas):
    """Dos personas comparten apellido: tienen que salir las dos."""
    assert len(client.get("/personas/?last_name=Cabral").json()) == 2


def test_la_busqueda_es_parcial(client, tres_personas):
    """Se busca mientras se tipea, así que tiene que matchear por pedazo."""
    assert len(client.get("/personas/?name=Cec").json()) == 1
    assert len(client.get("/personas/?last_name=abra").json()) == 2


def test_la_busqueda_ignora_mayusculas(client, tres_personas):
    assert len(client.get("/personas/?name=CECILIA").json()) == 1
    assert len(client.get("/personas/?name=cecilia").json()) == 1


def test_busca_por_ciudad_provincia_y_cuit(client, tres_personas):
    assert len(client.get("/personas/?city=Rosario").json()) == 1
    assert len(client.get("/personas/?province=Santa Fe").json()) == 2
    assert len(client.get("/personas/?cuit=22222222").json()) == 1


def test_los_filtros_se_combinan(client, tres_personas):
    r = client.get("/personas/?last_name=Cabral&city=Rosario")

    assert len(r.json()) == 1
    assert r.json()[0]["name"] == "Cecilia"


def test_una_busqueda_sin_resultados_devuelve_lista_vacia(client, tres_personas):
    assert client.get("/personas/?name=Nadie").json() == []


def test_la_lista_viene_ordenada_por_nombre(client, tres_personas):
    nombres = [p["name"] for p in client.get("/personas/").json()]

    assert nombres == sorted(nombres), "La lista tiene que venir alfabética para poder leerla"


# ── Edición y baja ─────────────────────────────────────────────────────────

def test_editar_solo_cambia_lo_enviado(client):
    creada = client.post("/personas/", json={**UNA_PERSONA, "city": "Rosario"}).json()

    r = client.put(f"/personas/{creada['id']}", json={"phone": "341-1234567"})

    assert r.json()["phone"] == "341-1234567"
    assert r.json()["city"] == "Rosario", "El PUT pisó un campo que no se envió"
    assert r.json()["email"] == UNA_PERSONA["email"]


def test_no_se_puede_editar_hacia_un_email_ya_usado(client):
    client.post("/personas/", json=UNA_PERSONA)
    otra = client.post("/personas/", json={"name": "Otra", "email": "otra@ejemplo.com"}).json()

    r = client.put(f"/personas/{otra['id']}", json={"email": UNA_PERSONA["email"]})

    assert r.status_code == 409


def test_borrar_una_persona(client):
    creada = client.post("/personas/", json=UNA_PERSONA).json()

    assert client.delete(f"/personas/{creada['id']}").status_code == 204
    assert client.get(f"/personas/{creada['id']}").status_code == 404


def test_404_en_una_persona_que_no_existe(client):
    assert client.get("/personas/99999").status_code == 404
    assert client.put("/personas/99999", json={"name": "X"}).status_code == 404
    assert client.delete("/personas/99999").status_code == 404


def test_el_abm_no_notifica_a_nadie(client, buzon):
    """Cargar gente en la base no le escribe a nadie. La invitación es otro
    endpoint y es un acto deliberado."""
    creada = client.post("/personas/", json=UNA_PERSONA).json()
    client.put(f"/personas/{creada['id']}", json={"name": "Cambiada"})
    client.delete(f"/personas/{creada['id']}")

    assert buzon.emails == []
    assert buzon.push == []

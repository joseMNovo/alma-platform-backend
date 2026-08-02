"""
CRUD de los módulos que comparten forma: grupos, talleres, actividades,
inventario y pagos.

Los cinco exponen exactamente los mismos cinco endpoints (GET /, GET /{id},
POST /, PUT /{id}, DELETE /{id}), así que en vez de repetir la misma batería
cinco veces, la escribimos una y la corremos contra todos. Sumar un módulo nuevo
con esta forma es agregar una fila en MODULOS.

Lo que NO se testea acá es quién tiene permiso para cada cosa: el backend no
decide eso. El control de acceso vive en el frontend (lib/permissions.ts, que
tiene sus propios tests) y el backend confía en la API key interna.
"""
import pytest

# (ruta, cuerpo mínimo para crear, cambio para el PUT, campo que se verifica)
MODULOS = [
    (
        "/grupos",
        {"name": "Grupo de los martes"},
        {"name": "Grupo de los jueves"},
        "name",
    ),
    (
        "/talleres",
        {"name": "Taller de memoria"},
        {"name": "Taller de memoria avanzado"},
        "name",
    ),
    (
        "/actividades",
        {"name": "Caminata"},
        {"name": "Caminata larga"},
        "name",
    ),
    (
        "/inventario",
        {"name": "Sillas", "entry_date": "2026-01-15", "quantity": 10},
        {"name": "Sillas plegables"},
        "name",
    ),
    (
        "/pagos",
        {"user_id": 1, "concept": "Cuota enero", "amount": 5000, "due_date": "2026-01-31"},
        {"concept": "Cuota febrero"},
        "concept",
    ),
]

IDS = [m[0].strip("/") for m in MODULOS]


@pytest.fixture(autouse=True)
def voluntario_base(crear_voluntario):
    """`pagos.user_id` tiene una FK contra `voluntarios`, así que sin un
    voluntario cargado el INSERT falla (en SQLite igual que en MySQL).

    Como la base arranca vacía en cada test, este es siempre el voluntario #1:
    el que referencian los pagos de prueba de MODULOS.
    """
    return crear_voluntario()


@pytest.mark.parametrize("ruta,crear,editar,campo", MODULOS, ids=IDS)
class TestCrud:
    """Batería completa contra un módulo. pytest la corre una vez por fila."""

    def test_arranca_vacio(self, client, ruta, crear, editar, campo):
        r = client.get(f"{ruta}/")
        assert r.status_code == 200
        assert r.json() == []

    def test_crear_devuelve_201_y_un_id(self, client, ruta, crear, editar, campo):
        r = client.post(f"{ruta}/", json=crear)

        assert r.status_code == 201, r.text
        cuerpo = r.json()
        assert cuerpo["id"] > 0
        assert cuerpo[campo] == crear[campo]

    def test_lo_creado_aparece_en_la_lista(self, client, ruta, crear, editar, campo):
        client.post(f"{ruta}/", json=crear)

        lista = client.get(f"{ruta}/").json()

        assert len(lista) == 1
        assert lista[0][campo] == crear[campo]

    def test_se_puede_traer_por_id(self, client, ruta, crear, editar, campo):
        creado = client.post(f"{ruta}/", json=crear).json()

        r = client.get(f"{ruta}/{creado['id']}")

        assert r.status_code == 200
        assert r.json()["id"] == creado["id"]

    def test_editar_cambia_solo_lo_enviado(self, client, ruta, crear, editar, campo):
        """El PUT es parcial (exclude_unset): lo que no se manda no se pisa."""
        creado = client.post(f"{ruta}/", json=crear).json()

        r = client.put(f"{ruta}/{creado['id']}", json=editar)

        assert r.status_code == 200
        actualizado = r.json()
        assert actualizado[campo] == editar[campo]
        # Los campos que no viajaron en el PUT quedaron como estaban.
        for clave, valor in crear.items():
            if clave not in editar:
                assert actualizado[clave] == valor, f"El PUT pisó {clave}, que no se envió"

    def test_borrar_lo_saca_de_la_lista(self, client, ruta, crear, editar, campo):
        creado = client.post(f"{ruta}/", json=crear).json()

        r = client.delete(f"{ruta}/{creado['id']}")

        assert r.status_code == 204
        assert client.get(f"{ruta}/").json() == []
        assert client.get(f"{ruta}/{creado['id']}").status_code == 404

    def test_un_id_que_no_existe_da_404(self, client, ruta, crear, editar, campo):
        assert client.get(f"{ruta}/99999").status_code == 404
        assert client.put(f"{ruta}/99999", json=editar).status_code == 404
        assert client.delete(f"{ruta}/99999").status_code == 404

    def test_crear_sin_los_campos_obligatorios_da_422(self, client, ruta, crear, editar, campo):
        assert client.post(f"{ruta}/", json={}).status_code == 422

    def test_varios_registros_conviven(self, client, ruta, crear, editar, campo):
        for i in range(3):
            client.post(f"{ruta}/", json={**crear, campo: f"{crear[campo]} {i}"})

        assert len(client.get(f"{ruta}/").json()) == 3

    def test_no_manda_mails_ni_push(self, client, ruta, crear, editar, campo, buzon):
        """Un ABM común no debe notificar a nadie."""
        creado = client.post(f"{ruta}/", json=crear).json()
        client.put(f"{ruta}/{creado['id']}", json=editar)
        client.delete(f"{ruta}/{creado['id']}")

        assert buzon.emails == []
        assert buzon.push == []


# ── Cosas propias de cada módulo ───────────────────────────────────────────

def test_grupos_filtra_por_status(client):
    client.post("/grupos/", json={"name": "Activo", "status": "activo"})
    client.post("/grupos/", json={"name": "Inactivo", "status": "inactivo"})

    assert len(client.get("/grupos/?status=activo").json()) == 1
    assert len(client.get("/grupos/?status=inactivo").json()) == 1
    assert len(client.get("/grupos/").json()) == 2


def test_grupos_arranca_activo_por_defecto(client):
    assert client.post("/grupos/", json={"name": "Sin status"}).json()["status"] == "activo"


def test_la_paginacion_funciona(client):
    for i in range(5):
        client.post("/grupos/", json={"name": f"Grupo {i}"})

    assert len(client.get("/grupos/?limit=2").json()) == 2
    assert len(client.get("/grupos/?skip=3").json()) == 2
    assert client.get("/grupos/?skip=99").json() == []


def test_pagos_valida_el_medio_de_pago(client):
    base = {"user_id": 1, "concept": "Cuota", "amount": 100, "due_date": "2026-01-31"}

    assert client.post("/pagos/", json={**base, "payment_method": "efectivo"}).status_code == 201
    assert client.post("/pagos/", json={**base, "payment_method": "bitcoin"}).status_code == 422


def test_pagos_valida_el_estado(client):
    base = {"user_id": 1, "concept": "Cuota", "amount": 100, "due_date": "2026-01-31"}

    assert client.post("/pagos/", json={**base, "status": "pagado"}).status_code == 201
    assert client.post("/pagos/", json={**base, "status": "inventado"}).status_code == 422


def test_pagos_arranca_pendiente(client):
    r = client.post("/pagos/", json={
        "user_id": 1, "concept": "Cuota", "amount": 100, "due_date": "2026-01-31"})

    assert r.json()["status"] == "pendiente"


def test_actividades_guarda_quien_la_creo(client, voluntario):
    r = client.post("/actividades/", json={
        "name": "Caminata", "created_by_volunteer_id": voluntario.id})

    assert r.status_code == 201
    assert r.json()["created_by_volunteer_id"] == voluntario.id


def test_inventario_exige_fecha_de_ingreso(client):
    assert client.post("/inventario/", json={"name": "Sin fecha"}).status_code == 422

"""
Red de seguridad que cubre TODOS los routers de una.

No mira la lógica de cada endpoint: mira que la app entera esté sana. Recorre las
rutas registradas en FastAPI y las llama. Como se arma sola a partir de la app, un
router nuevo entra a la suite sin que nadie tenga que acordarse de agregarlo acá.

Lo que agarra: imports rotos, modelos que no matchean la tabla, respuestas que no
validan contra su schema, endpoints que revientan con la base vacía. Es el test
que te avisa "algo se rompió" cuando tocás algo transversal.
"""
import pytest
from fastapi.routing import APIRoute

from app.main import app as fastapi_app


def _rutas(metodo: str) -> list[str]:
    """Rutas de ese método HTTP que no necesitan parámetros en el path."""
    encontradas = []
    for route in fastapi_app.routes:
        if not isinstance(route, APIRoute) or metodo not in route.methods:
            continue
        if "{" in route.path:  # necesita un id concreto; se testea en su archivo
            continue
        encontradas.append(route.path)
    return sorted(set(encontradas))


GETS = _rutas("GET")

# Rutas deliberadamente públicas: son health checks, no exponen datos y sirven
# para que el monitoreo sepa si el backend está vivo sin repartir la API key.
PUBLICAS = {"/"}


def test_hay_rutas_para_revisar():
    """Si esto falla, el descubrimiento de rutas se rompió y la suite de abajo
    estaría pasando sin probar nada."""
    assert len(GETS) > 15, f"Se esperaban muchas rutas GET, se encontraron {len(GETS)}"


@pytest.mark.parametrize("ruta", GETS)
def test_los_get_no_revientan_con_la_base_vacia(client, ruta):
    """Ningún GET debe tirar 500 contra una base recién creada.

    Un 422 está bien (le faltan query params obligatorios); un 500 no: significa
    que el endpoint asume datos que pueden no existir.
    """
    r = client.get(ruta)
    assert r.status_code != 500, (
        f"GET {ruta} devolvió 500 con la base vacía.\n"
        f"Respuesta: {r.text[:400]}"
    )
    assert r.status_code < 500, f"GET {ruta} devolvió {r.status_code}"


@pytest.mark.parametrize("ruta", [r for r in GETS if r not in PUBLICAS])
def test_todos_los_get_exigen_la_api_key(client, ruta):
    """La API interna nunca debe quedar abierta: es la única barrera que tiene
    el backend, que no está expuesto pero igual escucha en la VPS.

    Si agregás un router y te olvidás del `**common` en main.py, este test lo
    agarra: aparece solo en la lista y falla.
    """
    r = client.get(ruta, headers={"X-API-Key": "clave-invalida"})
    assert r.status_code == 403, f"GET {ruta} respondió {r.status_code} con una API key inválida"


def test_ningun_test_mando_nada(buzon):
    """Cierre: recorrer toda la API no debe disparar un solo mail ni push."""
    assert buzon.emails == []
    assert buzon.push == []

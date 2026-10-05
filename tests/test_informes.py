"""Informes descargables (PDF / Excel) y el filtro por fecha que los alimenta.

OJO CON LAS FECHAS EN ESTE ARCHIVO
==================================
Los tests corren sobre SQLite y producción sobre MySQL, y **guardan los
TIMESTAMP en relojes distintos**: SQLite escribe UTC, MySQL con
`time_zone = SYSTEM` escribe la hora local del servidor (hoy Europe/Berlin).

Medido en este entorno: una venta creada a las 23:18 hora local quedó
guardada como `2026-10-05 02:18` — el día siguiente.

Por eso acá NO se testea "la venta de hoy entra en el filtro de hoy" a través
del endpoint: ese test pasaría o fallaría según la hora del día en que se
corra, y no diría nada sobre producción. La conversión de fechas se prueba
como unidad, que es donde vive la lógica, y de los endpoints se verifica lo
que sí es independiente del reloj: que contesten y que el archivo salga bien
formado.
"""
import base64
import io
from datetime import date, datetime, timedelta

from app.utils.timezone import AR_TZ, ar_date_to_server, to_ar_date


# ── La conversión de fechas ────────────────────────────────────────────

def test_ida_y_vuelta_de_fecha():
    """Convertir una fecha argentina al reloj del servidor y volver tiene que
    devolver la misma fecha. Es la garantía de la que cuelga todo el filtro."""
    for d in (date(2026, 1, 1), date(2026, 7, 15), date(2026, 10, 4), date(2026, 12, 31)):
        assert to_ar_date(ar_date_to_server(d)) == d


def test_el_borde_superior_es_el_dia_siguiente():
    for d in (date(2026, 3, 10), date(2026, 9, 30)):
        assert ar_date_to_server(d, dia_siguiente=True) == ar_date_to_server(d + timedelta(days=1))


def test_una_venta_de_la_noche_entra_en_su_propio_dia():
    """El caso que motivó todo esto: una jornada que cobra hasta tarde.

    Una venta de las 22:00 en Rosario cae en el día siguiente del reloj del
    servidor. Tiene que seguir entrando en el filtro de SU día argentino, no
    del siguiente.
    """
    dia = date(2026, 9, 21)
    venta_ar = datetime(2026, 9, 21, 22, 0, tzinfo=AR_TZ)
    # Como la guardaría MySQL: hora local del servidor, sin tzinfo.
    guardada = venta_ar.astimezone().replace(tzinfo=None)

    assert ar_date_to_server(dia) <= guardada < ar_date_to_server(dia, dia_siguiente=True)
    # Y NO tiene que entrar en el día de después.
    assert not (ar_date_to_server(dia + timedelta(days=1)) <= guardada)


# ── Los endpoints ──────────────────────────────────────────────────────

def _tabla(ws):
    """Las filas de la tabla de una hoja, salteando el encabezado informativo.

    Arriba de los títulos van el período y la fecha de emisión, más una fila
    en blanco. Buscar la fila de títulos en vez de asumir que es la primera
    deja estos tests a salvo de que ese bloque crezca.
    """
    filas = list(ws.values)
    inicio = next(i for i, f in enumerate(filas) if f[0] in ("Fecha", "Producto", "Medio", "Origen"))
    return filas[inicio], filas[inicio + 1:]


def _con_una_venta(client):
    p = client.post("/stand/products", json={
        "name": "Mates", "unit_price": 15000, "quantity": 10, "sort_order": 1}).json()
    client.post("/stand/sales", json={
        "payment_method": "efectivo", "items": [{"product_id": p["id"], "quantity": 2}]})
    return p


def test_informe_del_stand_con_base_vacia(client):
    """Un período sin ventas tiene que dar un informe válido, no un error."""
    for formato, firma in (("pdf", b"%PDF"), ("xlsx", b"PK")):
        r = client.get(f"/stand/informe?formato={formato}")
        assert r.status_code == 200, r.text[:300]
        crudo = base64.b64decode(r.json()["base64"])
        assert crudo[:len(firma)] == firma
        assert r.json()["filename"].endswith(formato)


def test_informe_del_stand_con_ventas(client):
    _con_una_venta(client)

    pdf = base64.b64decode(client.get("/stand/informe?formato=pdf").json()["base64"])
    assert pdf[:4] == b"%PDF" and len(pdf) > 3000

    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(base64.b64decode(
        client.get("/stand/informe?formato=xlsx").json()["base64"])))
    assert wb.sheetnames == ["Ventas", "Resumen por producto", "Medio de pago"]
    titulos, _ = _tabla(wb["Ventas"])
    assert titulos == (
        "Fecha", "Hora", "Producto", "Cantidad", "Precio unitario",
        "Subtotal", "Medio de pago", "Anulada")


def test_las_anuladas_se_ven_pero_no_suman(client):
    """Un informe que esconde las anuladas no se puede auditar; uno que las
    suma miente. Tienen que estar en el detalle y afuera del total."""
    p = _con_una_venta(client)
    anulada = client.post("/stand/sales", json={
        "payment_method": "efectivo", "items": [{"product_id": p["id"], "quantity": 3}]}).json()
    client.post(f"/stand/sales/{anulada['id']}/void", json={})

    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(base64.b64decode(
        client.get("/stand/informe?formato=xlsx").json()["base64"])))

    _, ventas = _tabla(wb["Ventas"])
    assert any(f[7] == "Si" for f in ventas), "la anulada no aparece en el detalle"

    total = [f for f in wb["Resumen por producto"].values if f[0] == "TOTAL"][0]
    assert float(total[2]) == 30000, f"el total no deberia incluir la anulada: {total}"


def test_informe_de_ingresos(client):
    _con_una_venta(client)
    for formato, firma in (("pdf", b"%PDF"), ("xlsx", b"PK")):
        r = client.get(f"/ingresos/informe?formato={formato}")
        assert r.status_code == 200, r.text[:300]
        assert base64.b64decode(r.json()["base64"])[:len(firma)] == firma


def test_formato_invalido_no_pasa(client):
    assert client.get("/stand/informe?formato=docx").status_code == 422


def test_un_rango_viejo_no_trae_nada(client):
    _con_una_venta(client)
    assert client.get("/stand/sales?desde=2020-01-01&hasta=2020-01-02").json() == []


def test_el_excel_dice_cuando_se_emitio(client):
    """La fecha de descarga tiene que estar, y en hora de ARGENTINA.

    El servidor corre en Berlín: con `date.today()` a secas, un informe bajado
    a las 21:00 de Rosario salía fechado al día siguiente, y quien lo recibe
    lee esa fecha como la de la descarga.
    """
    from openpyxl import load_workbook
    from app.utils.timezone import today_ar
    from app.services.reportes import fecha_corta

    _con_una_venta(client)
    wb = load_workbook(io.BytesIO(base64.b64decode(
        client.get("/stand/informe?formato=xlsx").json()["base64"])))

    # Va al PIE, despues de los datos: arriba de la tabla se veia como
    # suciedad en lo primero que uno abre.
    texto = " | ".join(str(f[0]) for f in wb["Ventas"].values if f[0])
    assert "Emitido el" in texto, texto
    assert fecha_corta(today_ar()) in texto, texto


def test_la_fecha_de_emision_no_es_la_del_servidor():
    """`emitido_el()` no puede depender del reloj del servidor."""
    from app.services import reportes
    from app.utils.timezone import today_ar

    # En formato corto ("5 oct 2026"): el membrete lo pone al lado de la marca,
    # en una línea, y "5 de octubre de 2026, 02:04" no entraba.
    assert reportes.emitido_el().startswith(reportes.fecha_corta(today_ar()))

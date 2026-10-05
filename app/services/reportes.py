"""Informes descargables: PDF y Excel.

Primitivas compartidas. El puesto de venta y los ingresos arman sus datos y
llaman acá: así los dos informes salen con la misma cara y hay un solo lugar
donde cambiar el estilo.

El PDF reusa la paleta y las fuentes de `certificate_pdf.py`, para que un
informe se vea hermano de un certificado y no de otra aplicación.

── La forma del informe ───────────────────────────────────────────────────

Una hoja, de arriba hacia abajo, contestando preguntas cada vez más finas:

  1. El titular y los KPI: cuánto, cuántos, cuánto por cabeza. Es lo único
     que mira la mayoría.
  2. Dos paneles al lado: de dónde viene la plata (magnitudes) y cómo entró
     (parte-todo). Juntos ocupan el alto de uno solo.
  3. El detalle, agrupado por día y con el subtotal de cada jornada. Es la
     parte que se audita, y sin los cortes por día había que sumar a mano
     para contestar "¿cuánto hicimos el sábado?".
  4. El total del período, cerrando.

── Por qué barras y no torta para el ranking ──────────────────────────────

Una torta de ocho productos es ilegible por dos motivos independientes:

1. La venta está concentrada. Si un producto se lleva el 60% y otro el 0,5%,
   la porción chica no tiene lugar ni para su etiqueta.
2. La paleta de ALMA no da para ocho categorías. El teal `#5EC0CF` y el
   lavanda `#9A8BC2` tienen ΔE 14,9 entre sí — por debajo del piso de 15, o
   sea difíciles de distinguir incluso con visión normal. Con ocho porciones
   el problema se multiplica, y en una impresión en blanco y negro no queda
   nada.

Comparar magnitudes es trabajo de barras: un solo tono, ordenadas de mayor a
menor, con el número al lado. Lee igual en pantalla, en papel y en gris.

Para medio de pago —dos o tres valores, parte-todo de verdad— va una barra
APILADA y no una torta. Mismo dato, pero una franja de 6mm de alto deja lugar
para la leyenda con monto y porcentaje al lado, mientras que la torta se comía
media hoja para decir "60 / 33 / 7". Las etiquetas van siempre escritas: el
teal y el lavanda no se distinguen lo suficiente como para que el color solo
diga cuál es cuál.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer,
    Table, TableStyle,
)
from reportlab.graphics.shapes import Drawing, Rect, String

from app.utils.timezone import now_ar
from app.services.certificate_pdf import (
    TEAL, LAVENDER, DARK_TEAL, CHARCOAL, GRAY, FONT, FONT_BOLD,
    default_logo_bytes,
)

TINTA = colors.HexColor("#22333B")       # Títulos y cifras: más oscuro que CHARCOAL.
LIGHT = colors.HexColor("#F4F4F4")       # Pista de las barras, fondo de los cortes por día.
BORDE = colors.HexColor("#E3E3E3")
BORDE_SUAVE = colors.HexColor("#EFEFEF")  # Separador entre filas del detalle.
TEAL_CLARO = colors.HexColor("#A7DDE6")   # Tercer tono de la barra apilada.

MARGEN = 16 * mm
ANCHO_UTIL = A4[0] - 2 * MARGEN

# El alto que ocupa el membrete. El marco del contenido arranca abajo de esto.
ALTO_MEMBRETE = 30 * mm

# Más de esto en el ranking y las barras se vuelven ilegibles: la cola se
# agrupa en "Otros". Es el techo que recomienda cualquier guía de gráficos, y
# acá además evita una hoja de barras de un milímetro.
TOPE_BARRAS = 8

MESES_CORTOS = ("ene", "feb", "mar", "abr", "may", "jun",
                "jul", "ago", "sep", "oct", "nov", "dic")
DIAS_CORTOS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")


def emitido_el() -> str:
    """«4 oct 2026, 23:45» — hora de ARGENTINA.

    Con `date.today()` a secas salía la fecha del servidor, que corre en
    Berlín: un informe bajado a las 21:00 de Rosario quedaba fechado al día
    siguiente. Quien lo recibe lee esa fecha como la de la descarga, así que
    tiene que ser la de acá.

    Lleva la hora y no solo el día porque un mismo período se baja varias
    veces mientras se corrigen cosas, y sin la hora no hay forma de saber
    cuál de los dos archivos es el último.
    """
    ahora = now_ar()
    return f"{fecha_corta(ahora.date())}, {ahora.strftime('%H:%M')}"


def fecha_corta(d: date) -> str:
    """«22 sep 2026». Entra en una línea y no se lee como una fecha de EE.UU."""
    return f"{d.day} {MESES_CORTOS[d.month - 1]} {d.year}"


def dia_corto(d: date) -> str:
    """«Sáb 19 sep» — el encabezado de cada jornada en el detalle.

    Lleva el día de la semana porque las jornadas del puesto son sábados y
    feriados: ver "Sáb" al lado del número ubica mucho más rápido que el
    número solo, sobre todo cuando se comparan dos informes.
    """
    return f"{DIAS_CORTOS[d.weekday()]} {d.day} {MESES_CORTOS[d.month - 1]}"


def periodo_corto(desde: Optional[date], hasta: Optional[date]) -> str:
    """«1 ene – 31 dic 2026». El año se escribe una sola vez si no cambia."""
    if not desde and not hasta:
        return "Todo el historial"
    if desde and hasta:
        if desde.year == hasta.year:
            return (f"{desde.day} {MESES_CORTOS[desde.month - 1]} – "
                    f"{hasta.day} {MESES_CORTOS[hasta.month - 1]} {hasta.year}")
        return f"{fecha_corta(desde)} – {fecha_corta(hasta)}"
    if desde:
        return f"Desde el {fecha_corta(desde)}"
    return f"Hasta el {fecha_corta(hasta)}"


def pesos(valor) -> str:
    """$1.234.567 — sin decimales, que en un informe de caja solo estorban."""
    try:
        return "$" + f"{float(valor or 0):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "$0"


def porcentaje(parte: float, total: float) -> str:
    """«7%», y «<1%» en vez de «0%».

    Una línea que dice 0% al lado de un monto que no es cero se lee como un
    error del informe. Pasó con los biromes: $1.500 sobre $425.500.
    """
    if not total:
        return "0%"
    p = parte / total * 100
    if 0 < p < 1:
        return "<1%"
    return f"{p:.0f}%"


def _estilo(nombre: str, **kw) -> ParagraphStyle:
    base = dict(fontName=FONT, fontSize=9, textColor=CHARCOAL, leading=12)
    base.update(kw)
    return ParagraphStyle(nombre, **base)


H2 = _estilo("h2", fontName=FONT_BOLD, fontSize=10.5, textColor=TINTA, leading=14)
SUB = _estilo("sub", fontSize=9, textColor=GRAY, leading=12)
NOTA = _estilo("nota", fontSize=8, textColor=CHARCOAL, leading=11)
ROTULO = _estilo("rotulo", fontName=FONT_BOLD, fontSize=7, textColor=GRAY, leading=10)


# ── Piezas que arma cada informe ───────────────────────────────────────

@dataclass
class Barras:
    """Magnitudes comparables, una barra de ancho completo por fila."""
    titulo: str
    datos: Sequence[tuple[str, float]]


@dataclass
class Ranking:
    """Tabla con barra chica adentro: nombre, unidades, precio, total y %.

    Es la variante del ranking cuando además del monto importan las unidades
    y el precio unitario — en el puesto, "13 mates a $15.000" dice algo que
    "$195.000" solo no dice.
    """
    titulo: str
    # (nombre, unidades, precio_unitario, total)
    filas: Sequence[tuple[str, int, float, float]]


@dataclass
class Apilada:
    """Parte-todo en una sola franja, con la leyenda al lado."""
    titulo: str
    datos: Sequence[tuple[str, float]]


@dataclass
class Aviso:
    """Caja con borde para lo que hay que aclarar sin ensuciar los números."""
    texto: str


@dataclass
class Grupo:
    """Una jornada del detalle: encabezado con subtotal y sus filas."""
    titulo: str          # "Sáb 19 sep"
    detalle: str         # "13 ventas + 1 anulada"
    total: str           # "$380.500"
    filas: Sequence[Sequence[str]]
    # Índices DENTRO de `filas` que van en gris y tachados.
    anuladas: set = field(default_factory=set)


# ── Gráficos ───────────────────────────────────────────────────────────

def _barras(pieza: Barras, ancho: float) -> Optional[Drawing]:
    """Barras de un solo tono, ordenadas de mayor a menor.

    Un solo color a propósito: esto compara magnitudes, no distingue
    identidades. Con un color por barra habría que elegir ocho tonos que se
    distingan entre sí y del fondo, y la paleta de ALMA no da para eso.

    El nombre y el monto van ARRIBA de la barra, no al costado: así la barra
    usa todo el ancho de la columna y dos barras parecidas siguen siendo
    comparables aunque los nombres midan distinto.
    """
    datos = [(k, float(v or 0)) for k, v in pieza.datos if (v or 0) > 0]
    if not datos:
        return None

    datos.sort(key=lambda x: x[1], reverse=True)
    if len(datos) > TOPE_BARRAS:
        cola = sum(v for _, v in datos[TOPE_BARRAS:])
        datos = datos[:TOPE_BARRAS] + [("Otros", cola)]

    total = sum(v for _, v in datos) or 1
    maximo = max(v for _, v in datos) or 1

    alto_barra, alto_texto, sep = 5, 13, 9
    paso = alto_texto + alto_barra + sep
    d = Drawing(ancho, len(datos) * paso)

    for i, (nombre, valor) in enumerate(datos):
        tope = d.height - i * paso
        y_barra = tope - alto_texto - alto_barra

        d.add(String(0, tope - 9, nombre[:28], fontName=FONT, fontSize=8.5, fillColor=CHARCOAL))
        d.add(String(ancho, tope - 9, f"{pesos(valor)} · {porcentaje(valor, total)}",
                     fontName=FONT_BOLD, fontSize=8.5, fillColor=TINTA, textAnchor="end"))

        # Pista de fondo: da la referencia del 100% sin dibujar una grilla.
        d.add(Rect(0, y_barra, ancho, alto_barra, rx=2.5, ry=2.5,
                   fillColor=LIGHT, strokeColor=None))
        d.add(Rect(0, y_barra, max(ancho * (valor / maximo), 3), alto_barra, rx=2.5, ry=2.5,
                   fillColor=TEAL, strokeColor=None))

    return d


def _ranking(pieza: Ranking, ancho: float) -> Optional[Table]:
    """El ranking como tabla, con una barra chica en la columna del nombre."""
    filas = [f for f in pieza.filas if float(f[3] or 0) > 0]
    if not filas:
        return None

    filas = sorted(filas, key=lambda f: -float(f[3]))
    if len(filas) > TOPE_BARRAS:
        cola = sum(float(f[3]) for f in filas[TOPE_BARRAS:])
        unidades = sum(int(f[1] or 0) for f in filas[TOPE_BARRAS:])
        filas = filas[:TOPE_BARRAS] + [("Otros", unidades, 0, cola)]

    total = sum(float(f[3]) for f in filas) or 1
    maximo = max(float(f[3]) for f in filas) or 1

    anchos = [ancho * 0.30, ancho * 0.16, ancho * 0.12, ancho * 0.16, ancho * 0.16, ancho * 0.10]
    cuerpo = [[
        Paragraph("Producto", ROTULO), "",
        Paragraph("Unid.", _derecha(ROTULO)),
        Paragraph("Precio", _derecha(ROTULO)),
        Paragraph("Total", _derecha(ROTULO)),
        Paragraph("%", _derecha(ROTULO)),
    ]]

    for nombre, unidades, precio, monto in filas:
        monto = float(monto)
        barra = Drawing(anchos[1] - 8, 9)
        barra.add(Rect(0, 1, anchos[1] - 8, 6, rx=3, ry=3, fillColor=LIGHT, strokeColor=None))
        barra.add(Rect(0, 1, max((anchos[1] - 8) * (monto / maximo), 3), 6, rx=3, ry=3,
                       fillColor=TEAL, strokeColor=None))
        cuerpo.append([
            Paragraph(str(nombre)[:26], _estilo("rk", fontSize=8.5, textColor=TINTA)),
            barra,
            Paragraph(str(unidades), _derecha(_estilo("rku", fontSize=8.5))),
            Paragraph(pesos(precio) if precio else "—", _derecha(_estilo("rkp", fontSize=8.5))),
            Paragraph(pesos(monto), _derecha(_estilo("rkt", fontName=FONT_BOLD, fontSize=8.5, textColor=TINTA))),
            Paragraph(porcentaje(monto, total), _derecha(_estilo("rkpc", fontSize=8.5, textColor=GRAY))),
        ])

    t = Table(cuerpo, colWidths=anchos)
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, BORDE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, BORDE_SUAVE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _apilada(pieza: Apilada, ancho: float) -> Optional[Drawing]:
    """Una franja partida en proporción, y debajo la leyenda con los números.

    Los montos y porcentajes van escritos: el teal y el lavanda de ALMA están
    por debajo del piso de distinción, así que el color solo no alcanza para
    decir cuál es cuál — ni en pantalla, ni impreso, ni en gris.
    """
    datos = [(k, float(v or 0)) for k, v in pieza.datos if (v or 0) > 0]
    if not datos:
        return None

    datos.sort(key=lambda x: -x[1])
    total = sum(v for _, v in datos) or 1
    tonos = (TEAL, DARK_TEAL, TEAL_CLARO, LAVENDER, GRAY)

    alto_franja, sep, alto_fila = 9, 12, 15
    d = Drawing(ancho, alto_franja + sep + len(datos) * alto_fila)

    # La franja, arriba de todo.
    x = 0.0
    y = d.height - alto_franja
    for i, (_, valor) in enumerate(datos):
        w = ancho * (valor / total)
        # 2px de blanco entre tramos, para que dos tonos vecinos no se peguen.
        d.add(Rect(x, y, max(w - 2, 2), alto_franja, rx=3, ry=3,
                   fillColor=tonos[i % len(tonos)], strokeColor=None))
        x += w

    for i, (nombre, valor) in enumerate(datos):
        fila_y = d.height - alto_franja - sep - (i + 1) * alto_fila + 4
        d.add(Rect(0, fila_y, 8, 8, rx=2, ry=2,
                   fillColor=tonos[i % len(tonos)], strokeColor=None))
        d.add(String(14, fila_y + 1, str(nombre), fontName=FONT, fontSize=8.5, fillColor=CHARCOAL))
        d.add(String(ancho - 34, fila_y + 1, pesos(valor),
                     fontName=FONT_BOLD, fontSize=8.5, fillColor=TINTA, textAnchor="end"))
        d.add(String(ancho, fila_y + 1, porcentaje(valor, total),
                     fontName=FONT, fontSize=8.5, fillColor=GRAY, textAnchor="end"))

    return d


def _aviso(pieza: Aviso, ancho: float) -> Table:
    t = Table([[Paragraph(pieza.texto, NOTA)]], colWidths=[ancho])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, BORDE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def _derecha(base: ParagraphStyle) -> ParagraphStyle:
    copia = ParagraphStyle(base.name + "_r", parent=base)
    copia.alignment = 2  # TA_RIGHT
    return copia


# ── Membrete y pie ─────────────────────────────────────────────────────

def _membrete(canvas, doc, titulo: str, subtitulo: str, pie: str, total_paginas: Optional[int]):
    """Lo que se repite en cada página.

    El título va a la IZQUIERDA y grande, y la marca a la derecha. Es al revés
    de como estaba, y el motivo es que el informe se manda por mail: lo primero
    que tiene que contestar la miniatura es "qué es esto", no "de quién es".
    Quien lo recibe ya sabe quién se lo mandó.
    """
    canvas.saveState()
    alto = A4[1]
    base = alto - ALTO_MEMBRETE  # Línea divisoria del membrete.

    canvas.setFillColor(TINTA)
    canvas.setFont(FONT_BOLD, 19)
    canvas.drawString(MARGEN, base + 11 * mm, titulo)
    canvas.setFillColor(GRAY)
    canvas.setFont(FONT, 8.5)
    canvas.drawString(MARGEN, base + 6.5 * mm, subtitulo)

    # Marca a la derecha: la flor y el nombre en la misma línea del título.
    derecha = A4[0] - MARGEN
    logo = default_logo_bytes()
    ancho_nombre = canvas.stringWidth("Comunidad alma", FONT, 11)
    if logo:
        try:
            canvas.drawImage(
                ImageReader(io.BytesIO(logo)),
                derecha - ancho_nombre - 7 * mm, base + 10.5 * mm,
                width=5.5 * mm, height=5.5 * mm,
                preserveAspectRatio=True, anchor="sw", mask="auto",
            )
        except Exception:
            pass  # El informe sin logo sirve igual; sin informe, no.

    # "Comunidad" en tinta y "alma" en turquesa. Van en dos `drawString` desde
    # una x calculada y no en un `drawRightString`: ese pinta de un solo color,
    # y la marca son dos.
    canvas.setFont(FONT, 11)
    inicio = derecha - ancho_nombre
    canvas.setFillColor(TINTA)
    canvas.drawString(inicio, base + 11 * mm, "Comunidad ")
    canvas.setFillColor(TEAL)
    canvas.drawString(inicio + canvas.stringWidth("Comunidad ", FONT, 11),
                      base + 11 * mm, "alma")

    canvas.setFillColor(GRAY)
    canvas.setFont(FONT, 8)
    canvas.drawRightString(derecha, base + 6.5 * mm, f"Emitido el {emitido_el()}")

    canvas.setStrokeColor(BORDE)
    canvas.setLineWidth(0.8)
    canvas.line(MARGEN, base + 3 * mm, derecha, base + 3 * mm)

    # Pie
    canvas.setFillColor(GRAY)
    canvas.setFont(FONT, 7.5)
    canvas.drawString(MARGEN, 12 * mm, pie)
    pagina = str(canvas.getPageNumber())
    canvas.drawRightString(
        derecha, 12 * mm,
        f"Página {pagina} de {total_paginas}" if total_paginas else f"Página {pagina}",
    )
    canvas.restoreState()


def _kpis(items: Sequence[tuple[str, str]]) -> Table:
    """Los números grandes, sin caja.

    El PRIMERO sale al doble de tamaño y en turquesa: en un informe de plata
    siempre hay un número que es la respuesta y el resto que lo acompaña. Con
    los cuatro del mismo tamaño —como estaban, además adentro de recuadros
    grises— había que leerlos todos para descubrir cuál importaba.
    """
    if not items:
        return Table([[""]])

    etiquetas, valores = [], []
    for i, (k, v) in enumerate(items):
        etiquetas.append(Paragraph(k.upper(), ROTULO))
        valores.append(Paragraph(
            v,
            _estilo(f"kpi{i}",
                    fontName=FONT_BOLD,
                    fontSize=26 if i == 0 else 15,
                    textColor=DARK_TEAL if i == 0 else TINTA,
                    leading=30 if i == 0 else 19),
        ))

    # La primera columna más ancha: sostiene el número grande sin cortarlo.
    resto = (ANCHO_UTIL - ANCHO_UTIL * 0.3) / max(len(items) - 1, 1)
    anchos = [ANCHO_UTIL * 0.3] + [resto] * (len(items) - 1)

    t = Table([etiquetas, valores], colWidths=anchos)
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 0),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("VALIGN", (0, 1), (-1, 1), "BOTTOM"),
    ]))
    return t


def _panel(titulo: str, cuerpo, ancho: float) -> Table:
    """Un título chico con su gráfico debajo, como una sola pieza."""
    t = Table([[Paragraph(titulo, H2)], [Spacer(1, 4 * mm)], [cuerpo]], colWidths=[ancho])
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _detalle(columnas: Sequence[str], anchos: Sequence[float],
             grupos: Sequence[Grupo], total_label: Optional[str],
             total_valor: Optional[str]) -> Table:
    """El detalle, con un corte por jornada y su subtotal.

    Sin grilla y sin filas alternadas: las líneas finas entre filas y los
    cortes grises ya ordenan la lectura, y el enrejado competía con los
    números. La última columna va a la derecha porque son importes, y una
    columna de importes alineada a la izquierda no se puede comparar de un
    vistazo.
    """
    cuerpo: list[list] = [[
        Paragraph(c.upper(), _derecha(ROTULO) if i == len(columnas) - 1 else ROTULO)
        for i, c in enumerate(columnas)
    ]]
    estilo = [
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, BORDE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]

    normal = _estilo("td", fontSize=8.5, textColor=TINTA, leading=11)
    normal_der = _derecha(normal)
    gris = _estilo("tdg", fontSize=8.5, textColor=GRAY, leading=11)
    gris_der = _derecha(gris)

    for grupo in grupos:
        i = len(cuerpo)
        encabezado: list = [Paragraph(
            f"<b>{grupo.titulo}</b> · {grupo.detalle}",
            _estilo("gh", fontSize=8.5, textColor=TINTA, leading=11),
        )]
        encabezado += [""] * (len(columnas) - 2)
        encabezado.append(Paragraph(
            grupo.total,
            _derecha(_estilo("ght", fontName=FONT_BOLD, fontSize=8.5, textColor=TINTA)),
        ))
        cuerpo.append(encabezado)
        estilo += [
            ("BACKGROUND", (0, i), (-1, i), LIGHT),
            ("SPAN", (0, i), (-2, i)),
        ]

        for j, fila in enumerate(grupo.filas):
            k = len(cuerpo)
            anulada = j in grupo.anuladas
            est = gris if anulada else normal
            est_der = gris_der if anulada else normal_der
            cuerpo.append([
                Paragraph(f"<strike>{c}</strike>" if anulada and c else str(c),
                          est_der if idx == len(columnas) - 1 else est)
                for idx, c in enumerate(fila)
            ])
            estilo.append(("LINEBELOW", (0, k), (-1, k), 0.4, BORDE_SUAVE))

    if total_label is not None:
        i = len(cuerpo)
        fila: list = [Paragraph(total_label, _estilo("tl", fontName=FONT_BOLD, fontSize=9.5, textColor=TINTA))]
        fila += [""] * (len(columnas) - 2)
        fila.append(Paragraph(
            total_valor or "",
            _derecha(_estilo("tv", fontName=FONT_BOLD, fontSize=9.5, textColor=TINTA)),
        ))
        cuerpo.append(fila)
        estilo += [
            ("SPAN", (0, i), (-2, i)),
            ("LINEABOVE", (0, i), (-1, i), 0.9, TINTA),
            ("TOPPADDING", (0, i), (-1, i), 7),
        ]

    t = Table(cuerpo, colWidths=anchos, repeatRows=1)
    t.setStyle(TableStyle(estilo))
    return t


def pdf_informe(
    *,
    titulo: str,
    subtitulo: str,
    kpis: Sequence[tuple[str, str]],
    izquierda=None,
    derecha: Sequence = (),
    tabla_titulo: Optional[str] = None,
    tabla_columnas: Sequence[str] = (),
    tabla_anchos: Optional[Sequence[float]] = None,
    grupos: Sequence[Grupo] = (),
    total_label: Optional[str] = None,
    total_valor: Optional[str] = None,
    pie: Optional[str] = None,
) -> bytes:
    """Arma el informe completo y devuelve los bytes del PDF.

    Se construye DOS VECES. La primera es a descarte y solo sirve para contar
    las páginas; la segunda ya puede escribir "Página 2 de 3" en el pie.
    Ninguna otra forma funciona: el pie se dibuja mientras el documento se
    arma, cuando todavía no se sabe cuántas páginas van a salir. Son informes
    de una o dos hojas, así que el costo de hacerlo dos veces no se nota.
    """
    def construir(total_paginas: Optional[int]) -> tuple[bytes, int]:
        buffer = io.BytesIO()
        doc = BaseDocTemplate(
            buffer, pagesize=A4,
            leftMargin=MARGEN, rightMargin=MARGEN,
            topMargin=ALTO_MEMBRETE + 6 * mm, bottomMargin=18 * mm,
            title=titulo, author="Comunidad ALMA",
        )
        marco = Frame(MARGEN, 18 * mm, ANCHO_UTIL,
                      A4[1] - ALTO_MEMBRETE - 24 * mm, id="cuerpo")
        doc.addPageTemplates([PageTemplate(
            id="informe", frames=[marco],
            onPage=lambda c, d: _membrete(c, d, titulo, subtitulo,
                                          pie or f"Comunidad alma · {titulo}", total_paginas),
        )])
        doc.build(list(_historia(kpis, izquierda, derecha, tabla_titulo,
                                tabla_columnas, tabla_anchos, grupos,
                                total_label, total_valor)))
        return buffer.getvalue(), doc.page

    _, paginas = construir(None)
    contenido, _ = construir(paginas)
    return contenido


def _historia(kpis, izquierda, derecha, tabla_titulo, tabla_columnas,
              tabla_anchos, grupos, total_label, total_valor):
    """Los elementos del cuerpo, en orden."""
    yield _kpis(kpis)
    yield Spacer(1, 9 * mm)

    # Los dos paneles, al lado. El de la izquierda es más ancho porque lleva
    # nombres de producto; el de la derecha, dos o tres medios de pago.
    hueco = 8 * mm
    ancho_izq = (ANCHO_UTIL - hueco) * 0.54
    ancho_der = (ANCHO_UTIL - hueco) * 0.46

    def armar(pieza, ancho):
        if isinstance(pieza, Barras):
            g = _barras(pieza, ancho)
            return _panel(pieza.titulo, g, ancho) if g is not None else None
        if isinstance(pieza, Ranking):
            g = _ranking(pieza, ancho)
            return _panel(pieza.titulo, g, ancho) if g is not None else None
        if isinstance(pieza, Apilada):
            g = _apilada(pieza, ancho)
            return _panel(pieza.titulo, g, ancho) if g is not None else None
        if isinstance(pieza, Aviso):
            return _aviso(pieza, ancho)
        return None

    col_izq = armar(izquierda, ancho_izq) if izquierda else None

    piezas_der = [p for p in (armar(d, ancho_der) for d in derecha) if p is not None]
    col_der = None
    if piezas_der:
        filas = []
        for i, pieza in enumerate(piezas_der):
            if i:
                filas.append([Spacer(1, 6 * mm)])
            filas.append([pieza])
        col_der = Table(filas, colWidths=[ancho_der])
        col_der.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))

    if col_izq is not None or col_der is not None:
        dupla = Table([[col_izq or "", "", col_der or ""]],
                      colWidths=[ancho_izq, hueco, ancho_der])
        dupla.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        yield dupla
        yield Spacer(1, 10 * mm)

    if grupos:
        # El título pegado a su tabla: un encabezado que queda solo al pie de
        # una página, con la tabla arrancando en la siguiente, es peor que no
        # tener encabezado.
        yield KeepTogether([
            Paragraph(tabla_titulo or "", H2),
            Spacer(1, 4 * mm),
        ])
        yield _detalle(tabla_columnas, tabla_anchos or [], grupos, total_label, total_valor)
    elif col_izq is None and col_der is None:
        yield Paragraph("No hay movimientos en el período elegido.", SUB)


# ── Excel ──────────────────────────────────────────────────────────────

class Hoja:
    """Una hoja del Excel.

    `columnas` son tuplas (título en castellano, tipo). El tipo decide el
    formato de la celda, y eso importa más de lo que parece: una fecha
    guardada como TEXTO no se puede filtrar por mes en Excel, que es
    justamente lo que se pidió. Tipos: texto | numero | moneda | fecha.
    """

    def __init__(self, nombre: str, columnas: Sequence[tuple[str, str]], filas: Sequence[Sequence]):
        self.nombre = nombre[:31]  # Excel no admite nombres de hoja más largos.
        self.columnas = columnas
        self.filas = filas


def xlsx_informe(hojas: Sequence[Hoja], periodo: Optional[str] = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)

    encabezado_fill = PatternFill("solid", fgColor="1A6B7A")
    encabezado_font = Font(bold=True, color="FFFFFF", size=10)

    gris = Font(size=9, color="8A8A8A")
    total_font = Font(bold=True, size=10)
    total_fill = PatternFill("solid", fgColor="EAF6F8")

    for hoja in hojas:
        ws = wb.create_sheet(hoja.nombre)

        # La tabla arranca en la fila 1. Antes había dos líneas de período y
        # emisión arriba, y se veían como suciedad en lo primero que uno abre.
        # Esa información ahora va al pie, después de los datos.
        ws.append([c[0] for c in hoja.columnas])
        for celda in ws[1]:
            celda.fill = encabezado_fill
            celda.font = encabezado_font
            celda.alignment = Alignment(vertical="center")
        ws.row_dimensions[1].height = 22

        for fila in hoja.filas:
            ws.append(list(fila))

        ultima_dato = 1 + len(hoja.filas)

        # Fila de totales, con el número YA CALCULADO y no con una fórmula.
        #
        # Una `=SUM()` tiene la gracia de seguir a la persona si filtra o
        # borra filas, pero solo la calcula un Excel de verdad: en la vista
        # previa de Gmail, en el visor del celular o en cualquier lector que
        # no evalúe fórmulas, el total aparece VACÍO. Este archivo se manda
        # por mail a quien lleva las cuentas, así que vale más que el número
        # se vea siempre a que se recalcule solo.
        sumables = [i for i, (_, t) in enumerate(hoja.columnas, start=1)
                    if t in ("moneda", "numero")]
        if hoja.filas and sumables:
            fila_total = ultima_dato + 1
            ws.cell(row=fila_total, column=1, value="TOTAL")
            for i in sumables:
                suma = sum(
                    float(f[i - 1]) for f in hoja.filas
                    if i - 1 < len(f) and isinstance(f[i - 1], (int, float))
                )
                ws.cell(row=fila_total, column=i, value=suma)
            for celda in ws[fila_total]:
                celda.font = total_font
                celda.fill = total_fill
        else:
            fila_total = ultima_dato

        for i, (titulo, tipo) in enumerate(hoja.columnas, start=1):
            letra = get_column_letter(i)
            if tipo == "moneda":
                formato, ancho = '"$"#,##0', 14
            elif tipo == "numero":
                formato, ancho = "#,##0", 11
            elif tipo == "fecha":
                formato, ancho = "dd/mm/yyyy", 12
            elif tipo == "hora":
                formato, ancho = "hh:mm", 9
            else:
                formato, ancho = None, max(12, min(len(titulo) + 6, 42))
            if formato:
                for celda in ws[letra][1:]:
                    celda.number_format = formato
            ws.column_dimensions[letra].width = ancho

        # Al pie, separado por una fila en blanco: de qué período es el
        # archivo y cuándo se bajó. Abajo no estorba y sigue estando para
        # quien necesite saber de cuándo es el informe que le pasaron.
        ws.cell(row=fila_total + 2, column=1,
                value=f"Emitido el {emitido_el()}").font = gris
        if periodo:
            ws.cell(row=fila_total + 3, column=1, value=f"Período: {periodo}").font = gris

        # Títulos congelados y autofiltro: con cientos de ventas, sin esto hay
        # que scrollear a ciegas. El filtro NO incluye la fila de totales:
        # ordenar por una columna se la llevaría al medio de los datos.
        ws.freeze_panes = "A2"
        if hoja.filas:
            ws.auto_filter.ref = (
                f"A1:{get_column_letter(len(hoja.columnas))}{ultima_dato}"
            )

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()

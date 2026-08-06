"""
app/services/certificate_pdf.py — ALMA Backend — Certificados en PDF
=====================================================================
Dibuja el certificado a partir de la plantilla editable
(`certificate_templates`) y de los datos de una persona.

Por qué reportlab y no WeasyPrint/Chromium: es Python puro. No necesita GTK,
Pango ni un navegador headless, así que se instala igual en el VPS que en la
notebook de desarrollo con un `pip install`.

El LAYOUT es fijo a propósito (hoja apaisada, marco, encabezado, firma abajo).
Lo editable es el TEXTO. Un editor de posiciones tipo "arrastrá el logo" es un
proyecto aparte que se decidió no encarar.
"""
import re
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import Paragraph

from app.utils.logger import log_warn

# Paleta de marca (ver memoria de identidad ALMA).
TEAL = colors.HexColor("#5EC0CF")
LAVENDER = colors.HexColor("#9A8BC2")
DARK_TEAL = colors.HexColor("#1A6B7A")
CHARCOAL = colors.HexColor("#4D4D4D")
GRAY = colors.HexColor("#8A8A8A")

# Gotham Rounded y Nunito Sans son las tipografías de ALMA, pero exigen
# registrar los .ttf en el servidor. Helvetica viene con reportlab y no puede
# faltar; si algún día se suben las fuentes, se cambia acá y nada más.
FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)

# Logo institucional por defecto (el lockup horizontal del sitio: flor + alma
# + ALZHEIMER ROSARIO). Vive DENTRO del backend, copiado del frontend, para
# que el PDF no dependa de dónde esté desplegado el otro repo ni de que una
# URL conteste justo cuando alguien descarga su certificado.
DEFAULT_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "alma-logo.png"

# Bloque opcional: [[texto con {{marcador}}]] desaparece entero si alguno de
# sus marcadores queda vacío. Es lo que hace que un certificado sin DNI no
# imprima «, DNI ,» — ver render_text().
_OPTIONAL_BLOCK = re.compile(r"\[\[(.*?)\]\]", re.DOTALL)
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def default_logo_bytes() -> Optional[bytes]:
    """Logo de ALMA. None si el archivo no está: el PDF sale sin logo, no rompe."""
    try:
        return DEFAULT_LOGO_PATH.read_bytes()
    except OSError:
        log_warn("No se encontró el logo por defecto del certificado",
                 module="certificados", action="default_logo",
                 meta={"path": str(DEFAULT_LOGO_PATH)})
        return None


def format_date_es(value: Optional[date] = None) -> str:
    """«2 de agosto de 2026». Es el formato que espera leer alguien acá."""
    value = value or date.today()
    return f"{value.day} de {MESES[value.month - 1]} de {value.year}"


def render_text(text: Optional[str], values: dict) -> str:
    """Resuelve la plantilla: bloques opcionales primero, marcadores después.

    Dos reglas:

    1. `[[...]]` es un bloque OPCIONAL: si alguno de los marcadores que tiene
       adentro queda vacío, se borra el bloque entero. Es lo que permite que
       el DNI no sea obligatorio — con «{{nombre}}[[, DNI {{dni}}]], completó»
       alguien sin documento sale «María Fernández, completó» y no
       «María Fernández, DNI , completó».

    2. Los marcadores desconocidos se dejan tal cual. Que se vea `{{nombe}}`
       en la vista previa es preferible a imprimir un hueco en blanco en un
       texto legal sin que nadie se entere del error de tipeo.
    """
    if not text:
        return ""

    def resolve(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)  # desconocido: se deja visible
        value = values.get(key)
        return "" if value is None else str(value)

    def keep_block(match: "re.Match[str]") -> str:
        inner = match.group(1)
        keys = _PLACEHOLDER.findall(inner)
        # Un bloque sin marcadores no tiene condición: se deja siempre.
        for key in keys:
            if key in values and not str(values.get(key) or "").strip():
                return ""
        return inner

    return _PLACEHOLDER.sub(resolve, _OPTIONAL_BLOCK.sub(keep_block, text))


def solo_imprimible(texto: str) -> str:
    """Saca lo que la fuente no sabe dibujar.

    Red de contención, no la validación: los schemas ya rechazan emojis y
    caracteres raros al guardar. Esto cubre las filas viejas y la vista
    previa, para que un carácter suelto no tumbe la generación entera.
    """
    salida = []
    for caracter in texto:
        if caracter in "\n\t":
            salida.append(caracter)
            continue
        if ord(caracter) < 32:
            continue
        try:
            caracter.encode("cp1252")
        except UnicodeEncodeError:
            continue
        salida.append(caracter)
    return "".join(salida)


def _to_markup(text: str) -> str:
    """Texto plano → markup de Paragraph, respetando los saltos de línea."""
    return escape(solo_imprimible(text)).replace("\n", "<br/>")


def _draw_image(canvas, raw: bytes, *, center_x: float, top_y: float, max_w: float, max_h: float) -> float:
    """Dibuja una imagen centrada respetando su proporción. Devuelve su alto.

    Nunca hace fallar el PDF: si el archivo está roto, el certificado sale sin
    esa imagen. Un certificado sin logo es un problema menor que un 500 cuando
    alguien lo va a descargar.
    """
    try:
        image = ImageReader(BytesIO(raw))
        width, height = image.getSize()
        if not width or not height:
            return 0.0
        scale = min(max_w / width, max_h / height)
        draw_w, draw_h = width * scale, height * scale
        canvas.drawImage(
            image,
            center_x - draw_w / 2,
            top_y - draw_h,
            width=draw_w,
            height=draw_h,
            mask="auto",
        )
        return draw_h
    except Exception:
        log_warn("No se pudo dibujar una imagen del certificado",
                 module="certificados", action="draw_image")
        return 0.0


def render_certificate(
    *,
    heading: str,
    body: Optional[str],
    legal_note: Optional[str] = None,
    signature_name: Optional[str] = None,
    signature_role: Optional[str] = None,
    logo_bytes: Optional[bytes] = None,
    signature_bytes: Optional[bytes] = None,
    values: Optional[dict] = None,
) -> bytes:
    """Arma el PDF y devuelve sus bytes.

    `values` son los datos que reemplazan a los marcadores: nombre, dni,
    capacitacion, fecha, horas, codigo.

    Lo ÚNICO obligatorio es el titular (nombre_completo, o nombre/apellido):
    un certificado sin titular no certifica nada, y es preferible fallar acá
    que entregar una hoja con un hueco. El DNI y el resto son opcionales — se
    manejan con los bloques `[[...]]` de la plantilla (ver render_text).
    """
    values = values or {}

    titular = " ".join(
        str(values.get(k) or "").strip()
        for k in ("nombre_completo", "nombre", "apellido")
    ).strip()
    if not titular:
        raise ValueError("El certificado necesita el nombre de la persona")

    page_w, page_h = landscape(A4)

    buffer = BytesIO()
    canvas = pdfcanvas.Canvas(buffer, pagesize=landscape(A4))
    canvas.setTitle(render_text(heading, values) or "Certificado")

    # ── Marco ──────────────────────────────────────────────────────────
    margin = 14 * mm
    canvas.setStrokeColor(DARK_TEAL)
    canvas.setLineWidth(1.4)
    canvas.roundRect(margin, margin, page_w - 2 * margin, page_h - 2 * margin, 10, stroke=1, fill=0)

    inner = margin + 3 * mm
    canvas.setStrokeColor(TEAL)
    canvas.setLineWidth(0.5)
    canvas.roundRect(inner, inner, page_w - 2 * inner, page_h - 2 * inner, 8, stroke=1, fill=0)

    # Detalle de color: dos barras cortas arriba, con los colores de ALMA.
    canvas.setFillColor(TEAL)
    canvas.rect(page_w / 2 - 30 * mm, page_h - margin - 3.5 * mm, 30 * mm, 1.6 * mm, stroke=0, fill=1)
    canvas.setFillColor(LAVENDER)
    canvas.rect(page_w / 2, page_h - margin - 3.5 * mm, 30 * mm, 1.6 * mm, stroke=0, fill=1)

    # ── Encabezado ─────────────────────────────────────────────────────
    cursor_y = page_h - margin - 16 * mm

    # Sin logo propio se usa el de ALMA. Chico y arriba: tiene que identificar
    # a la asociación sin comerse la hoja.
    logo = logo_bytes or default_logo_bytes()
    if logo:
        drawn = _draw_image(
            canvas, logo,
            center_x=page_w / 2, top_y=cursor_y, max_w=52 * mm, max_h=16 * mm,
        )
        cursor_y -= drawn + 8 * mm
    else:
        cursor_y -= 2 * mm

    canvas.setFillColor(DARK_TEAL)
    canvas.setFont(FONT_BOLD, 26)
    canvas.drawCentredString(page_w / 2, cursor_y - 20, solo_imprimible(render_text(heading, values)))
    cursor_y -= 20 + 7 * mm

    canvas.setStrokeColor(TEAL)
    canvas.setLineWidth(1)
    canvas.line(page_w / 2 - 35 * mm, cursor_y, page_w / 2 + 35 * mm, cursor_y)
    cursor_y -= 12 * mm

    # ── Cuerpo ─────────────────────────────────────────────────────────
    text = render_text(body, values)
    if text:
        # Textos largos bajan un punto de tamaño para no chocar con la firma.
        size = 13 if len(text) <= 420 else 11
        style = ParagraphStyle(
            "cuerpo",
            fontName=FONT,
            fontSize=size,
            leading=size + 7,
            alignment=TA_CENTER,
            textColor=CHARCOAL,
        )
        content_w = page_w - 2 * (32 * mm)
        paragraph = Paragraph(_to_markup(text), style)
        _, text_h = paragraph.wrap(content_w, page_h)
        paragraph.drawOn(canvas, (page_w - content_w) / 2, cursor_y - text_h)

    # ── Firma ──────────────────────────────────────────────────────────
    # Anclada abajo, no debajo del cuerpo: así todos los certificados tienen
    # la firma a la misma altura por más que cambie el largo del texto.
    signature_y = margin + 30 * mm

    if signature_bytes:
        _draw_image(
            canvas, signature_bytes,
            center_x=page_w / 2, top_y=signature_y + 20 * mm, max_w=50 * mm, max_h=18 * mm,
        )

    if signature_name or signature_role:
        canvas.setStrokeColor(CHARCOAL)
        canvas.setLineWidth(0.6)
        canvas.line(page_w / 2 - 30 * mm, signature_y, page_w / 2 + 30 * mm, signature_y)

        if signature_name:
            canvas.setFillColor(CHARCOAL)
            canvas.setFont(FONT_BOLD, 11)
            canvas.drawCentredString(page_w / 2, signature_y - 14, solo_imprimible(signature_name))
        if signature_role:
            canvas.setFillColor(GRAY)
            canvas.setFont(FONT, 9)
            canvas.drawCentredString(page_w / 2, signature_y - 26, solo_imprimible(signature_role))

    # ── Pie legal ──────────────────────────────────────────────────────
    note = render_text(legal_note, values)
    if note:
        style = ParagraphStyle(
            "legal",
            fontName=FONT,
            fontSize=7.5,
            leading=10,
            alignment=TA_CENTER,
            textColor=GRAY,
        )
        content_w = page_w - 2 * (30 * mm)
        paragraph = Paragraph(_to_markup(note), style)
        _, note_h = paragraph.wrap(content_w, page_h)
        paragraph.drawOn(canvas, (page_w - content_w) / 2, margin + 8 * mm)

    canvas.showPage()
    canvas.save()
    return buffer.getvalue()

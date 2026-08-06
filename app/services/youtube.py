"""
app/services/youtube.py — ALMA Backend — Utilidades de YouTube
==============================================================
Dos cosas, ambas sin API key ni credenciales:

1. extract_video_id(): el admin pega la URL como la tenga y sale el ID pelado.
   Se guarda SOLO el ID, así migrar a otro proveedor es cambiar provider + ref.

2. check_embeddable(): pregunta por oEmbed si el video se puede embeber.
   Un video subido como "Privado" NO se puede embeber y la persona vería un
   cuadro negro. Este chequeo lo detecta EN EL MOMENTO de cargarlo, que es
   el error operativo más probable de todo el módulo.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from app.utils.logger import log_warn

_OEMBED_URL = "https://www.youtube.com/oembed"
_TIMEOUT_SECONDS = 6

# Un ID de YouTube son 11 caracteres de [A-Za-z0-9_-]
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")

_URL_PATTERNS = (
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})"),
    re.compile(r"/embed/([A-Za-z0-9_-]{11})"),
    re.compile(r"/shorts/([A-Za-z0-9_-]{11})"),
    re.compile(r"/live/([A-Za-z0-9_-]{11})"),
    re.compile(r"/v/([A-Za-z0-9_-]{11})"),
)


def extract_video_id(value: str) -> Optional[str]:
    """Devuelve el ID de YouTube a partir de una URL o del ID pelado."""
    if not value:
        return None

    candidate = value.strip()
    if _ID_PATTERN.match(candidate):
        return candidate

    for pattern in _URL_PATTERNS:
        match = pattern.search(candidate)
        if match:
            return match.group(1)

    return None


def check_embeddable(video_id: str) -> Tuple[bool, Optional[dict]]:
    """¿Se puede embeber? Devuelve (ok, datos) con título y miniatura.

    oEmbed responde 200 para públicos y NO LISTADOS (que es lo que usa ALMA),
    y 401/403/404 para privados, borrados o inexistentes.

    Si el chequeo no se puede hacer (sin red, timeout), devuelve True: no
    tiene sentido bloquear la carga de contenido porque falló una consulta
    externa. El error se ve igual en el preview del formulario.
    """
    if not video_id:
        return False, None

    url = f"{_OEMBED_URL}?{urllib.parse.urlencode({'url': f'https://www.youtube.com/watch?v={video_id}', 'format': 'json'})}"

    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
            return True, {
                "title": data.get("title"),
                "author_name": data.get("author_name"),
                "thumbnail_url": data.get("thumbnail_url"),
            }

    except urllib.error.HTTPError as e:
        # 401/403 = privado o embebido deshabilitado. 404 = no existe.
        log_warn(
            "YouTube rechazó el video",
            module="capacitaciones",
            action="oembed_rejected",
            meta={"video_id": video_id, "status": e.code},
        )
        return False, None

    except Exception:
        log_warn(
            "No se pudo verificar el video contra YouTube, se acepta igual",
            module="capacitaciones",
            action="oembed_unreachable",
            meta={"video_id": video_id},
        )
        return True, None

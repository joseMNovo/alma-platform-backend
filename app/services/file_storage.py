"""
app/services/file_storage.py — ALMA Backend — Almacén de archivos en disco
==========================================================================
Los bytes viven en disco, la metadata en la tabla `files`.

Flujo de subida:
    base64 del cliente → decodificar → validar tamaño → verificar MAGIC BYTES
    → (imagen) redimensionar → escribir en FILES_STORAGE_PATH/<xx>/<guid>.<ext>

Reglas de seguridad (todas server-side, ninguna confía en el cliente):
  • El mime_type declarado NO se cree: se verifica contra los primeros bytes.
  • La extensión sale del mime verificado, jamás del nombre que mandó el cliente.
  • El path se arma SOLO con el GUID validado como UUID → sin path traversal.
  • Cada `purpose` define sus mimes permitidos y su tamaño máximo.
"""
import base64
import binascii
import hashlib
import io
import uuid
from pathlib import Path
from typing import Optional, Tuple

from config import settings
from app.utils.logger import log_info, log_warn

# Pillow es opcional: si no está instalado, el archivo se guarda tal cual llegó
# (el límite de tamaño se sigue aplicando). Ver requirements.txt.
try:
    from PIL import Image  # type: ignore

    _PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PIL_AVAILABLE = False


# ── Registro de usos permitidos ────────────────────────────────────────
# Para habilitar un uso nuevo (foto de voluntario, adjunto de anuncio, etc.)
# se agrega una entrada acá. La tabla `files` no cambia.
PURPOSES: dict[str, dict] = {
    "training_cover": {
        "label": "Portada de capacitación",
        "mimes": {"image/jpeg", "image/png", "image/webp"},
        "max_mb": 5,
        "max_px": None,  # None = usa FILES_MAX_IMAGE_PX
    },
    "certificate_logo": {
        "label": "Logo del certificado",
        "mimes": {"image/jpeg", "image/png", "image/webp"},
        "max_mb": 3,
        # Va impreso a ~55 mm de ancho: más de 1200 px no aporta nada al PDF
        # y solo lo engorda.
        "max_px": 1200,
    },
    "certificate_signature": {
        "label": "Firma del certificado",
        # PNG con fondo transparente es lo que mejor queda sobre la hoja.
        "mimes": {"image/jpeg", "image/png", "image/webp"},
        "max_mb": 3,
        "max_px": 1200,
    },
}

# Magic bytes → mime real. Es la verificación que impide que suban un .php
# diciendo que es image/jpeg: el nombre y el header mienten, los bytes no.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
)

_EXTENSIONS: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "application/pdf": "pdf",
}


class FileValidationError(ValueError):
    """Error de validación del archivo subido (se traduce a HTTP 422)."""


# ── Validación ─────────────────────────────────────────────────────────

def sniff_mime(raw: bytes) -> Optional[str]:
    """Devuelve el mime REAL según los primeros bytes, o None si no lo reconoce."""
    for signature, mime in _MAGIC:
        if raw.startswith(signature):
            return mime
    # WebP: "RIFF" + 4 bytes de tamaño + "WEBP"
    if len(raw) >= 12 and raw[0:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_payload(data_base64: str, purpose: str) -> bytes:
    """Decodifica el base64 del cliente y valida su tamaño.

    Acepta tanto el base64 pelado como un data URL completo
    ("data:image/jpeg;base64,...") por comodidad del frontend.
    """
    if not data_base64 or not data_base64.strip():
        raise FileValidationError("El archivo está vacío")

    payload = data_base64.strip()
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")

    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise FileValidationError("El contenido no es base64 válido")

    if not raw:
        raise FileValidationError("El archivo está vacío")

    max_mb = PURPOSES.get(purpose, {}).get("max_mb") or settings.FILES_MAX_UPLOAD_MB
    if len(raw) > max_mb * 1024 * 1024:
        raise FileValidationError(f"El archivo supera el máximo de {max_mb} MB")

    return raw


def validate_purpose(purpose: str) -> dict:
    """Verifica que el uso esté habilitado y devuelve su configuración."""
    config = PURPOSES.get(purpose)
    if not config:
        permitidos = ", ".join(sorted(PURPOSES)) or "(ninguno)"
        raise FileValidationError(
            f"Uso '{purpose}' no habilitado. Permitidos: {permitidos}"
        )
    return config


def verify_mime(raw: bytes, declared_mime: str, config: dict) -> str:
    """Verifica el tipo real contra los magic bytes y contra el allowlist del uso.

    Devuelve el mime REAL (que puede diferir del declarado: gana el real).
    """
    real_mime = sniff_mime(raw)
    if not real_mime:
        raise FileValidationError("No se reconoce el tipo de archivo")

    if real_mime not in config["mimes"]:
        permitidos = ", ".join(sorted(config["mimes"]))
        raise FileValidationError(
            f"Tipo de archivo no permitido ({real_mime}). Permitidos: {permitidos}"
        )

    if declared_mime and declared_mime != real_mime:
        # No es un error: el navegador a veces informa mal. Queda el registro.
        log_warn(
            "El tipo declarado no coincide con el contenido real",
            module="files",
            action="mime_mismatch",
            meta={"declarado": declared_mime, "real": real_mime},
        )

    return real_mime


# ── Optimización de imágenes ───────────────────────────────────────────

def optimize_image(raw: bytes, mime: str, config: dict) -> Tuple[bytes, str, Optional[int], Optional[int]]:
    """Redimensiona y recomprime la imagen. Devuelve (bytes, mime, ancho, alto).

    Es la palanca que mantiene chico el almacenamiento (y el backup): una foto
    de celular de 6 MB queda en ~200 KB. Si Pillow no está instalado o el
    formato no es imagen, devuelve el original sin tocar.
    """
    if not _PIL_AVAILABLE or not mime.startswith("image/"):
        return raw, mime, None, None

    max_px = config.get("max_px") or settings.FILES_MAX_IMAGE_PX

    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.load()

            # GIF puede ser animado: redimensionarlo lo rompería. Se deja intacto.
            if getattr(img, "is_animated", False):
                return raw, mime, img.width, img.height

            # Descarta metadata EXIF (incluye geolocalización del celular).
            if img.mode in ("RGBA", "LA", "P") and mime == "image/png":
                img = img.convert("RGBA")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            if max(img.width, img.height) > max_px:
                img.thumbnail((max_px, max_px), Image.LANCZOS)

            buffer = io.BytesIO()
            if mime == "image/png":
                img.save(buffer, format="PNG", optimize=True)
            elif mime == "image/webp":
                img.save(buffer, format="WEBP", quality=settings.FILES_IMAGE_QUALITY)
            else:
                img.save(
                    buffer,
                    format="JPEG",
                    quality=settings.FILES_IMAGE_QUALITY,
                    optimize=True,
                    progressive=True,
                )
                mime = "image/jpeg"

            return buffer.getvalue(), mime, img.width, img.height

    except Exception:
        # Una imagen rara no debe tumbar la subida: se guarda el original.
        log_warn(
            "No se pudo optimizar la imagen, se guarda el original",
            module="files",
            action="optimize_failed",
            meta={"mime": mime, "bytes": len(raw)},
        )
        return raw, mime, None, None


# ── Disco ──────────────────────────────────────────────────────────────

def _root() -> Path:
    root = Path(settings.FILES_STORAGE_PATH).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_guid(guid: str) -> str:
    """Valida que el guid sea un UUID real. Es lo que hace imposible el path traversal."""
    try:
        return str(uuid.UUID(str(guid)))
    except (ValueError, AttributeError, TypeError):
        raise FileValidationError("Identificador de archivo inválido")


def path_for(guid: str, extension: Optional[str]) -> Path:
    """Ruta en disco. Shardeada por los 2 primeros caracteres del guid para no
    terminar con decenas de miles de archivos en una sola carpeta."""
    safe = _safe_guid(guid)
    ext = (extension or "bin").lower().lstrip(".")
    if not ext.isalnum():
        ext = "bin"
    return _root() / safe[:2] / f"{safe}.{ext}"


def write_bytes(guid: str, extension: Optional[str], raw: bytes) -> Path:
    destination = path_for(guid, extension)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return destination


def read_bytes(guid: str, extension: Optional[str]) -> bytes:
    source = path_for(guid, extension)
    if not source.is_file():
        raise FileNotFoundError(f"El archivo {guid} no está en disco")
    return source.read_bytes()


def delete_bytes(guid: str, extension: Optional[str]) -> bool:
    """Borra el archivo físico. SOLO se llama desde el ABM, por decisión de una
    persona (purge=true). No hay ningún job automático que borre archivos."""
    target = path_for(guid, extension)
    if target.is_file():
        target.unlink()
        log_info("Archivo físico eliminado", module="files", action="purge", meta={"guid": guid})
        return True
    return False


def checksum(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def extension_for(mime: str) -> str:
    return _EXTENSIONS.get(mime, "bin")


def new_guid() -> str:
    return str(uuid.uuid4())

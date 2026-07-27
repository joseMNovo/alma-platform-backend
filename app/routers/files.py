"""
app/routers/files.py — ALMA Backend — ABM de archivos
======================================================
Subida en base64 (JSON, sin multipart), descarga en BYTES con su Content-Type
para que el navegador la cachee y sirva en un <img src> directo. La variante
base64 queda disponible en GET /files/{guid}/base64.

Borrado: la baja es lógica. El archivo físico solo se elimina con purge=true,
que es una acción explícita de una persona desde el ABM.
"""
import base64
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.file import File as FileModel
from app.schemas.file import FileCreate, FileUpdate, FileOut, FileBase64Out
from app.services import file_storage
from app.services.file_storage import FileValidationError
from app.utils.logger import log_info, log_warn, log_error

router = APIRouter()

# El contenido de un guid nunca cambia (para cambiar la imagen se sube otra),
# así que el navegador puede cachearlo para siempre.
_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


def _get_or_404(guid: str, db: Session) -> FileModel:
    archivo = db.query(FileModel).filter(FileModel.guid == guid).first()
    if not archivo:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return archivo


# ── Alta ───────────────────────────────────────────────────────────────

@router.post("/", response_model=FileOut, status_code=201)
def upload_file(data: FileCreate, db: Session = Depends(get_db)):
    """Recibe base64, valida, optimiza si es imagen y guarda en disco + metadata."""
    try:
        config = file_storage.validate_purpose(data.purpose)
        raw = file_storage.decode_payload(data.data_base64, data.purpose)
        mime = file_storage.verify_mime(raw, data.mime_type, config)
        raw, mime, width, height = file_storage.optimize_image(raw, mime, config)
    except FileValidationError as e:
        log_warn(
            "Archivo rechazado en la validación",
            module="files",
            action="upload_rejected",
            user=data.uploaded_by_volunteer_id,
            meta={"purpose": data.purpose, "motivo": str(e)},
        )
        raise HTTPException(status_code=422, detail=str(e))

    guid = file_storage.new_guid()
    extension = file_storage.extension_for(mime)

    # Primero el disco: si falla, no queda una fila apuntando a la nada.
    try:
        file_storage.write_bytes(guid, extension, raw)
    except OSError:
        log_error(
            "No se pudo escribir el archivo en disco",
            module="files",
            action="upload",
            meta={"guid": guid, "purpose": data.purpose},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="No se pudo guardar el archivo")

    archivo = FileModel(
        guid=guid,
        name=data.name,
        mime_type=mime,
        extension=extension,
        size_bytes=len(raw),
        checksum_sha256=file_storage.checksum(raw),
        purpose=data.purpose,
        owner_type=data.owner_type,
        owner_id=data.owner_id,
        width=width,
        height=height,
        is_active=True,
        uploaded_by_volunteer_id=data.uploaded_by_volunteer_id or None,
    )

    try:
        db.add(archivo)
        db.commit()
        db.refresh(archivo)
    except Exception:
        db.rollback()
        # La fila no entró: el archivo huérfano se limpia acá mismo, no queda
        # basura en disco ni hace falta ningún job de limpieza posterior.
        file_storage.delete_bytes(guid, extension)
        log_error(
            "Error al registrar el archivo, se descartó el archivo físico",
            module="files",
            action="upload",
            meta={"guid": guid},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="No se pudo registrar el archivo")

    log_info(
        "Archivo subido",
        module="files",
        action="upload",
        user=data.uploaded_by_volunteer_id,
        meta={
            "guid": guid,
            "purpose": data.purpose,
            "kb": round(len(raw) / 1024, 1),
            "owner": f"{data.owner_type}:{data.owner_id}" if data.owner_type else None,
        },
    )
    return archivo


# ── Consulta ───────────────────────────────────────────────────────────

@router.get("/", response_model=List[FileOut])
def list_files(
    purpose: Optional[str] = Query(None),
    owner_type: Optional[str] = Query(None),
    owner_id: Optional[int] = Query(None),
    include_inactive: bool = Query(False),
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    q = db.query(FileModel)
    if purpose:
        q = q.filter(FileModel.purpose == purpose)
    if owner_type:
        q = q.filter(FileModel.owner_type == owner_type)
    if owner_id is not None:
        q = q.filter(FileModel.owner_id == owner_id)
    if not include_inactive:
        q = q.filter(FileModel.is_active.is_(True))
    return q.order_by(FileModel.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/purposes")
def list_purposes():
    """Usos habilitados, para que la UI sepa qué puede ofrecer y con qué límites."""
    return [
        {
            "key": key,
            "label": cfg["label"],
            "mimes": sorted(cfg["mimes"]),
            "max_mb": cfg["max_mb"],
        }
        for key, cfg in file_storage.PURPOSES.items()
    ]


@router.get("/{guid}", response_model=FileOut)
def get_file(guid: str, db: Session = Depends(get_db)):
    return _get_or_404(guid, db)


@router.get("/{guid}/raw")
def get_file_raw(guid: str, db: Session = Depends(get_db)):
    """Los bytes con su Content-Type. Es lo que consume un <img src>."""
    archivo = _get_or_404(guid, db)
    try:
        raw = file_storage.read_bytes(archivo.guid, archivo.extension)
    except (FileNotFoundError, FileValidationError):
        # La fila existe pero el archivo no está: se avisa fuerte, porque
        # significa que alguien tocó el disco por fuera de la aplicación.
        log_error(
            "Archivo registrado pero ausente en disco",
            module="files",
            action="read_missing",
            meta={"guid": guid, "purpose": archivo.purpose},
        )
        raise HTTPException(status_code=404, detail="El archivo no está disponible")

    return Response(
        content=raw,
        media_type=archivo.mime_type,
        headers={
            "Cache-Control": _IMMUTABLE_CACHE,
            "Content-Disposition": f'inline; filename="{archivo.guid}.{archivo.extension}"',
        },
    )


@router.get("/{guid}/base64", response_model=FileBase64Out)
def get_file_base64(guid: str, db: Session = Depends(get_db)):
    """Variante base64, por si algún consumidor necesita el contenido dentro
    de un JSON. Para mostrar imágenes preferir /raw: se cachea y pesa 33% menos."""
    archivo = _get_or_404(guid, db)
    try:
        raw = file_storage.read_bytes(archivo.guid, archivo.extension)
    except (FileNotFoundError, FileValidationError):
        raise HTTPException(status_code=404, detail="El archivo no está disponible")

    return FileBase64Out(
        guid=archivo.guid,
        name=archivo.name,
        mime_type=archivo.mime_type,
        size_bytes=archivo.size_bytes,
        data_base64=base64.b64encode(raw).decode("ascii"),
    )


# ── Edición y baja ─────────────────────────────────────────────────────

@router.put("/{guid}", response_model=FileOut)
def update_file(guid: str, data: FileUpdate, db: Session = Depends(get_db)):
    """Actualiza la metadata (reasignar dueño, renombrar, reactivar).
    El contenido no se toca: para cambiar la imagen se sube una nueva."""
    archivo = _get_or_404(guid, db)

    payload = data.model_dump(exclude_unset=True)
    if "purpose" in payload and payload["purpose"]:
        try:
            file_storage.validate_purpose(payload["purpose"])
        except FileValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))

    try:
        for key, value in payload.items():
            setattr(archivo, key, value)
        db.commit()
        db.refresh(archivo)
    except Exception:
        db.rollback()
        log_error("Error al actualizar archivo", module="files", action="update", meta={"guid": guid}, exc_info=True)
        raise

    log_info("Archivo actualizado", module="files", action="update", meta={"guid": guid, "campos": list(payload)})
    return archivo


@router.delete("/{guid}", status_code=200)
def delete_file(
    guid: str,
    purge: bool = Query(False, description="true = borra también el archivo físico y la fila"),
    volunteer_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Baja lógica por defecto (is_active = 0, el archivo queda en disco).

    Con purge=true borra el archivo físico y la fila: es irreversible y siempre
    lo dispara una persona desde el ABM. No existe ningún proceso automático
    que borre archivos.
    """
    archivo = _get_or_404(guid, db)

    if not purge:
        archivo.is_active = False
        db.commit()
        log_info("Archivo dado de baja", module="files", action="deactivate", user=volunteer_id, meta={"guid": guid})
        return {"success": True, "purged": False}

    extension = archivo.extension
    try:
        db.delete(archivo)
        db.commit()
    except Exception:
        db.rollback()
        log_error("Error al eliminar archivo", module="files", action="purge", meta={"guid": guid}, exc_info=True)
        raise

    # El disco se toca DESPUÉS de confirmar la baja en la base: si esto falla,
    # queda un archivo suelto (inofensivo) en vez de una fila sin archivo.
    removed = file_storage.delete_bytes(guid, extension)
    log_info(
        "Archivo eliminado definitivamente",
        module="files",
        action="purge",
        user=volunteer_id,
        meta={"guid": guid, "archivo_en_disco": removed},
    )
    return {"success": True, "purged": True}

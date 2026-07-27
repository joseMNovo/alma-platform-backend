from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional
from datetime import datetime


class FileCreate(BaseModel):
    """Subida: el cliente manda el contenido en base64 y el backend valida todo."""

    name: str
    mime_type: str
    purpose: str
    data_base64: str
    owner_type: Optional[str] = None
    owner_id: Optional[int] = None
    uploaded_by_volunteer_id: Optional[int] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El nombre del archivo no puede estar vacío")
        # Solo se guarda para mostrar; el path se arma con el guid.
        return v.strip()[:255]

    @field_validator("purpose")
    @classmethod
    def purpose_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El uso (purpose) es obligatorio")
        return v.strip()

    @field_validator("owner_type")
    @classmethod
    def owner_type_strip(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            return v if v else None
        return None


class FileUpdate(BaseModel):
    """Edición de metadata. El contenido de un archivo NUNCA se modifica:
    para cambiar la imagen se sube una nueva (el guid es inmutable)."""

    name: Optional[str] = None
    purpose: Optional[str] = None
    owner_type: Optional[str] = None
    owner_id: Optional[int] = None
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("El nombre del archivo no puede estar vacío")
        return v.strip()[:255] if v else v


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    guid: str
    name: str
    mime_type: str
    extension: Optional[str] = None
    size_bytes: int = 0
    checksum_sha256: Optional[str] = None
    purpose: str
    owner_type: Optional[str] = None
    owner_id: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    is_active: bool = True
    uploaded_by_volunteer_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class FileBase64Out(BaseModel):
    """Variante base64 de la descarga. La forma normal de consumir un archivo es
    GET /files/{guid}/raw (bytes, cacheable por el navegador); esto existe para
    los casos donde hace falta el contenido embebido en un JSON."""

    guid: str
    name: str
    mime_type: str
    size_bytes: int
    data_base64: str

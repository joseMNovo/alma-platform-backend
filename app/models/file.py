from sqlalchemy import Column, Integer, String, Boolean, TIMESTAMP, ForeignKey, func
from app.database import Base


class File(Base):
    """Archivo subido (imagen, PDF). Los BYTES viven en disco; acá solo la metadata.

    Genérica a propósito: `purpose` dice para qué se subió y `owner_type`/`owner_id`
    a qué entidad pertenece, sin FK (es polimórfico). Agregar un uso nuevo no
    requiere tocar el esquema, solo el registro PURPOSES del servicio de storage.
    """

    __tablename__ = "files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Identidad pública e inmutable: nombre en disco y en la URL.
    guid = Column(String(36), nullable=False, unique=True)
    # Nombre original, SOLO para mostrar. Nunca se usa para armar el path.
    name = Column(String(255), nullable=False)
    # Tipo real verificado por magic bytes, no el que declaró el cliente.
    mime_type = Column(String(100), nullable=False)
    extension = Column(String(10), nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    checksum_sha256 = Column(String(64), nullable=True)
    purpose = Column(String(40), nullable=False)
    owner_type = Column(String(30), nullable=True)
    owner_id = Column(Integer, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    # Baja lógica. El archivo físico solo se borra si una persona lo pide (purge).
    is_active = Column(Boolean, nullable=False, default=True)
    uploaded_by_volunteer_id = Column(
        Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

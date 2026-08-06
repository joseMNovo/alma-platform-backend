"""
app/routers/configuracion.py — ALMA Backend — Ajustes generales
================================================================
Clave/valor sobre `app_settings`: lo que una persona configura desde la app
en vez de pedir que alguien toque el código o el .env.

La lógica vive en services/settings_service.py porque otros módulos la leen
(el link de pago lo necesita el serializador de capacitaciones).
"""
from typing import Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import settings_service
from app.utils.logger import log_error, log_info

router = APIRouter()


@router.get("/", response_model=Dict[str, Optional[str]])
def list_settings(db: Session = Depends(get_db)):
    """Todos los ajustes conocidos. Los que no están cargados vienen en None."""
    return settings_service.all_settings(db)


@router.put("/{key}", response_model=Dict[str, Optional[str]])
def set_setting(
    key: str,
    value: Optional[str] = Body(None, embed=True),
    updated_by_volunteer_id: Optional[int] = Body(None, embed=True),
    db: Session = Depends(get_db),
):
    """Guarda un ajuste. Vacío lo deja sin configurar."""
    try:
        clean = settings_service.set_value(db, key, value, actor_id=updated_by_volunteer_id)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        db.rollback()
        log_error("Error al guardar el ajuste", module="configuracion", action="set",
                  meta={"key": key}, exc_info=True)
        raise

    log_info("Ajuste actualizado", module="configuracion", action="set",
             user=updated_by_volunteer_id, meta={"key": key, "vacio": clean is None})
    return {key: clean}

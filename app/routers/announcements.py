from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import datetime
from typing import List, Optional

from app.database import get_db
from app.models.announcement import Announcement as AnnouncementModel
from app.models.announcement_dismissal import AnnouncementDismissal as DismissalModel
from app.schemas.announcement import (
    AnnouncementOut,
    AnnouncementCreate,
    AnnouncementUpdate,
    DismissCreate,
)
from app.utils.logger import log_info, log_warn, log_error

router = APIRouter()


def _allowed_audiences(role: str) -> List[str]:
    """Audiencias que le corresponden ver a un rol dado."""
    allowed = ["all"]
    if role == "admin":
        allowed += ["admin", "voluntario"]
    elif role == "voluntario":
        allowed += ["voluntario"]
    elif role == "participante":
        allowed += ["participante"]
    return allowed


# ── Pendiente para un usuario (lo que dispara el popup) ─────────────────
# IMPORTANTE: declarar antes de "/{id}" para que no lo capture la ruta dinámica.

@router.get("/pending", response_model=Optional[AnnouncementOut])
def get_pending(
    user_type: str = Query(...),
    user_id: int = Query(...),
    role: str = Query(...),
    db: Session = Depends(get_db),
):
    """Devuelve el anuncio activo más reciente que el usuario aún no descartó,
    dentro de su ventana de fechas y para su audiencia. None si no hay."""
    now = datetime.now()

    dismissed_subq = db.query(DismissalModel.announcement_id).filter(
        DismissalModel.user_type == user_type,
        DismissalModel.user_id == user_id,
    )

    announcement = (
        db.query(AnnouncementModel)
        .filter(AnnouncementModel.is_active.is_(True))
        .filter(AnnouncementModel.audience.in_(_allowed_audiences(role)))
        .filter(or_(AnnouncementModel.starts_at.is_(None), AnnouncementModel.starts_at <= now))
        .filter(or_(AnnouncementModel.ends_at.is_(None), AnnouncementModel.ends_at >= now))
        .filter(~AnnouncementModel.id.in_(dismissed_subq))
        .order_by(AnnouncementModel.created_at.desc())
        .first()
    )
    return announcement


@router.post("/{id}/dismiss", status_code=204)
def dismiss(id: int, data: DismissCreate, db: Session = Depends(get_db)):
    """Marca un anuncio como "no volver a mostrar" para un usuario. Idempotente."""
    announcement = db.query(AnnouncementModel).filter(AnnouncementModel.id == id).first()
    if not announcement:
        log_warn("Anuncio no encontrado al descartar", module="announcements", action="dismiss", meta={"id": id})
        raise HTTPException(status_code=404, detail="Anuncio no encontrado")

    existing = (
        db.query(DismissalModel)
        .filter(
            DismissalModel.announcement_id == id,
            DismissalModel.user_type == data.user_type,
            DismissalModel.user_id == data.user_id,
        )
        .first()
    )
    if existing:
        return  # ya estaba descartado, no hacemos nada

    try:
        db.add(DismissalModel(announcement_id=id, user_type=data.user_type, user_id=data.user_id))
        db.commit()
        log_info(
            "Anuncio descartado",
            module="announcements",
            action="dismiss",
            user=data.user_id,
            meta={"announcement_id": id, "user_type": data.user_type},
        )
    except Exception:
        db.rollback()
        log_error("Error al descartar anuncio", module="announcements", action="dismiss", meta={"id": id}, exc_info=True)
        raise


# ── CRUD (para gestionar a mano hoy; UI de carga a futuro) ──────────────

@router.get("/", response_model=List[AnnouncementOut])
def list_announcements(
    skip: int = 0,
    limit: int = 200,
    only_active: bool = False,
    db: Session = Depends(get_db),
):
    q = db.query(AnnouncementModel)
    if only_active:
        q = q.filter(AnnouncementModel.is_active.is_(True))
    return q.order_by(AnnouncementModel.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/{id}", response_model=AnnouncementOut)
def get_announcement(id: int, db: Session = Depends(get_db)):
    announcement = db.query(AnnouncementModel).filter(AnnouncementModel.id == id).first()
    if not announcement:
        raise HTTPException(status_code=404, detail="Anuncio no encontrado")
    return announcement


@router.post("/", response_model=AnnouncementOut, status_code=201)
def create_announcement(data: AnnouncementCreate, db: Session = Depends(get_db)):
    try:
        announcement = AnnouncementModel(**data.model_dump())
        db.add(announcement)
        db.commit()
        db.refresh(announcement)
        log_info("Anuncio creado", module="announcements", action="create", meta={"id": announcement.id})
        return announcement
    except Exception:
        db.rollback()
        log_error("Error al crear anuncio", module="announcements", action="create", exc_info=True)
        raise


@router.put("/{id}", response_model=AnnouncementOut)
def update_announcement(id: int, data: AnnouncementUpdate, db: Session = Depends(get_db)):
    announcement = db.query(AnnouncementModel).filter(AnnouncementModel.id == id).first()
    if not announcement:
        raise HTTPException(status_code=404, detail="Anuncio no encontrado")
    try:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(announcement, key, value)
        db.commit()
        db.refresh(announcement)
        log_info("Anuncio actualizado", module="announcements", action="update", meta={"id": id})
        return announcement
    except Exception:
        db.rollback()
        log_error("Error al actualizar anuncio", module="announcements", action="update", meta={"id": id}, exc_info=True)
        raise


@router.delete("/{id}", status_code=204)
def delete_announcement(id: int, db: Session = Depends(get_db)):
    announcement = db.query(AnnouncementModel).filter(AnnouncementModel.id == id).first()
    if not announcement:
        raise HTTPException(status_code=404, detail="Anuncio no encontrado")
    try:
        db.delete(announcement)
        db.commit()
        log_info("Anuncio eliminado", module="announcements", action="delete", meta={"id": id})
    except Exception:
        db.rollback()
        log_error("Error al eliminar anuncio", module="announcements", action="delete", meta={"id": id}, exc_info=True)
        raise

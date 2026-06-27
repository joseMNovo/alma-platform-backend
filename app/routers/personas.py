from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional

from app.database import get_db
from app.models.participant import ParticipantProfile as ProfileModel
from app.schemas.persona import Persona, PersonaCreate, PersonaUpdate
from app.utils.logger import log_info, log_warn, log_error

router = APIRouter()


# ── Personas (ABM de la base de datos) ────────────────────────────────

@router.get("/", response_model=List[Persona])
def list_personas(
    skip: int = 0,
    limit: int = 1000,
    name: Optional[str] = Query(None),
    last_name: Optional[str] = Query(None),
    cuit: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    province: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(ProfileModel)
    if name:
        q = q.filter(ProfileModel.name.ilike(f"%{name}%"))
    if last_name:
        q = q.filter(ProfileModel.last_name.ilike(f"%{last_name}%"))
    if cuit:
        q = q.filter(ProfileModel.cuit.ilike(f"%{cuit}%"))
    if city:
        q = q.filter(ProfileModel.city.ilike(f"%{city}%"))
    if province:
        q = q.filter(ProfileModel.province.ilike(f"%{province}%"))
    return (
        q.order_by(ProfileModel.name, ProfileModel.last_name)
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/{id}", response_model=Persona)
def get_persona(id: int, db: Session = Depends(get_db)):
    p = db.query(ProfileModel).filter(ProfileModel.id == id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    return p


@router.post("/", response_model=Persona, status_code=201)
def create_persona(data: PersonaCreate, db: Session = Depends(get_db)):
    try:
        p = ProfileModel(**data.model_dump())  # participant_id queda NULL (sin login)
        db.add(p)
        db.commit()
        db.refresh(p)
        log_info("Persona creada", module="personas", action="create_persona", meta={"id": p.id})
        return p
    except IntegrityError:
        db.rollback()
        log_warn("Email duplicado al crear persona", module="personas", action="create_persona")
        raise HTTPException(status_code=409, detail="Ya existe una persona con ese email")
    except Exception:
        db.rollback()
        log_error("Error al crear persona", module="personas", action="create_persona", exc_info=True)
        raise


@router.put("/{id}", response_model=Persona)
def update_persona(id: int, data: PersonaUpdate, db: Session = Depends(get_db)):
    p = db.query(ProfileModel).filter(ProfileModel.id == id).first()
    if not p:
        log_warn("Persona no encontrada para editar", module="personas", action="edit_persona", meta={"id": id})
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    try:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(p, key, value)
        db.commit()
        db.refresh(p)
        log_info("Persona actualizada", module="personas", action="edit_persona", meta={"id": id})
        return p
    except IntegrityError:
        db.rollback()
        log_warn("Email duplicado al editar persona", module="personas", action="edit_persona", meta={"id": id})
        raise HTTPException(status_code=409, detail="Ya existe una persona con ese email")
    except Exception:
        db.rollback()
        log_error("Error al actualizar persona", module="personas", action="edit_persona", meta={"id": id}, exc_info=True)
        raise


@router.delete("/{id}", status_code=204)
def delete_persona(id: int, db: Session = Depends(get_db)):
    # La tabla no tiene campo "activo" → baja física.
    p = db.query(ProfileModel).filter(ProfileModel.id == id).first()
    if not p:
        log_warn("Persona no encontrada para eliminar", module="personas", action="delete_persona", meta={"id": id})
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    try:
        db.delete(p)
        db.commit()
        log_info("Persona eliminada", module="personas", action="delete_persona", meta={"id": id})
    except Exception:
        db.rollback()
        log_error("Error al eliminar persona", module="personas", action="delete_persona", meta={"id": id}, exc_info=True)
        raise

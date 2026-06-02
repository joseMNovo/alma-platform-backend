from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.inscripcion import Inscripcion as InscripcionModel
from app.schemas.inscripcion import Inscripcion, InscripcionCreate, InscripcionUpdate
from app.utils.logger import log_info, log_warn, log_error

router = APIRouter()


@router.get("/", response_model=List[Inscripcion])
def list_inscripciones(
    skip: int = 0,
    limit: int = 100,
    user_id: Optional[int] = Query(None),
    type: Optional[str] = Query(None),
    item_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(InscripcionModel)
    if user_id is not None:
        q = q.filter(InscripcionModel.user_id == user_id)
    if type is not None:
        q = q.filter(InscripcionModel.type == type)
    if item_id is not None:
        q = q.filter(InscripcionModel.item_id == item_id)
    return q.offset(skip).limit(limit).all()


@router.get("/{id}", response_model=Inscripcion)
def get_inscripcion(id: int, db: Session = Depends(get_db)):
    i = db.query(InscripcionModel).filter(InscripcionModel.id == id).first()
    if not i:
        raise HTTPException(status_code=404, detail="Inscripción no encontrada")
    return i


@router.post("/", response_model=Inscripcion, status_code=201)
def create_inscripcion(data: InscripcionCreate, db: Session = Depends(get_db)):
    try:
        i = InscripcionModel(**data.model_dump())
        db.add(i)
        db.commit()
        db.refresh(i)
        log_info("Inscripción creada", module="inscripciones", action="enroll", meta={"id": i.id, "user_id": i.user_id, "type": i.type, "item_id": i.item_id})
        return i
    except Exception:
        log_error("Error al crear inscripción", module="inscripciones", action="enroll", exc_info=True)
        raise


@router.put("/{id}", response_model=Inscripcion)
def update_inscripcion(id: int, data: InscripcionUpdate, db: Session = Depends(get_db)):
    i = db.query(InscripcionModel).filter(InscripcionModel.id == id).first()
    if not i:
        log_warn("Inscripción no encontrada para editar", module="inscripciones", action="enroll", meta={"id": id})
        raise HTTPException(status_code=404, detail="Inscripción no encontrada")
    try:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(i, key, value)
        db.commit()
        db.refresh(i)
        log_info("Inscripción actualizada", module="inscripciones", action="enroll", meta={"id": id})
        return i
    except Exception:
        log_error("Error al actualizar inscripción", module="inscripciones", action="enroll", meta={"id": id}, exc_info=True)
        raise


@router.delete("/{id}", status_code=204)
def delete_inscripcion(id: int, db: Session = Depends(get_db)):
    i = db.query(InscripcionModel).filter(InscripcionModel.id == id).first()
    if not i:
        log_warn("Inscripción no encontrada para eliminar", module="inscripciones", action="unenroll", meta={"id": id})
        raise HTTPException(status_code=404, detail="Inscripción no encontrada")
    try:
        db.delete(i)
        db.commit()
        log_info("Inscripción eliminada", module="inscripciones", action="unenroll", meta={"id": id})
    except Exception:
        log_error("Error al eliminar inscripción", module="inscripciones", action="unenroll", meta={"id": id}, exc_info=True)
        raise

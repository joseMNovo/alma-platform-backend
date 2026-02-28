from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.inscripcion import Inscripcion as InscripcionModel
from app.schemas.inscripcion import Inscripcion, InscripcionCreate, InscripcionUpdate

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
    i = InscripcionModel(**data.model_dump())
    db.add(i)
    db.commit()
    db.refresh(i)
    return i


@router.put("/{id}", response_model=Inscripcion)
def update_inscripcion(id: int, data: InscripcionUpdate, db: Session = Depends(get_db)):
    i = db.query(InscripcionModel).filter(InscripcionModel.id == id).first()
    if not i:
        raise HTTPException(status_code=404, detail="Inscripción no encontrada")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(i, key, value)
    db.commit()
    db.refresh(i)
    return i


@router.delete("/{id}", status_code=204)
def delete_inscripcion(id: int, db: Session = Depends(get_db)):
    i = db.query(InscripcionModel).filter(InscripcionModel.id == id).first()
    if not i:
        raise HTTPException(status_code=404, detail="Inscripción no encontrada")
    db.delete(i)
    db.commit()

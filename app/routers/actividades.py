from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.actividad import Actividad as ActividadModel
from app.schemas.actividad import Actividad, ActividadCreate, ActividadUpdate

router = APIRouter()


@router.get("/", response_model=List[Actividad])
def list_actividades(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(ActividadModel)
    if status is not None:
        q = q.filter(ActividadModel.status == status)
    return q.offset(skip).limit(limit).all()


@router.get("/{id}", response_model=Actividad)
def get_actividad(id: int, db: Session = Depends(get_db)):
    a = db.query(ActividadModel).filter(ActividadModel.id == id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Actividad no encontrada")
    return a


@router.post("/", response_model=Actividad, status_code=201)
def create_actividad(data: ActividadCreate, db: Session = Depends(get_db)):
    a = ActividadModel(**data.model_dump())
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@router.put("/{id}", response_model=Actividad)
def update_actividad(id: int, data: ActividadUpdate, db: Session = Depends(get_db)):
    a = db.query(ActividadModel).filter(ActividadModel.id == id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Actividad no encontrada")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(a, key, value)
    db.commit()
    db.refresh(a)
    return a


@router.delete("/{id}", status_code=204)
def delete_actividad(id: int, db: Session = Depends(get_db)):
    a = db.query(ActividadModel).filter(ActividadModel.id == id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Actividad no encontrada")
    db.delete(a)
    db.commit()

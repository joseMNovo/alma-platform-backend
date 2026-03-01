from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.actividad import Actividad as ActividadModel
from app.schemas.actividad import Actividad, ActividadCreate, ActividadUpdate

router = APIRouter()


def _serialize(a: ActividadModel) -> dict:
    """Convert ORM row to dict, including creator name from joined relationship."""
    creator_name = None
    if a.created_by is not None:
        creator_name = f"{a.created_by.name or ''} {a.created_by.last_name or ''}".strip() or None
    return {
        "id": a.id,
        "name": a.name,
        "description": a.description,
        "status": a.status,
        "created_at": a.created_at,
        "updated_at": a.updated_at,
        "created_by_volunteer_id": a.created_by_volunteer_id,
        "created_by_name": creator_name,
    }


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
    return [_serialize(a) for a in q.offset(skip).limit(limit).all()]


@router.get("/{id}", response_model=Actividad)
def get_actividad(id: int, db: Session = Depends(get_db)):
    a = db.query(ActividadModel).filter(ActividadModel.id == id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Actividad no encontrada")
    return _serialize(a)


@router.post("/", response_model=Actividad, status_code=201)
def create_actividad(data: ActividadCreate, db: Session = Depends(get_db)):
    a = ActividadModel(**data.model_dump())
    db.add(a)
    db.commit()
    db.refresh(a)
    return _serialize(a)


@router.put("/{id}", response_model=Actividad)
def update_actividad(id: int, data: ActividadUpdate, db: Session = Depends(get_db)):
    a = db.query(ActividadModel).filter(ActividadModel.id == id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Actividad no encontrada")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(a, key, value)
    db.commit()
    db.refresh(a)
    return _serialize(a)


@router.delete("/{id}", status_code=204)
def delete_actividad(id: int, db: Session = Depends(get_db)):
    a = db.query(ActividadModel).filter(ActividadModel.id == id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Actividad no encontrada")
    db.delete(a)
    db.commit()

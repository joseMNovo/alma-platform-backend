from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.taller import Taller as TallerModel
from app.schemas.taller import Taller, TallerCreate, TallerUpdate

router = APIRouter()


@router.get("/", response_model=List[Taller])
def list_talleres(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(TallerModel)
    if status is not None:
        q = q.filter(TallerModel.status == status)
    return q.offset(skip).limit(limit).all()


@router.get("/{id}", response_model=Taller)
def get_taller(id: int, db: Session = Depends(get_db)):
    t = db.query(TallerModel).filter(TallerModel.id == id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Taller no encontrado")
    return t


@router.post("/", response_model=Taller, status_code=201)
def create_taller(data: TallerCreate, db: Session = Depends(get_db)):
    t = TallerModel(**data.model_dump())
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.put("/{id}", response_model=Taller)
def update_taller(id: int, data: TallerUpdate, db: Session = Depends(get_db)):
    t = db.query(TallerModel).filter(TallerModel.id == id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Taller no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(t, key, value)
    db.commit()
    db.refresh(t)
    return t


@router.delete("/{id}", status_code=204)
def delete_taller(id: int, db: Session = Depends(get_db)):
    t = db.query(TallerModel).filter(TallerModel.id == id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Taller no encontrado")
    db.delete(t)
    db.commit()

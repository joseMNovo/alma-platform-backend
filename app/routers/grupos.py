from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.grupo import Grupo as GrupoModel
from app.schemas.grupo import Grupo, GrupoCreate, GrupoUpdate

router = APIRouter()


@router.get("/", response_model=List[Grupo])
def list_grupos(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(GrupoModel)
    if status is not None:
        q = q.filter(GrupoModel.status == status)
    return q.offset(skip).limit(limit).all()


@router.get("/{id}", response_model=Grupo)
def get_grupo(id: int, db: Session = Depends(get_db)):
    g = db.query(GrupoModel).filter(GrupoModel.id == id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    return g


@router.post("/", response_model=Grupo, status_code=201)
def create_grupo(data: GrupoCreate, db: Session = Depends(get_db)):
    g = GrupoModel(**data.model_dump())
    db.add(g)
    db.commit()
    db.refresh(g)
    return g


@router.put("/{id}", response_model=Grupo)
def update_grupo(id: int, data: GrupoUpdate, db: Session = Depends(get_db)):
    g = db.query(GrupoModel).filter(GrupoModel.id == id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(g, key, value)
    db.commit()
    db.refresh(g)
    return g


@router.delete("/{id}", status_code=204)
def delete_grupo(id: int, db: Session = Depends(get_db)):
    g = db.query(GrupoModel).filter(GrupoModel.id == id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    db.delete(g)
    db.commit()

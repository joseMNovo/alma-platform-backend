from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.voluntario import Voluntario as VoluntarioModel
from app.schemas.voluntario import Voluntario, VoluntarioCreate, VoluntarioUpdate, VoluntarioAuth

router = APIRouter()


@router.get("/auth/{email}", response_model=VoluntarioAuth)
def get_voluntario_auth(email: str, db: Session = Depends(get_db)):
    """Endpoint interno para autenticación — devuelve pin_hash."""
    v = db.query(VoluntarioModel).filter(VoluntarioModel.email == email).first()
    if not v:
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    return v


@router.get("/by-email/{email}", response_model=Voluntario)
def get_voluntario_by_email(email: str, db: Session = Depends(get_db)):
    v = db.query(VoluntarioModel).filter(VoluntarioModel.email == email).first()
    if not v:
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    return v


@router.get("/", response_model=List[Voluntario])
def list_voluntarios(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = Query(None),
    is_admin: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(VoluntarioModel)
    if status is not None:
        q = q.filter(VoluntarioModel.status == status)
    if is_admin is not None:
        q = q.filter(VoluntarioModel.is_admin == is_admin)
    return q.offset(skip).limit(limit).all()


@router.get("/{id}", response_model=Voluntario)
def get_voluntario(id: int, db: Session = Depends(get_db)):
    v = db.query(VoluntarioModel).filter(VoluntarioModel.id == id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    return v


@router.post("/", response_model=Voluntario, status_code=201)
def create_voluntario(data: VoluntarioCreate, db: Session = Depends(get_db)):
    v = VoluntarioModel(**data.model_dump())
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@router.put("/{id}", response_model=Voluntario)
def update_voluntario(id: int, data: VoluntarioUpdate, db: Session = Depends(get_db)):
    v = db.query(VoluntarioModel).filter(VoluntarioModel.id == id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(v, key, value)
    db.commit()
    db.refresh(v)
    return v


@router.delete("/{id}", status_code=204)
def delete_voluntario(id: int, db: Session = Depends(get_db)):
    v = db.query(VoluntarioModel).filter(VoluntarioModel.id == id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Voluntario no encontrado")
    db.delete(v)
    db.commit()

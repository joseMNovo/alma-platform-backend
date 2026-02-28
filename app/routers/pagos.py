from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.pago import Pago as PagoModel
from app.schemas.pago import Pago, PagoCreate, PagoUpdate

router = APIRouter()


@router.get("/", response_model=List[Pago])
def list_pagos(
    skip: int = 0,
    limit: int = 100,
    user_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(PagoModel)
    if user_id is not None:
        q = q.filter(PagoModel.user_id == user_id)
    if status is not None:
        q = q.filter(PagoModel.status == status)
    return q.offset(skip).limit(limit).all()


@router.get("/{id}", response_model=Pago)
def get_pago(id: int, db: Session = Depends(get_db)):
    p = db.query(PagoModel).filter(PagoModel.id == id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    return p


@router.post("/", response_model=Pago, status_code=201)
def create_pago(data: PagoCreate, db: Session = Depends(get_db)):
    p = PagoModel(**data.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.put("/{id}", response_model=Pago)
def update_pago(id: int, data: PagoUpdate, db: Session = Depends(get_db)):
    p = db.query(PagoModel).filter(PagoModel.id == id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(p, key, value)
    db.commit()
    db.refresh(p)
    return p


@router.delete("/{id}", status_code=204)
def delete_pago(id: int, db: Session = Depends(get_db)):
    p = db.query(PagoModel).filter(PagoModel.id == id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    db.delete(p)
    db.commit()

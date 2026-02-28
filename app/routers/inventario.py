from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.inventario import Inventario as InventarioModel
from app.schemas.inventario import Inventario, InventarioCreate, InventarioUpdate

router = APIRouter()


@router.get("/", response_model=List[Inventario])
def list_inventario(
    skip: int = 0,
    limit: int = 100,
    category: Optional[str] = Query(None),
    assigned_volunteer_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(InventarioModel)
    if category is not None:
        q = q.filter(InventarioModel.category == category)
    if assigned_volunteer_id is not None:
        q = q.filter(InventarioModel.assigned_volunteer_id == assigned_volunteer_id)
    return q.offset(skip).limit(limit).all()


@router.get("/{id}", response_model=Inventario)
def get_inventario(id: int, db: Session = Depends(get_db)):
    item = db.query(InventarioModel).filter(InventarioModel.id == id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Ítem de inventario no encontrado")
    return item


@router.post("/", response_model=Inventario, status_code=201)
def create_inventario(data: InventarioCreate, db: Session = Depends(get_db)):
    item = InventarioModel(**data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/{id}", response_model=Inventario)
def update_inventario(id: int, data: InventarioUpdate, db: Session = Depends(get_db)):
    item = db.query(InventarioModel).filter(InventarioModel.id == id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Ítem de inventario no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{id}", status_code=204)
def delete_inventario(id: int, db: Session = Depends(get_db)):
    item = db.query(InventarioModel).filter(InventarioModel.id == id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Ítem de inventario no encontrado")
    db.delete(item)
    db.commit()

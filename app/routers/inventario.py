from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.inventario import Inventario as InventarioModel
from app.schemas.inventario import Inventario, InventarioCreate, InventarioUpdate
from app.utils.logger import log_info, log_warn, log_error

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
    try:
        item = InventarioModel(**data.model_dump())
        db.add(item)
        db.commit()
        db.refresh(item)
        log_info("Ítem de inventario creado", module="inventario", action="create_item", meta={"id": item.id, "name": item.name})
        return item
    except Exception:
        log_error("Error al crear ítem de inventario", module="inventario", action="create_item", exc_info=True)
        raise


@router.put("/{id}", response_model=Inventario)
def update_inventario(id: int, data: InventarioUpdate, db: Session = Depends(get_db)):
    item = db.query(InventarioModel).filter(InventarioModel.id == id).first()
    if not item:
        log_warn("Ítem de inventario no encontrado para editar", module="inventario", action="edit_item", meta={"id": id})
        raise HTTPException(status_code=404, detail="Ítem de inventario no encontrado")
    try:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        db.commit()
        db.refresh(item)
        log_info("Ítem de inventario actualizado", module="inventario", action="edit_item", meta={"id": id})
        return item
    except Exception:
        log_error("Error al actualizar ítem de inventario", module="inventario", action="edit_item", meta={"id": id}, exc_info=True)
        raise


@router.delete("/{id}", status_code=204)
def delete_inventario(id: int, db: Session = Depends(get_db)):
    item = db.query(InventarioModel).filter(InventarioModel.id == id).first()
    if not item:
        log_warn("Ítem de inventario no encontrado para eliminar", module="inventario", action="delete_item", meta={"id": id})
        raise HTTPException(status_code=404, detail="Ítem de inventario no encontrado")
    try:
        db.delete(item)
        db.commit()
        log_info("Ítem de inventario eliminado", module="inventario", action="delete_item", meta={"id": id})
    except Exception:
        log_error("Error al eliminar ítem de inventario", module="inventario", action="delete_item", meta={"id": id}, exc_info=True)
        raise

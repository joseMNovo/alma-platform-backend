from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.pendiente import Pendiente as PendienteModel, PendingItem as PendingItemModel
from app.schemas.pendiente import (
    Pendiente, PendienteCreate, PendienteUpdate,
    PendingItem, PendingItemCreate, PendingItemUpdate,
    SyncPendientesRequest,
)

router = APIRouter()


# ── Pendientes ────────────────────────────────────────────────────────

@router.get("/", response_model=List[Pendiente])
def list_pendientes(
    skip: int = 0,
    limit: int = 100,
    completed: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(PendienteModel)
    if completed is not None:
        q = q.filter(PendienteModel.completed == completed)
    return q.offset(skip).limit(limit).all()


@router.get("/{id}", response_model=Pendiente)
def get_pendiente(id: str, db: Session = Depends(get_db)):
    p = db.query(PendienteModel).filter(PendienteModel.id == id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Pendiente no encontrado")
    return p


@router.post("/", response_model=Pendiente, status_code=201)
def create_pendiente(data: PendienteCreate, db: Session = Depends(get_db)):
    p = PendienteModel(**data.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.put("/{id}", response_model=Pendiente)
def update_pendiente(id: str, data: PendienteUpdate, db: Session = Depends(get_db)):
    p = db.query(PendienteModel).filter(PendienteModel.id == id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Pendiente no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(p, key, value)
    db.commit()
    db.refresh(p)
    return p


@router.delete("/{id}", status_code=204)
def delete_pendiente(id: str, db: Session = Depends(get_db)):
    p = db.query(PendienteModel).filter(PendienteModel.id == id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Pendiente no encontrado")
    db.delete(p)
    db.commit()


# ── Pending Items ─────────────────────────────────────────────────────

@router.get("/{pending_id}/items", response_model=List[PendingItem])
def list_pending_items(pending_id: str, db: Session = Depends(get_db)):
    return db.query(PendingItemModel).filter(PendingItemModel.pending_id == pending_id).all()


@router.post("/{pending_id}/items", response_model=PendingItem, status_code=201)
def create_pending_item(pending_id: str, data: PendingItemCreate, db: Session = Depends(get_db)):
    if not db.query(PendienteModel).filter(PendienteModel.id == pending_id).first():
        raise HTTPException(status_code=404, detail="Pendiente no encontrado")
    item = PendingItemModel(**{**data.model_dump(), "pending_id": pending_id})
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/{pending_id}/items/{item_id}", response_model=PendingItem)
def update_pending_item(pending_id: str, item_id: str, data: PendingItemUpdate, db: Session = Depends(get_db)):
    item = db.query(PendingItemModel).filter(
        PendingItemModel.id == item_id,
        PendingItemModel.pending_id == pending_id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{pending_id}/items/{item_id}", status_code=204)
def delete_pending_item(pending_id: str, item_id: str, db: Session = Depends(get_db)):
    item = db.query(PendingItemModel).filter(
        PendingItemModel.id == item_id,
        PendingItemModel.pending_id == pending_id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    db.delete(item)
    db.commit()


# ── Sync (reemplaza toda la tabla de una vez) ─────────────────────────

@router.post("/sync")
def sync_pendientes(data: SyncPendientesRequest, db: Session = Depends(get_db)):
    """Borra todos los pendientes y sub-items y los reemplaza con los datos enviados."""
    db.query(PendingItemModel).delete(synchronize_session=False)
    db.query(PendienteModel).delete(synchronize_session=False)

    for task in data.tasks:
        p = PendienteModel(
            id=task.id,
            description=task.description,
            assigned_volunteer_id=task.assigned_volunteer_id,
            completed=task.completed,
            created_date=task.created_date,
            completed_date=task.completed_date,
        )
        db.add(p)
        for sub in task.sub_items:
            pi = PendingItemModel(
                id=sub.id,
                pending_id=task.id,
                description=sub.description,
                assigned_volunteer_id=sub.assigned_volunteer_id,
                completed=sub.completed,
                created_date=sub.created_date,
                completed_date=sub.completed_date,
            )
            db.add(pi)

    db.commit()
    return {"synced": len(data.tasks)}

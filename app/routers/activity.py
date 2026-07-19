from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.activity_event import ActivityEvent as ActivityEventModel
from app.schemas.activity_event import ActivityEventCreate, ActivityEventOut, ActivityUserSummary
from app.utils.logger import log_error

router = APIRouter()


@router.get("/summary", response_model=List[ActivityUserSummary])
def get_summary(db: Session = Depends(get_db)):
    """Resumen por usuario: cantidad de logins, último ingreso, vistas por
    módulo y acciones (create/edit/delete) por módulo. Se agrega en Python
    a partir de todos los eventos (escala de ONG chica, sin tabla de rollup).
    Los eventos se recorren en orden ascendente para que el último escrito
    gane como "rol actual" del usuario (puede cambiar si lo promueven a admin).
    """
    rows = db.query(ActivityEventModel).order_by(ActivityEventModel.created_at.asc()).all()

    aggregated: dict = {}
    for row in rows:
        key = (row.user_type, row.user_id)
        entry = aggregated.setdefault(key, {
            "user_type": row.user_type,
            "user_id": row.user_id,
            "role": row.role,
            "login_count": 0,
            "last_login": None,
            "view_counts": {},
            "action_counts": {},
        })
        entry["role"] = row.role

        if row.event_type == "login":
            entry["login_count"] += 1
            entry["last_login"] = row.created_at
        elif row.event_type == "view" and row.module:
            entry["view_counts"][row.module] = entry["view_counts"].get(row.module, 0) + 1
        elif row.event_type in ("create", "edit", "delete") and row.module:
            label = f"{row.module}:{row.event_type}"
            entry["action_counts"][label] = entry["action_counts"].get(label, 0) + 1

    return list(aggregated.values())


@router.get("/", response_model=List[ActivityEventOut])
def list_events(
    user_type: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    event_type: Optional[str] = Query(None),
    module: Optional[str] = Query(None),
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """Timeline de eventos, más recientes primero. Usado para el detalle por usuario."""
    q = db.query(ActivityEventModel)
    if user_type:
        q = q.filter(ActivityEventModel.user_type == user_type)
    if user_id is not None:
        q = q.filter(ActivityEventModel.user_id == user_id)
    if event_type:
        q = q.filter(ActivityEventModel.event_type == event_type)
    if module:
        q = q.filter(ActivityEventModel.module == module)
    return q.order_by(ActivityEventModel.created_at.desc()).offset(skip).limit(limit).all()


@router.post("/", response_model=ActivityEventOut, status_code=201)
def create_event(data: ActivityEventCreate, db: Session = Depends(get_db)):
    """Registra un evento de actividad (login / vista / alta / edición / baja).
    No loguea éxito a texto (winston ya cubre esas acciones en el Next.js API
    layer) para no duplicar/inflar los logs con eventos de alta frecuencia."""
    try:
        event = ActivityEventModel(**data.model_dump())
        db.add(event)
        db.commit()
        db.refresh(event)
        return event
    except Exception:
        db.rollback()
        log_error(
            "Error al registrar evento de actividad",
            module="activity",
            action="log_event",
            meta=data.model_dump(),
            exc_info=True,
        )
        raise

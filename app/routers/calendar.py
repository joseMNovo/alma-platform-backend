from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from datetime import date, timedelta

from app.database import get_db
from app.models.calendar import (
    CalendarInstance as CIModel,
    CalendarAssignment as CAModel,
    CalendarEventParticipant as CEPModel,
)
from app.schemas.calendar import (
    CalendarInstance, CalendarInstanceCreate, CalendarInstanceUpdate,
    CalendarInstanceRich, VolunteerRef,
    CalendarAssignment, CalendarAssignmentCreate, CalendarAssignmentUpdate,
    AssignmentUpsertRequest,
    CalendarEventParticipant, CalendarEventParticipantCreate, CalendarEventParticipantUpdate,
    BulkDeleteFilters, GenerateCalendarParams,
)

router = APIRouter()


def _fmt_time(val) -> str:
    """Convierte timedelta (PyMySQL TIME) a string HH:MM:SS."""
    if val is None:
        return "00:00:00"
    if isinstance(val, timedelta):
        total = int(val.total_seconds())
        h, rem = divmod(abs(total), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    return str(val)


# ── Calendar Instances ────────────────────────────────────────────────

@router.get("/instances-rich", response_model=List[CalendarInstanceRich])
def list_instances_rich(
    year: int = Query(...),
    month: Optional[int] = Query(None),
    type: Optional[str] = Query(None),
    volunteer_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Instancias de calendario con coordinadores y co-coordinadores (JOIN a voluntarios)."""
    sql = """
    SELECT
        ci.id, ci.type, ci.source_id, ci.date, ci.start_time, ci.end_time, ci.notes, ci.status,
        coord_v.id   AS coord_id,   coord_v.name   AS coord_name,   coord_v.last_name AS coord_last,
        cocoord_v.id AS cocoord_id, cocoord_v.name AS cocoord_name, cocoord_v.last_name AS cocoord_last
    FROM calendar_instances ci
    LEFT JOIN calendar_assignments coord_ca
        ON coord_ca.instance_id = ci.id AND coord_ca.role = 'coordinator'
    LEFT JOIN voluntarios coord_v ON coord_v.id = coord_ca.volunteer_id
    LEFT JOIN calendar_assignments cocoord_ca
        ON cocoord_ca.instance_id = ci.id AND cocoord_ca.role = 'co_coordinator'
    LEFT JOIN voluntarios cocoord_v ON cocoord_v.id = cocoord_ca.volunteer_id
    WHERE YEAR(ci.date) = :year
    """
    params: dict = {"year": year}

    if month is not None:
        sql += " AND MONTH(ci.date) = :month"
        params["month"] = month
    if type is not None:
        sql += " AND ci.type = :type"
        params["type"] = type
    if volunteer_id is not None:
        sql += " AND (coord_ca.volunteer_id = :vol_id OR cocoord_ca.volunteer_id = :vol_id)"
        params["vol_id"] = volunteer_id

    sql += " ORDER BY ci.date ASC, ci.start_time ASC"

    rows = db.execute(text(sql), params).fetchall()
    return [
        {
            "id": row.id,
            "type": row.type,
            "source_id": row.source_id,
            "date": str(row.date),
            "start_time": _fmt_time(row.start_time),
            "end_time": _fmt_time(row.end_time),
            "notes": row.notes,
            "status": row.status,
            "coordinator": {"id": row.coord_id, "name": row.coord_name, "last_name": row.coord_last or ""}
                if row.coord_id else None,
            "co_coordinator": {"id": row.cocoord_id, "name": row.cocoord_name, "last_name": row.cocoord_last or ""}
                if row.cocoord_id else None,
        }
        for row in rows
    ]


@router.get("/instances", response_model=List[CalendarInstance])
def list_instances(
    skip: int = 0,
    limit: int = 100,
    type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    source_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(CIModel)
    if type is not None:
        q = q.filter(CIModel.type == type)
    if status is not None:
        q = q.filter(CIModel.status == status)
    if date_from is not None:
        q = q.filter(CIModel.date >= date_from)
    if date_to is not None:
        q = q.filter(CIModel.date <= date_to)
    if source_id is not None:
        q = q.filter(CIModel.source_id == source_id)
    return q.order_by(CIModel.date).offset(skip).limit(limit).all()


@router.get("/instances/{id}", response_model=CalendarInstance)
def get_instance(id: int, db: Session = Depends(get_db)):
    ci = db.query(CIModel).filter(CIModel.id == id).first()
    if not ci:
        raise HTTPException(status_code=404, detail="Instancia no encontrada")
    return ci


@router.post("/instances", response_model=CalendarInstance, status_code=201)
def create_instance(data: CalendarInstanceCreate, db: Session = Depends(get_db)):
    ci = CIModel(**data.model_dump())
    db.add(ci)
    db.commit()
    db.refresh(ci)
    return ci


@router.put("/instances/{id}", response_model=CalendarInstance)
def update_instance(id: int, data: CalendarInstanceUpdate, db: Session = Depends(get_db)):
    ci = db.query(CIModel).filter(CIModel.id == id).first()
    if not ci:
        raise HTTPException(status_code=404, detail="Instancia no encontrada")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(ci, key, value)
    db.commit()
    db.refresh(ci)
    return ci


@router.delete("/instances/{id}", status_code=204)
def delete_instance(id: int, db: Session = Depends(get_db)):
    ci = db.query(CIModel).filter(CIModel.id == id).first()
    if not ci:
        raise HTTPException(status_code=404, detail="Instancia no encontrada")
    db.delete(ci)
    db.commit()


# ── Calendar Assignments ──────────────────────────────────────────────

@router.get("/instances/{instance_id}/assignments", response_model=List[CalendarAssignment])
def list_assignments(instance_id: int, db: Session = Depends(get_db)):
    return db.query(CAModel).filter(CAModel.instance_id == instance_id).all()


@router.post("/instances/{instance_id}/assignments", response_model=CalendarAssignment, status_code=201)
def create_assignment(instance_id: int, data: CalendarAssignmentCreate, db: Session = Depends(get_db)):
    if not db.query(CIModel).filter(CIModel.id == instance_id).first():
        raise HTTPException(status_code=404, detail="Instancia no encontrada")
    ca = CAModel(**{**data.model_dump(), "instance_id": instance_id})
    db.add(ca)
    db.commit()
    db.refresh(ca)
    return ca


@router.put("/instances/{instance_id}/assignments/by-role/{role}", response_model=CalendarAssignment)
def upsert_assignment_by_role(
    instance_id: int, role: str, data: AssignmentUpsertRequest, db: Session = Depends(get_db)
):
    """Crea o actualiza el asignado para un rol específico en una instancia."""
    ca = db.query(CAModel).filter(CAModel.instance_id == instance_id, CAModel.role == role).first()
    if ca:
        ca.volunteer_id = data.volunteer_id
    else:
        ca = CAModel(instance_id=instance_id, volunteer_id=data.volunteer_id, role=role)
        db.add(ca)
    db.commit()
    db.refresh(ca)
    return ca


@router.delete("/instances/{instance_id}/assignments/by-role/{role}", status_code=204)
def delete_assignment_by_role(instance_id: int, role: str, db: Session = Depends(get_db)):
    ca = db.query(CAModel).filter(CAModel.instance_id == instance_id, CAModel.role == role).first()
    if not ca:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")
    db.delete(ca)
    db.commit()


@router.put("/assignments/{id}", response_model=CalendarAssignment)
def update_assignment(id: int, data: CalendarAssignmentUpdate, db: Session = Depends(get_db)):
    ca = db.query(CAModel).filter(CAModel.id == id).first()
    if not ca:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(ca, key, value)
    db.commit()
    db.refresh(ca)
    return ca


@router.delete("/assignments/{id}", status_code=204)
def delete_assignment(id: int, db: Session = Depends(get_db)):
    ca = db.query(CAModel).filter(CAModel.id == id).first()
    if not ca:
        raise HTTPException(status_code=404, detail="Asignación no encontrada")
    db.delete(ca)
    db.commit()


# ── Generación bulk ───────────────────────────────────────────────────

@router.post("/generate")
def generate_calendar_instances(params: GenerateCalendarParams, db: Session = Depends(get_db)):
    """Genera instancias de calendario alternando grupo/taller en un rango de fechas."""
    start_time = params.start_time
    end_hour = int(start_time.split(":")[0]) + 2
    end_time = f"{str(end_hour).zfill(2)}:{start_time.split(':')[1]}:00"

    start = date.fromisoformat(params.start_date)
    end = date.fromisoformat(params.end_date)

    types = ["grupo", "taller"]
    type_index = 1 if params.first_type == "taller" else 0

    created_ids = []
    current = start

    while current <= end:
        tipo = types[type_index % 2]
        source_id = params.source_group_id if tipo == "grupo" else params.source_workshop_id

        ci = CIModel(
            type=tipo,
            source_id=source_id,
            date=current,
            start_time=start_time,
            end_time=end_time,
            status="programado",
        )
        db.add(ci)
        db.flush()
        created_ids.append(ci.id)

        current += timedelta(days=params.interval_days)
        type_index += 1

    db.commit()

    instances = db.query(CIModel).filter(CIModel.id.in_(created_ids)).order_by(CIModel.date).all()
    return {
        "created": len(created_ids),
        "instances": [
            {
                "id": ci.id, "type": ci.type, "source_id": ci.source_id,
                "date": str(ci.date), "start_time": _fmt_time(ci.start_time),
                "end_time": _fmt_time(ci.end_time), "notes": ci.notes,
                "status": ci.status, "coordinator": None, "co_coordinator": None,
            }
            for ci in instances
        ],
    }


@router.post("/bulk-count")
def bulk_count(filters: BulkDeleteFilters, db: Session = Depends(get_db)):
    where, bind = _build_bulk_where(filters)
    row = db.execute(text(f"SELECT COUNT(*) AS cnt FROM calendar_instances WHERE {where}"), bind).fetchone()
    return {"count": row.cnt if row else 0}


@router.post("/bulk-delete")
def bulk_delete(filters: BulkDeleteFilters, db: Session = Depends(get_db)):
    where, bind = _build_bulk_where(filters)
    result = db.execute(text(f"DELETE FROM calendar_instances WHERE {where}"), bind)
    db.commit()
    return {"deleted": result.rowcount}


def _build_bulk_where(filters: BulkDeleteFilters):
    if filters.scope == "month":
        return "YEAR(date) = :year AND MONTH(date) = :month", {"year": filters.year, "month": filters.month}
    elif filters.scope == "type":
        return "type = :type", {"type": filters.type}
    elif filters.scope == "series":
        if filters.source_id is not None:
            return "type = :type AND source_id = :source_id", {"type": filters.type, "source_id": filters.source_id}
        else:
            return "type = :type AND source_id IS NULL", {"type": filters.type}
    else:
        return "1=1", {}


# ── Calendar Event Participants ───────────────────────────────────────

@router.get("/instances/{event_id}/participants", response_model=List[CalendarEventParticipant])
def list_event_participants(event_id: int, db: Session = Depends(get_db)):
    return db.query(CEPModel).filter(CEPModel.event_id == event_id).all()


@router.post("/instances/{event_id}/participants", response_model=CalendarEventParticipant, status_code=201)
def add_event_participant(event_id: int, data: CalendarEventParticipantCreate, db: Session = Depends(get_db)):
    if not db.query(CIModel).filter(CIModel.id == event_id).first():
        raise HTTPException(status_code=404, detail="Instancia no encontrada")
    cep = CEPModel(**{**data.model_dump(), "event_id": event_id})
    db.add(cep)
    db.commit()
    db.refresh(cep)
    return cep


@router.put("/event-participants/{id}", response_model=CalendarEventParticipant)
def update_event_participant(id: int, data: CalendarEventParticipantUpdate, db: Session = Depends(get_db)):
    cep = db.query(CEPModel).filter(CEPModel.id == id).first()
    if not cep:
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(cep, key, value)
    db.commit()
    db.refresh(cep)
    return cep


@router.delete("/event-participants/{id}", status_code=204)
def delete_event_participant(id: int, db: Session = Depends(get_db)):
    cep = db.query(CEPModel).filter(CEPModel.id == id).first()
    if not cep:
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    db.delete(cep)
    db.commit()

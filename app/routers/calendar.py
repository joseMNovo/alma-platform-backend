import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text, bindparam
from typing import List, Optional
from datetime import date, datetime, timedelta

from app.database import get_db, SessionLocal
from app.models.calendar import (
    CalendarInstance as CIModel,
    CalendarAssignment as CAModel,
    CalendarEventParticipant as CEPModel,
)
from app.schemas.calendar import (
    CalendarInstance, CalendarInstanceCreate, CalendarInstanceUpdate,
    CalendarInstanceRich, VolunteerRef,
    CalendarAssignment, CalendarAssignmentCreate, CalendarAssignmentUpdate,
    AssignmentUpsertRequest, VolunteerListRequest,
    CalendarEventParticipant, CalendarEventParticipantCreate, CalendarEventParticipantUpdate,
    BulkDeleteFilters, GenerateCalendarParams,
)
from app.schemas.email_log import SendEmailRequest
from app.services import email_service
from app.services.notification_service import notify_user
from app.utils.logger import log_info, log_warn, log_error
from config import settings

router = APIRouter()

# Etiquetas legibles por tipo de evento (para el recordatorio manual).
_TYPE_LABELS = {"grupo": "el grupo de apoyo", "taller": "el taller", "actividad": "la actividad"}


def _when_label(event_date: date) -> str:
    """'hoy' / 'mañana' / 'en N días' según cuánto falta para el evento."""
    days = (event_date - date.today()).days
    if days <= 0:
        return "hoy"
    if days == 1:
        return "mañana"
    return f"en {days} días"


def _send_event_reminder_emails(event: dict, recipients: list[dict]) -> None:
    """Manda el email de recordatorio a cada involucrado. Corre en background con
    su propia sesión (la del request ya se cerró). Un fallo por destinatario no
    corta el resto."""
    db = SessionLocal()
    try:
        for r in recipients:
            if not r.get("email"):
                continue
            try:
                email_service.send_email(
                    db,
                    SendEmailRequest(
                        to=[r["email"]],
                        subject=f"Recordatorio: {event['label']} {event['when']} ({event['date_short']})",
                        template="event_reminder",
                        variables={
                            "name": r.get("name") or "",
                            "event_label": event["label"],
                            "event_date": event["date_full"],
                            "event_time": event["time"],
                            "when_label": event["when"],
                            "notes": event["notes"],
                            "app_url": settings.APP_BASE_URL,
                        },
                    ),
                )
            except Exception:
                log_error(
                    "Fallo al enviar recordatorio manual",
                    module="calendarios", action="notify_event_email",
                    meta={"email": r["email"], "event_id": event["id"]}, exc_info=True,
                )
    finally:
        db.close()


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
    """Instancias de calendario con coordinadores, co-coordinadores y lista de voluntarios (JOIN a voluntarios)."""
    sql = """
    SELECT
        ci.id, ci.type, ci.source_id, ci.title, ci.date, ci.start_time, ci.end_time, ci.notes, ci.status,
        ci.notify_enabled, ci.reminder_offsets, ci.created_by_volunteer_id,
        coord_v.id   AS coord_id,   coord_v.name   AS coord_name,   coord_v.last_name AS coord_last
    FROM calendar_instances ci
    LEFT JOIN calendar_assignments coord_ca
        ON coord_ca.instance_id = ci.id AND coord_ca.role = 'coordinator'
    LEFT JOIN voluntarios coord_v ON coord_v.id = coord_ca.volunteer_id
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
        # El co-coordinador ya no está en un JOIN (puede haber varios), así que
        # se busca con EXISTS igual que los voluntarios.
        sql += """ AND (
            coord_ca.volunteer_id = :vol_id
            OR EXISTS (
                SELECT 1 FROM calendar_assignments va
                WHERE va.instance_id = ci.id
                  AND va.role IN ('volunteer', 'co_coordinator')
                  AND va.volunteer_id = :vol_id
            )
        )"""
        params["vol_id"] = volunteer_id

    sql += " ORDER BY ci.date ASC, ci.start_time ASC"

    rows = db.execute(text(sql), params).fetchall()
    if not rows:
        return []

    # Lista de voluntarios (role='volunteer') de todas las instancias en una sola query
    instance_ids = [row.id for row in rows]
    vol_rows = db.execute(
        text(
            """
            SELECT ca.instance_id, v.id, v.name, v.last_name
            FROM calendar_assignments ca
            JOIN voluntarios v ON v.id = ca.volunteer_id
            WHERE ca.role = 'volunteer' AND ca.instance_id IN :ids
            ORDER BY v.name ASC
            """
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": instance_ids},
    ).fetchall()

    volunteers_by_instance: dict[int, list] = {}
    for vr in vol_rows:
        volunteers_by_instance.setdefault(vr.instance_id, []).append(
            {"id": vr.id, "name": vr.name, "last_name": vr.last_name or ""}
        )

    # Co-coordinadores: pueden ser VARIOS por evento, por eso van en su propia
    # consulta y no en un JOIN (un JOIN duplicaría la fila del evento).
    cocoord_rows = db.execute(
        text(
            """
            SELECT ca.instance_id, v.id, v.name, v.last_name
            FROM calendar_assignments ca
            JOIN voluntarios v ON v.id = ca.volunteer_id
            WHERE ca.role = 'co_coordinator' AND ca.instance_id IN :ids
            ORDER BY v.name ASC
            """
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": instance_ids},
    ).fetchall()

    cocoords_by_instance: dict[int, list] = {}
    for cr in cocoord_rows:
        cocoords_by_instance.setdefault(cr.instance_id, []).append(
            {"id": cr.id, "name": cr.name, "last_name": cr.last_name or ""}
        )

    # Conteo REAL de participantes anotados por evento (no cancelados). Reemplaza
    # al número manual de taller.enrolled / grupo.participants, que estaba podrido.
    count_rows = db.execute(
        text(
            """
            SELECT event_id, COUNT(*) AS cnt
            FROM calendar_event_participants
            WHERE status <> 'cancelado' AND event_id IN :ids
            GROUP BY event_id
            """
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": instance_ids},
    ).fetchall()
    participants_count = {r.event_id: int(r.cnt) for r in count_rows}

    def _parse_offsets(raw):
        if raw is None:
            return None
        if isinstance(raw, (list, tuple)):
            return list(raw)
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    return [
        {
            "id": row.id,
            "type": row.type,
            "source_id": row.source_id,
            "title": row.title,
            "date": str(row.date),
            "start_time": _fmt_time(row.start_time),
            "end_time": _fmt_time(row.end_time),
            "notes": row.notes,
            "status": row.status,
            "notify_enabled": bool(row.notify_enabled),
            "reminder_offsets": _parse_offsets(row.reminder_offsets),
            "created_by_volunteer_id": row.created_by_volunteer_id,
            "coordinator": {"id": row.coord_id, "name": row.coord_name, "last_name": row.coord_last or ""}
                if row.coord_id else None,
            # Lista completa. `co_coordinator` (singular) se mantiene con el
            # primero para no romper consumidores viejos; la UI usa la lista.
            "co_coordinators": cocoords_by_instance.get(row.id, []),
            "co_coordinator": (cocoords_by_instance.get(row.id) or [None])[0],
            "volunteers": volunteers_by_instance.get(row.id, []),
            "participants_count": participants_count.get(row.id, 0),
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
    try:
        ci = CIModel(**data.model_dump())
        db.add(ci)
        db.commit()
        db.refresh(ci)
        log_info("Evento de calendario creado", module="calendarios", action="create_event", meta={"id": ci.id, "type": ci.type, "date": str(ci.date)})
        return ci
    except Exception:
        log_error("Error al crear evento de calendario", module="calendarios", action="create_event", exc_info=True)
        raise


@router.put("/instances/{id}", response_model=CalendarInstance)
def update_instance(id: int, data: CalendarInstanceUpdate, db: Session = Depends(get_db)):
    ci = db.query(CIModel).filter(CIModel.id == id).first()
    if not ci:
        log_warn("Evento de calendario no encontrado para editar", module="calendarios", action="edit_event", meta={"id": id})
        raise HTTPException(status_code=404, detail="Instancia no encontrada")
    try:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(ci, key, value)
        db.commit()
        db.refresh(ci)
        log_info("Evento de calendario actualizado", module="calendarios", action="edit_event", meta={"id": id})
        return ci
    except Exception:
        log_error("Error al actualizar evento de calendario", module="calendarios", action="edit_event", meta={"id": id}, exc_info=True)
        raise


@router.delete("/instances/{id}", status_code=204)
def delete_instance(id: int, db: Session = Depends(get_db)):
    ci = db.query(CIModel).filter(CIModel.id == id).first()
    if not ci:
        log_warn("Evento de calendario no encontrado para eliminar", module="calendarios", action="delete_event", meta={"id": id})
        raise HTTPException(status_code=404, detail="Instancia no encontrada")
    try:
        db.delete(ci)
        db.commit()
        log_info("Evento de calendario eliminado", module="calendarios", action="delete_event", meta={"id": id})
    except Exception:
        log_error("Error al eliminar evento de calendario", module="calendarios", action="delete_event", meta={"id": id}, exc_info=True)
        raise


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


@router.put("/instances/{instance_id}/volunteers", response_model=List[CalendarAssignment])
def set_event_volunteers(
    instance_id: int, data: VolunteerListRequest, db: Session = Depends(get_db)
):
    """Reemplaza la lista completa de voluntarios (role='volunteer') de una instancia."""
    if not db.query(CIModel).filter(CIModel.id == instance_id).first():
        raise HTTPException(status_code=404, detail="Instancia no encontrada")

    # Borra los voluntarios actuales (no toca coordinator/co_coordinator) y reinserta sin duplicados
    db.query(CAModel).filter(
        CAModel.instance_id == instance_id, CAModel.role == "volunteer"
    ).delete(synchronize_session=False)

    seen: set[int] = set()
    for vol_id in data.volunteer_ids:
        if vol_id in seen:
            continue
        seen.add(vol_id)
        db.add(CAModel(instance_id=instance_id, volunteer_id=vol_id, role="volunteer"))

    db.commit()
    return db.query(CAModel).filter(
        CAModel.instance_id == instance_id, CAModel.role == "volunteer"
    ).all()


@router.put("/instances/{instance_id}/cocoordinators", response_model=List[CalendarAssignment])
def set_event_cocoordinators(
    instance_id: int, data: VolunteerListRequest, db: Session = Depends(get_db)
):
    """Reemplaza la lista completa de co-coordinadores de una instancia.

    Un evento puede tener VARIOS co-coordinadores (a diferencia del
    coordinador, que sigue siendo uno). Se resuelve como reemplazo total —
    igual que la lista de voluntarios — porque es atómico y no deja estados
    intermedios raros si la UI manda dos cambios seguidos.
    """
    if not db.query(CIModel).filter(CIModel.id == instance_id).first():
        raise HTTPException(status_code=404, detail="Instancia no encontrada")

    # Quién es el coordinador: no puede estar además como co-coordinador.
    coordinator = (
        db.query(CAModel.volunteer_id)
        .filter(CAModel.instance_id == instance_id, CAModel.role == "coordinator")
        .scalar()
    )

    db.query(CAModel).filter(
        CAModel.instance_id == instance_id, CAModel.role == "co_coordinator"
    ).delete(synchronize_session=False)

    seen: set[int] = set()
    for vol_id in data.volunteer_ids:
        if vol_id in seen or vol_id == coordinator:
            continue
        seen.add(vol_id)
        db.add(CAModel(instance_id=instance_id, volunteer_id=vol_id, role="co_coordinator"))

    db.commit()
    log_info(
        "Co-coordinadores actualizados",
        module="calendarios", action="set_cocoordinators",
        meta={"instance_id": instance_id, "cantidad": len(seen)},
    )
    return db.query(CAModel).filter(
        CAModel.instance_id == instance_id, CAModel.role == "co_coordinator"
    ).all()


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

    try:
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
        log_info("Calendario generado", module="calendarios", action="generate_calendar", meta={"created": len(created_ids), "start_date": params.start_date, "end_date": params.end_date})
    except Exception:
        log_error("Error al generar calendario", module="calendarios", action="generate_calendar", exc_info=True)
        raise

    instances = db.query(CIModel).filter(CIModel.id.in_(created_ids)).order_by(CIModel.date).all()
    return {
        "created": len(created_ids),
        "instances": [
            {
                "id": ci.id, "type": ci.type, "source_id": ci.source_id,
                "date": str(ci.date), "start_time": _fmt_time(ci.start_time),
                "end_time": _fmt_time(ci.end_time), "notes": ci.notes,
                "status": ci.status, "notify_enabled": False, "reminder_offsets": None,
                "coordinator": None, "co_coordinator": None, "volunteers": [],
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
    try:
        where, bind = _build_bulk_where(filters)
        result = db.execute(text(f"DELETE FROM calendar_instances WHERE {where}"), bind)
        db.commit()
        log_info("Eliminación masiva de calendario", module="calendarios", action="bulk_delete", meta={"deleted": result.rowcount, "scope": filters.scope})
        return {"deleted": result.rowcount}
    except Exception:
        log_error("Error en eliminación masiva de calendario", module="calendarios", action="bulk_delete", exc_info=True)
        raise


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

    # Idempotente: si ya está anotado a ESTE evento, se reactiva/devuelve en vez
    # de duplicar. Inscribirse dos veces al mismo encuentro es un no-op.
    existing = (
        db.query(CEPModel)
        .filter(CEPModel.event_id == event_id, CEPModel.participant_id == data.participant_id)
        .first()
    )
    if existing:
        if existing.status == "cancelado":
            existing.status = "inscripto"
            db.commit()
            db.refresh(existing)
        return existing

    cep = CEPModel(**{**data.model_dump(), "event_id": event_id})
    db.add(cep)
    db.commit()
    db.refresh(cep)
    return cep


@router.delete("/instances/{event_id}/participants/by-participant/{participant_id}", status_code=204)
def remove_event_participant(event_id: int, participant_id: int, db: Session = Depends(get_db)):
    """Desanota a un participante de un evento (lo usa el propio participante).

    Borra la fila directamente: un "me desanoto" no necesita conservar historia.
    Para cancelar dejando rastro, usar PUT event-participants/{id} status=cancelado.
    """
    cep = (
        db.query(CEPModel)
        .filter(CEPModel.event_id == event_id, CEPModel.participant_id == participant_id)
        .first()
    )
    if cep:
        db.delete(cep)
        db.commit()


@router.get("/participants/{participant_id}/event-ids", response_model=List[int])
def participant_event_ids(participant_id: int, db: Session = Depends(get_db)):
    """Ids de los eventos a los que este participante está anotado (no cancelado).
    Lo usa el calendario para marcar en qué encuentros ya se inscribió."""
    rows = (
        db.query(CEPModel.event_id)
        .filter(CEPModel.participant_id == participant_id, CEPModel.status != "cancelado")
        .all()
    )
    return [r[0] for r in rows]


@router.get("/inscripciones")
def list_inscripciones(
    type: Optional[str] = Query(None, description="grupo | taller | actividad"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Listado de anotados por evento, para la sub-pestaña 'Inscripciones' de
    Espacios. Cada fila = una persona anotada a un encuentro puntual.

    Es la vista que reemplaza al 'número de inscriptos' del programa: acá se ve
    QUIÉN va a CADA encuentro, que es lo que refleja la asistencia real.
    """
    # El nombre del encuentro NO vive en ci.title (casi siempre NULL): sale del
    # programa de origen (grupo/taller/actividad) según el tipo. Se resuelve con
    # joins condicionales + COALESCE, igual que lo muestra el calendario.
    sql = """
    SELECT
        cep.id, cep.status, cep.created_at,
        ci.id AS event_id, ci.type, ci.date, ci.start_time,
        COALESCE(ci.title, g.name, t.name, a.name) AS event_title,
        pp.name AS person_name, pp.last_name AS person_last, p.email AS person_email
    FROM calendar_event_participants cep
    JOIN calendar_instances ci ON ci.id = cep.event_id
    JOIN participants p ON p.id = cep.participant_id
    LEFT JOIN participant_profiles pp ON pp.participant_id = p.id
    LEFT JOIN grupos      g ON ci.type = 'grupo'     AND g.id = ci.source_id
    LEFT JOIN talleres    t ON ci.type = 'taller'    AND t.id = ci.source_id
    LEFT JOIN actividades a ON ci.type = 'actividad' AND a.id = ci.source_id
    WHERE cep.status <> 'cancelado'
    """
    params: dict = {}
    if type:
        sql += " AND ci.type = :type"
        params["type"] = type
    if date_from:
        sql += " AND ci.date >= :date_from"
        params["date_from"] = date_from
    if date_to:
        sql += " AND ci.date <= :date_to"
        params["date_to"] = date_to
    sql += " ORDER BY ci.date DESC, ci.start_time ASC, pp.last_name ASC"

    rows = db.execute(text(sql), params).fetchall()
    return [
        {
            "id": r.id,
            "status": r.status,
            "event_id": r.event_id,
            "type": r.type,
            "event_title": r.event_title,
            "event_date": str(r.date) if r.date else None,
            "start_time": _fmt_time(r.start_time),
            "person_name": f"{r.person_name or ''} {r.person_last or ''}".strip() or (r.person_email or "—"),
            "person_email": r.person_email,
            "enrolled_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


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


@router.post("/instances/{event_id}/notify")
def notify_event(event_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Recordatorio MANUAL de un evento (botón del calendario, solo staff).

    Es el respaldo por si el cron falla o si el admin quiere avisar cuando
    disponga. Manda a TODOS los involucrados: voluntarios asignados
    (coordinador, co-coordinadores, voluntarios) + participantes anotados.

    Campanita: sincrónica (rápida). Emails: en background (no bloquean la
    respuesta ni la tumban por timeout, como el broadcast).
    """
    ci = db.query(CIModel).filter(CIModel.id == event_id).first()
    if not ci:
        raise HTTPException(status_code=404, detail="Evento no encontrado")

    # Nombre del evento (del programa de origen, como el calendario).
    name_row = db.execute(
        text("""
            SELECT COALESCE(ci.title, g.name, t.name, a.name) AS event_name
            FROM calendar_instances ci
            LEFT JOIN grupos      g ON ci.type = 'grupo'     AND g.id = ci.source_id
            LEFT JOIN talleres    t ON ci.type = 'taller'    AND t.id = ci.source_id
            LEFT JOIN actividades a ON ci.type = 'actividad' AND a.id = ci.source_id
            WHERE ci.id = :id
        """),
        {"id": event_id},
    ).fetchone()
    event_name = (name_row.event_name if name_row else None) or _TYPE_LABELS.get(ci.type, "el evento")

    # Involucrados 1: voluntarios asignados (cualquier rol).
    vol_rows = db.execute(
        text("""
            SELECT DISTINCT v.id, v.name, v.last_name, v.email
            FROM calendar_assignments ca JOIN voluntarios v ON v.id = ca.volunteer_id
            WHERE ca.instance_id = :id
        """),
        {"id": event_id},
    ).fetchall()

    # Involucrados 2: participantes anotados (no cancelados).
    part_rows = db.execute(
        text("""
            SELECT DISTINCT p.id, pp.name, pp.last_name, p.email
            FROM calendar_event_participants cep
            JOIN participants p ON p.id = cep.participant_id
            LEFT JOIN participant_profiles pp ON pp.participant_id = p.id
            WHERE cep.event_id = :id AND cep.status <> 'cancelado'
        """),
        {"id": event_id},
    ).fetchall()

    recipients: list[dict] = []
    for r in vol_rows:
        recipients.append({"user_type": "voluntario", "user_id": r.id,
                           "name": f"{r.name or ''} {r.last_name or ''}".strip(), "email": r.email})
    for r in part_rows:
        recipients.append({"user_type": "participante", "user_id": r.id,
                           "name": f"{r.name or ''} {r.last_name or ''}".strip(), "email": r.email})

    if not recipients:
        return {"recipients": 0, "message": "Este evento no tiene voluntarios asignados ni participantes anotados."}

    when = _when_label(ci.date)
    event = {
        "id": event_id,
        "label": event_name,
        "when": when,
        "date_full": ci.date.strftime("%d/%m/%Y"),
        "date_short": ci.date.strftime("%d/%m"),
        "time": _fmt_time(ci.start_time)[:5],
        "notes": ci.notes or "",
    }

    # Campanita in-app (rápida). El push va adentro de notify_user, ya blindado.
    title = f"Recordatorio: {event_name}"
    body = f"Es {when} ({event['date_full']} a las {event['time']} hs)."
    for r in recipients:
        try:
            notify_user(db, r["user_type"], r["user_id"], title=title, body=body,
                        kind="calendar_reminder", url="/calendarios")
        except Exception:
            db.rollback()
            log_warn("No se pudo crear la campanita del recordatorio manual",
                     module="calendarios", action="notify_event",
                     meta={"event_id": event_id, "user": f"{r['user_type']}:{r['user_id']}"})

    # Emails en background: no bloquean la respuesta.
    background_tasks.add_task(_send_event_reminder_emails, event, recipients)

    log_info("Recordatorio manual disparado", module="calendarios", action="notify_event",
             meta={"event_id": event_id, "destinatarios": len(recipients)})
    return {"recipients": len(recipients)}

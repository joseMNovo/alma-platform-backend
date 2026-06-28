from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from typing import List, Optional

from app.database import get_db
from app.models.group_history import GroupHistory as HistoryModel, GroupHistoryAttendee as AttendeeModel
from app.models.grupo import Grupo  # noqa: F401  (asegura el registro del mapper)
from app.models.voluntario import Voluntario as VoluntarioModel
from app.models.participant import ParticipantProfile as ProfileModel
from app.schemas.group_history import (
    GroupHistoryCreate,
    GroupHistoryUpdate,
    GroupHistoryOut,
    GroupHistorySuggestion,
)
from app.utils.logger import log_info, log_warn, log_error

router = APIRouter()


def _full_name(v) -> Optional[str]:
    if v is None:
        return None
    return f"{v.name or ''} {v.last_name or ''}".strip() or None


def _valid_volunteer_id(db: Session, vid: Optional[int]) -> Optional[int]:
    """Devuelve el id si existe en voluntarios; si no (p. ej. admin env id=0), None."""
    if not vid:
        return None
    exists = db.query(VoluntarioModel.id).filter(VoluntarioModel.id == vid).first()
    return vid if exists else None


def _serialize_attendee(a: AttendeeModel) -> dict:
    return {
        "id": a.id,
        "person_profile_id": a.person_profile_id,
        "person_name": a.person_name,
        "person_age": a.person_age,
        "patient_name": a.patient_name,
        "patient_age": a.patient_age,
        "relationship": a.relationship,
        "problematica": a.problematica,
        "notes": a.notes,
        "created_at": a.created_at,
    }


def _serialize(h: HistoryModel) -> dict:
    attendees = list(h.attendees)
    return {
        "id": h.id,
        "group_id": h.group_id,
        "group_name": h.group_name or (h.group.name if h.group else None),
        "title": h.title,
        "session_date": h.session_date,
        "coordinator_volunteer_id": h.coordinator_volunteer_id,
        "coordinator_name": _full_name(h.coordinator),
        "summary": h.summary,
        "created_by_volunteer_id": h.created_by_volunteer_id,
        "created_by_name": _full_name(h.created_by),
        "attendee_count": len(attendees),
        "attendees": [_serialize_attendee(a) for a in attendees],
        "created_at": h.created_at,
        "updated_at": h.updated_at,
    }


@router.get("/", response_model=List[GroupHistoryOut])
def list_histories(
    group_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None, description="Búsqueda inteligente sobre encuentro y asistentes"),
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    query = db.query(HistoryModel)

    if group_id is not None:
        query = query.filter(HistoryModel.group_id == group_id)

    if q and q.strip():
        like = f"%{q.strip()}%"
        # Coincidencia parcial (LIKE) sobre asistentes → ids de historiales que matchean.
        attendee_ids = db.query(AttendeeModel.history_id).filter(
            or_(
                AttendeeModel.person_name.like(like),
                AttendeeModel.patient_name.like(like),
                AttendeeModel.relationship.like(like),
                AttendeeModel.problematica.like(like),
                AttendeeModel.notes.like(like),
            )
        )
        query = query.filter(
            or_(
                HistoryModel.title.like(like),
                HistoryModel.summary.like(like),
                HistoryModel.group_name.like(like),
                HistoryModel.id.in_(attendee_ids),
            )
        )

    histories = (
        query.order_by(HistoryModel.session_date.desc(), HistoryModel.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_serialize(h) for h in histories]


@router.get("/suggest", response_model=List[GroupHistorySuggestion])
def suggest_attendees(
    q: str = Query(..., min_length=2, description="Texto parcial del nombre del asistente"),
    limit: int = 8,
    db: Session = Depends(get_db),
):
    """Autocompletado sutil de nombre de asistente.

    Mezcla dos fuentes: personas del registro (participant_profiles, NUNCA
    voluntarios) y nombres de asistentes de otras fichas. Cada token del texto
    debe aparecer en el nombre (permite 'rica gó' → 'Ricardo Gómez').
    """
    tokens = [t for t in q.strip().split() if t]
    if not tokens:
        return []

    # ── Fuente 1: personas del registro (excluye voluntarios) ──────────────
    full_profile = func.concat(
        func.coalesce(ProfileModel.name, ""), " ", func.coalesce(ProfileModel.last_name, "")
    )
    profile_conds = [full_profile.like(f"%{t}%") for t in tokens]
    profiles = (
        db.query(ProfileModel)
        .filter(ProfileModel.is_volunteer == False, *profile_conds)  # noqa: E712
        .order_by(ProfileModel.name, ProfileModel.last_name)
        .limit(limit)
        .all()
    )

    suggestions: List[dict] = []
    seen: set[str] = set()
    for p in profiles:
        label = f"{p.name or ''} {p.last_name or ''}".strip()
        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        suggestions.append({"label": label, "source": "participante", "person_profile_id": p.id})

    # ── Fuente 2: asistentes de otras fichas (los que no son ya participante) ──
    attendee_conds = [AttendeeModel.person_name.like(f"%{t}%") for t in tokens]
    attendees = (
        db.query(AttendeeModel.person_name, AttendeeModel.person_profile_id)
        .filter(*attendee_conds)
        .limit(limit * 3)
        .all()
    )
    for name, profile_id in attendees:
        label = (name or "").strip()
        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        suggestions.append({"label": label, "source": "fichero", "person_profile_id": profile_id})

    return suggestions[:limit]


@router.get("/{id}", response_model=GroupHistoryOut)
def get_history(id: int, db: Session = Depends(get_db)):
    h = db.query(HistoryModel).filter(HistoryModel.id == id).first()
    if not h:
        raise HTTPException(status_code=404, detail="Historial no encontrado")
    return _serialize(h)


@router.post("/", response_model=GroupHistoryOut, status_code=201)
def create_history(data: GroupHistoryCreate, db: Session = Depends(get_db)):
    payload = data.model_dump()
    attendees = payload.pop("attendees", []) or []

    # Voluntarios inexistentes (admin env id=0) → NULL, para no violar la FK.
    payload["created_by_volunteer_id"] = _valid_volunteer_id(db, payload.get("created_by_volunteer_id"))
    payload["coordinator_volunteer_id"] = _valid_volunteer_id(db, payload.get("coordinator_volunteer_id"))

    # Snapshot del nombre del grupo si vino group_id y no un nombre explícito.
    if payload.get("group_id") and not payload.get("group_name"):
        grupo = db.query(Grupo).filter(Grupo.id == payload["group_id"]).first()
        if grupo:
            payload["group_name"] = grupo.name

    try:
        history = HistoryModel(**payload)
        for a in attendees:
            history.attendees.append(AttendeeModel(**a))
        db.add(history)
        db.commit()
        db.refresh(history)
        log_info("Historial de grupo creado", module="group_histories", action="create",
                 user=data.created_by_volunteer_id, meta={"id": history.id, "attendees": len(attendees)})
        return _serialize(history)
    except Exception:
        db.rollback()
        log_error("Error al crear historial de grupo", module="group_histories", action="create", exc_info=True)
        raise


@router.put("/{id}", response_model=GroupHistoryOut)
def update_history(id: int, data: GroupHistoryUpdate, db: Session = Depends(get_db)):
    history = db.query(HistoryModel).filter(HistoryModel.id == id).first()
    if not history:
        log_warn("Historial no encontrado para editar", module="group_histories", action="update", meta={"id": id})
        raise HTTPException(status_code=404, detail="Historial no encontrado")

    payload = data.model_dump(exclude_unset=True)
    attendees = payload.pop("attendees", None)

    # Voluntario coordinador inexistente (admin env id=0) → NULL.
    if "coordinator_volunteer_id" in payload:
        payload["coordinator_volunteer_id"] = _valid_volunteer_id(db, payload.get("coordinator_volunteer_id"))

    if payload.get("group_id") and "group_name" not in payload:
        grupo = db.query(Grupo).filter(Grupo.id == payload["group_id"]).first()
        if grupo:
            payload["group_name"] = grupo.name

    try:
        for key, value in payload.items():
            setattr(history, key, value)

        # Si se mandaron asistentes, se reemplaza la lista completa.
        if attendees is not None:
            history.attendees.clear()
            db.flush()
            for a in attendees:
                history.attendees.append(AttendeeModel(**a))

        db.commit()
        db.refresh(history)
        log_info("Historial de grupo actualizado", module="group_histories", action="update", meta={"id": id})
        return _serialize(history)
    except Exception:
        db.rollback()
        log_error("Error al actualizar historial de grupo", module="group_histories", action="update", meta={"id": id}, exc_info=True)
        raise


@router.delete("/{id}", status_code=204)
def delete_history(id: int, db: Session = Depends(get_db)):
    history = db.query(HistoryModel).filter(HistoryModel.id == id).first()
    if not history:
        log_warn("Historial no encontrado para eliminar", module="group_histories", action="delete", meta={"id": id})
        raise HTTPException(status_code=404, detail="Historial no encontrado")
    try:
        db.delete(history)
        db.commit()
        log_info("Historial de grupo eliminado", module="group_histories", action="delete", meta={"id": id})
    except Exception:
        db.rollback()
        log_error("Error al eliminar historial de grupo", module="group_histories", action="delete", meta={"id": id}, exc_info=True)
        raise

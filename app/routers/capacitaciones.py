"""
app/routers/capacitaciones.py — ALMA Backend — Capacitaciones en video
=======================================================================
Regla de oro del módulo: `video_ref` NUNCA sale si quien pregunta no tiene
acceso. Los ítems bloqueados viajan con locked=True y el campo en None, así
el catálogo se puede mostrar entero sin filtrar el contenido.

Staff (admin/voluntario) ve todo sin habilitación. Los participantes
necesitan una habilitación vigente, salvo que la capacitación esté en
access_mode='abierta' (contenido gratuito).
"""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.training import Training, TrainingItem, TrainingItemProgress, TrainingItemView
from app.models.access import PersonAccessGrant, PersonPayment
from app.models.participant import ParticipantProfile
from app.schemas.training import (
    TrainingCreate, TrainingUpdate, TrainingOut,
    TrainingItemCreate, TrainingItemUpdate, TrainingItemOut,
    ItemReorder, ProgressUpdate, ProgressOut, ViewCreate,
    SharedAccountAlert, VideoCheckRequest, VideoCheckResult,
    slugify,
)
from app.services import access_service, youtube
from app.utils.logger import log_info, log_warn, log_error

router = APIRouter()

MODULE_KEY = "capacitaciones"
# Porcentaje de reproducción real a partir del cual un video se da por visto.
COMPLETION_RATIO = 0.9


# ── Helpers ────────────────────────────────────────────────────────────

def _unique_slug(db: Session, base: str, exclude_id: Optional[int] = None) -> str:
    """Slug único. Si el título se repite, agrega -2, -3, etc."""
    slug = slugify(base)
    candidate = slug
    n = 1
    while True:
        q = db.query(Training.id).filter(Training.slug == candidate)
        if exclude_id:
            q = q.filter(Training.id != exclude_id)
        if not q.first():
            return candidate
        n += 1
        candidate = f"{slug}-{n}"[:160]


def _progress_map(db: Session, person_id: Optional[int], training_id: int) -> dict:
    if not person_id:
        return {}
    rows = (
        db.query(TrainingItemProgress)
        .join(TrainingItem, TrainingItem.id == TrainingItemProgress.training_item_id)
        .filter(
            TrainingItemProgress.person_id == person_id,
            TrainingItem.training_id == training_id,
        )
        .all()
    )
    return {r.training_item_id: r for r in rows}


def _serialize_item(item: TrainingItem, has_access: bool, progress: Optional[TrainingItemProgress]) -> dict:
    """Serializa un ítem. Sin acceso, el contenido real se vacía: esta función
    es la frontera de seguridad, no la UI."""
    locked = not has_access
    return {
        "id": item.id,
        "training_id": item.training_id,
        "kind": item.kind,
        "title": item.title,
        "description": item.description,
        "provider": item.provider,
        "video_ref": None if locked else item.video_ref,
        "body": None if locked else item.body,
        "file_url": None if locked else item.file_url,
        "duration_minutes": item.duration_minutes,
        "sort_order": item.sort_order,
        "is_published": item.is_published,
        "locked": locked,
        "last_position_sec": progress.last_position_sec if progress else None,
        "watched_sec": progress.watched_sec if progress else None,
        "completed": bool(progress and progress.completed_at),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _serialize_training(
    training: Training,
    *,
    has_access: bool = False,
    expires_at: Optional[datetime] = None,
    progress: Optional[dict] = None,
    include_items: bool = False,
    only_published_items: bool = True,
) -> dict:
    progress = progress or {}
    items = [i for i in training.items if i.is_published or not only_published_items]

    return {
        "id": training.id,
        "title": training.title,
        "slug": training.slug,
        "description": training.description,
        "cover_file_guid": training.cover_file_guid,
        "price": training.price,
        "currency": training.currency,
        "status": training.status,
        "access_mode": training.access_mode,
        "default_access_days": training.default_access_days,
        "category": training.category,
        "available_from": training.available_from,
        "available_until": training.available_until,
        "sort_order": training.sort_order,
        "created_by_volunteer_id": training.created_by_volunteer_id,
        "item_count": len(items),
        "has_access": has_access,
        "access_expires_at": expires_at,
        "completed_items": sum(1 for i in items if progress.get(i.id) and progress[i.id].completed_at),
        "items": [_serialize_item(i, has_access, progress.get(i.id)) for i in items] if include_items else [],
        "created_at": training.created_at,
        "updated_at": training.updated_at,
    }


def _access_for(db: Session, training: Training, user_type: Optional[str], user_id: Optional[int]):
    """(tiene_acceso, vence_el, person_id) para el usuario que consulta."""
    if not user_type:
        return False, None, None

    person_id = access_service.resolve_person_id(db, user_type, user_id or 0)

    if user_type in access_service.STAFF_ROLES or training.access_mode == "abierta":
        return True, None, person_id

    if not person_id:
        return False, None, None

    grant = (
        db.query(PersonAccessGrant)
        .filter(
            PersonAccessGrant.person_id == person_id,
            PersonAccessGrant.module_key == MODULE_KEY,
            PersonAccessGrant.resource_id == training.id,
            *access_service.live_filter(),
        )
        .first()
    )
    return (bool(grant), grant.expires_at if grant else None, person_id)


def _get_or_404(training_id: int, db: Session) -> Training:
    training = db.query(Training).filter(Training.id == training_id).first()
    if not training:
        raise HTTPException(status_code=404, detail="Capacitación no encontrada")
    return training


# ── Capacitaciones ─────────────────────────────────────────────────────

@router.get("/", response_model=List[TrainingOut])
def list_trainings(
    status: Optional[str] = Query(None),
    user_type: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    include_items: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Catálogo. Con user_type/user_id viene marcado qué tiene habilitado."""
    q = db.query(Training)
    if status:
        q = q.filter(Training.status == status)

    trainings = q.order_by(Training.sort_order, Training.title).all()

    result = []
    for t in trainings:
        has_access, expires_at, person_id = _access_for(db, t, user_type, user_id)
        result.append(
            _serialize_training(
                t,
                has_access=has_access,
                expires_at=expires_at,
                progress=_progress_map(db, person_id, t.id) if include_items else None,
                include_items=include_items,
            )
        )
    return result


@router.get("/mis", response_model=List[TrainingOut])
def my_trainings(
    user_type: str = Query(...),
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Lo que el usuario logueado puede ver, con su contenido y su progreso."""
    trainings = (
        db.query(Training)
        .filter(Training.status == "publicada")
        .order_by(Training.sort_order, Training.title)
        .all()
    )

    result = []
    for t in trainings:
        has_access, expires_at, person_id = _access_for(db, t, user_type, user_id)
        result.append(
            _serialize_training(
                t,
                has_access=has_access,
                expires_at=expires_at,
                progress=_progress_map(db, person_id, t.id),
                include_items=True,
            )
        )
    return result


@router.get("/publica/{slug}", response_model=TrainingOut)
def public_landing(slug: str, db: Session = Depends(get_db)):
    """Landing pública (sin sesión): título, descripción, precio y TEMARIO.

    Nunca devuelve contenido: los ítems vienen todos con locked=True.
    """
    training = db.query(Training).filter(Training.slug == slug, Training.status == "publicada").first()
    if not training:
        raise HTTPException(status_code=404, detail="Capacitación no encontrada")
    return _serialize_training(training, has_access=False, include_items=True)


@router.get("/{training_id}", response_model=TrainingOut)
def get_training(
    training_id: int,
    user_type: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    include_unpublished: bool = Query(False),
    db: Session = Depends(get_db),
):
    training = _get_or_404(training_id, db)
    has_access, expires_at, person_id = _access_for(db, training, user_type, user_id)
    return _serialize_training(
        training,
        has_access=has_access,
        expires_at=expires_at,
        progress=_progress_map(db, person_id, training.id),
        include_items=True,
        only_published_items=not include_unpublished,
    )


@router.post("/", response_model=TrainingOut, status_code=201)
def create_training(data: TrainingCreate, db: Session = Depends(get_db)):
    payload = data.model_dump(exclude={"slug"})
    try:
        training = Training(**payload, slug=_unique_slug(db, data.slug or data.title))
        db.add(training)
        db.commit()
        db.refresh(training)
        log_info(
            "Capacitación creada",
            module="capacitaciones", action="create",
            user=data.created_by_volunteer_id,
            meta={"id": training.id, "title": training.title},
        )
        return _serialize_training(training, has_access=True, include_items=True)
    except Exception:
        db.rollback()
        log_error("Error al crear capacitación", module="capacitaciones", action="create", exc_info=True)
        raise


@router.put("/{training_id}", response_model=TrainingOut)
def update_training(training_id: int, data: TrainingUpdate, db: Session = Depends(get_db)):
    training = _get_or_404(training_id, db)
    payload = data.model_dump(exclude_unset=True)

    if payload.get("slug"):
        payload["slug"] = _unique_slug(db, payload["slug"], exclude_id=training_id)

    try:
        for key, value in payload.items():
            setattr(training, key, value)
        db.commit()
        db.refresh(training)
        log_info("Capacitación actualizada", module="capacitaciones", action="edit",
                 meta={"id": training_id, "campos": list(payload)})
        return _serialize_training(training, has_access=True, include_items=True, only_published_items=False)
    except Exception:
        db.rollback()
        log_error("Error al actualizar capacitación", module="capacitaciones", action="edit",
                  meta={"id": training_id}, exc_info=True)
        raise


@router.delete("/{training_id}", status_code=204)
def delete_training(training_id: int, db: Session = Depends(get_db)):
    """Borra la capacitación y su contenido (cascade). Las habilitaciones y los
    pagos quedan: son registros de algo que efectivamente pasó."""
    training = _get_or_404(training_id, db)
    try:
        db.delete(training)
        db.commit()
        log_info("Capacitación eliminada", module="capacitaciones", action="delete", meta={"id": training_id})
    except Exception:
        db.rollback()
        log_error("Error al eliminar capacitación", module="capacitaciones", action="delete",
                  meta={"id": training_id}, exc_info=True)
        raise


# ── Contenido ──────────────────────────────────────────────────────────

@router.post("/check-video", response_model=VideoCheckResult)
def check_video(data: VideoCheckRequest):
    """Valida el link de YouTube ANTES de guardarlo y trae el título.

    Detecta el error más probable del módulo: subir el video como "Privado".
    Los privados no se pueden embeber y el alumno vería un cuadro negro.
    """
    video_id = youtube.extract_video_id(data.url)
    if not video_id:
        return VideoCheckResult(ok=False, message="No se reconoce el link de YouTube")

    ok, info = youtube.check_embeddable(video_id)
    if not ok:
        return VideoCheckResult(
            ok=False,
            video_id=video_id,
            message="El video no se puede insertar. Si lo subiste como «Privado», cambialo a «No listado».",
        )

    return VideoCheckResult(
        ok=True,
        video_id=video_id,
        title=(info or {}).get("title"),
        author_name=(info or {}).get("author_name"),
        thumbnail_url=(info or {}).get("thumbnail_url"),
    )


@router.post("/{training_id}/items", response_model=TrainingItemOut, status_code=201)
def create_item(training_id: int, data: TrainingItemCreate, db: Session = Depends(get_db)):
    _get_or_404(training_id, db)
    payload = data.model_dump()

    # Se guarda SIEMPRE el ID pelado, aunque el admin pegue la URL completa.
    if payload.get("kind") == "video" and payload.get("video_ref"):
        video_id = youtube.extract_video_id(payload["video_ref"])
        if not video_id:
            raise HTTPException(status_code=422, detail="El link de YouTube no es válido")
        payload["video_ref"] = video_id

    if not payload.get("sort_order"):
        last = (
            db.query(func.max(TrainingItem.sort_order))
            .filter(TrainingItem.training_id == training_id)
            .scalar()
        )
        payload["sort_order"] = (last or 0) + 1

    try:
        item = TrainingItem(**payload, training_id=training_id)
        db.add(item)
        db.commit()
        db.refresh(item)
        log_info("Contenido agregado", module="capacitaciones", action="create_item",
                 meta={"training_id": training_id, "item_id": item.id, "kind": item.kind})
        return _serialize_item(item, has_access=True, progress=None)
    except Exception:
        db.rollback()
        log_error("Error al agregar contenido", module="capacitaciones", action="create_item",
                  meta={"training_id": training_id}, exc_info=True)
        raise


@router.put("/items/{item_id}", response_model=TrainingItemOut)
def update_item(item_id: int, data: TrainingItemUpdate, db: Session = Depends(get_db)):
    item = db.query(TrainingItem).filter(TrainingItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Contenido no encontrado")

    payload = data.model_dump(exclude_unset=True)
    if payload.get("video_ref"):
        video_id = youtube.extract_video_id(payload["video_ref"])
        if not video_id:
            raise HTTPException(status_code=422, detail="El link de YouTube no es válido")
        payload["video_ref"] = video_id

    try:
        for key, value in payload.items():
            setattr(item, key, value)
        db.commit()
        db.refresh(item)
        log_info("Contenido actualizado", module="capacitaciones", action="edit_item", meta={"item_id": item_id})
        return _serialize_item(item, has_access=True, progress=None)
    except Exception:
        db.rollback()
        log_error("Error al actualizar contenido", module="capacitaciones", action="edit_item",
                  meta={"item_id": item_id}, exc_info=True)
        raise


@router.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(TrainingItem).filter(TrainingItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Contenido no encontrado")
    try:
        db.delete(item)
        db.commit()
        log_info("Contenido eliminado", module="capacitaciones", action="delete_item", meta={"item_id": item_id})
    except Exception:
        db.rollback()
        log_error("Error al eliminar contenido", module="capacitaciones", action="delete_item",
                  meta={"item_id": item_id}, exc_info=True)
        raise


@router.put("/{training_id}/items/reorder", response_model=List[TrainingItemOut])
def reorder_items(training_id: int, data: ItemReorder, db: Session = Depends(get_db)):
    _get_or_404(training_id, db)
    items = db.query(TrainingItem).filter(TrainingItem.training_id == training_id).all()
    by_id = {i.id: i for i in items}

    try:
        for position, item_id in enumerate(data.order, start=1):
            if item_id in by_id:
                by_id[item_id].sort_order = position
        db.commit()
        log_info("Contenido reordenado", module="capacitaciones", action="reorder",
                 meta={"training_id": training_id})
        ordered = sorted(items, key=lambda i: i.sort_order)
        return [_serialize_item(i, has_access=True, progress=None) for i in ordered]
    except Exception:
        db.rollback()
        log_error("Error al reordenar contenido", module="capacitaciones", action="reorder",
                  meta={"training_id": training_id}, exc_info=True)
        raise


# ── Progreso y trazabilidad ────────────────────────────────────────────

@router.post("/items/{item_id}/progress", response_model=ProgressOut)
def save_progress(item_id: int, data: ProgressUpdate, db: Session = Depends(get_db)):
    """Ping del player: guarda dónde quedó y acumula lo REALMENTE reproducido."""
    item = db.query(TrainingItem).filter(TrainingItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Contenido no encontrado")

    person_id = access_service.resolve_person_id(db, data.user_type, data.user_id)
    if not person_id:
        # Staff sin ficha espejo: no se registra progreso, pero no es un error.
        return ProgressOut(training_item_id=item_id, last_position_sec=data.last_position_sec)

    progress = (
        db.query(TrainingItemProgress)
        .filter(
            TrainingItemProgress.person_id == person_id,
            TrainingItemProgress.training_item_id == item_id,
        )
        .first()
    )

    if not progress:
        progress = TrainingItemProgress(person_id=person_id, training_item_id=item_id)
        db.add(progress)

    progress.last_position_sec = max(0, data.last_position_sec)
    # Se acumulan los deltas del player: arrastrar la barra al final no suma
    # segundos, así "completado" significa que lo vio de verdad.
    progress.watched_sec = (progress.watched_sec or 0) + max(0, data.watched_delta)

    total_seconds = (item.duration_minutes or 0) * 60
    reached_end = total_seconds > 0 and progress.watched_sec >= total_seconds * COMPLETION_RATIO

    if (data.completed or reached_end) and not progress.completed_at:
        progress.completed_at = datetime.now()

    try:
        db.commit()
        db.refresh(progress)
    except Exception:
        db.rollback()
        log_error("Error al guardar progreso", module="capacitaciones", action="progress",
                  meta={"item_id": item_id}, exc_info=True)
        raise

    return ProgressOut(
        training_item_id=item_id,
        last_position_sec=progress.last_position_sec,
        watched_sec=progress.watched_sec,
        completed_at=progress.completed_at,
    )


@router.post("/items/{item_id}/view", status_code=201)
def log_view(item_id: int, data: ViewCreate, db: Session = Depends(get_db)):
    """Registra una reproducción. Es lo que le da dientes al watermark y lo
    que permite detectar cuentas compartidas."""
    if not db.query(TrainingItem.id).filter(TrainingItem.id == item_id).first():
        raise HTTPException(status_code=404, detail="Contenido no encontrado")

    person_id = access_service.resolve_person_id(db, data.user_type, data.user_id)
    try:
        db.add(
            TrainingItemView(
                training_item_id=item_id,
                person_id=person_id,
                user_type=data.user_type,
                user_id=data.user_id,
                ip=(data.ip or "")[:45] or None,
                user_agent=(data.user_agent or "")[:255] or None,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        log_warn("No se pudo registrar la reproducción", module="capacitaciones", action="view",
                 meta={"item_id": item_id})
    return {"success": True}


@router.get("/alertas/cuentas-compartidas", response_model=List[SharedAccountAlert])
def shared_account_alerts(
    days: int = Query(7, ge=1, le=90),
    min_ips: int = Query(4, ge=2),
    db: Session = Depends(get_db),
):
    """Personas que reprodujeron desde muchas IPs distintas.

    SOLO informativo. Nunca revocar automáticamente por esto: las IPs móviles
    cambian todo el tiempo y le cortaríamos el acceso a alguien que pagó.
    La decisión la toma una persona mirando el caso.
    """
    since = datetime.now() - timedelta(days=days)

    rows = (
        db.query(
            TrainingItemView.person_id,
            func.count(func.distinct(TrainingItemView.ip)).label("ips"),
            func.count(TrainingItemView.id).label("views"),
        )
        .filter(TrainingItemView.viewed_at > since, TrainingItemView.person_id.isnot(None))
        .group_by(TrainingItemView.person_id)
        .having(func.count(func.distinct(TrainingItemView.ip)) >= min_ips)
        .order_by(func.count(func.distinct(TrainingItemView.ip)).desc())
        .all()
    )

    alerts = []
    for person_id, ips, views in rows:
        person = db.query(ParticipantProfile).filter(ParticipantProfile.id == person_id).first()
        alerts.append(
            SharedAccountAlert(
                person_id=person_id,
                person_name=f"{person.name or ''} {person.last_name or ''}".strip() if person else None,
                person_email=person.email if person else None,
                distinct_ips=ips,
                views=views,
            )
        )
    return alerts


@router.get("/{training_id}/resumen")
def training_summary(training_id: int, db: Session = Depends(get_db)):
    """Alumnos, recaudación y avance de una capacitación."""
    training = _get_or_404(training_id, db)

    students = (
        db.query(func.count(PersonAccessGrant.id))
        .filter(
            PersonAccessGrant.module_key == MODULE_KEY,
            PersonAccessGrant.resource_id == training_id,
            *access_service.live_filter(),
        )
        .scalar()
    ) or 0

    collected = (
        db.query(func.coalesce(func.sum(PersonPayment.amount), 0))
        .filter(PersonPayment.concept_type == "capacitacion", PersonPayment.concept_id == training_id)
        .scalar()
    ) or 0

    item_ids = [i.id for i in training.items]
    completed = 0
    if item_ids:
        completed = (
            db.query(func.count(TrainingItemProgress.id))
            .filter(
                TrainingItemProgress.training_item_id.in_(item_ids),
                TrainingItemProgress.completed_at.isnot(None),
            )
            .scalar()
        ) or 0

    return {
        "training_id": training_id,
        "title": training.title,
        "students": students,
        "collected": float(collected),
        "currency": training.currency,
        "items": len(item_ids),
        "completed_items_total": completed,
    }

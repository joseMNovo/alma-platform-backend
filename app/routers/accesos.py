"""
app/routers/accesos.py — ALMA Backend — Switchboard de habilitaciones
======================================================================
El ABM de "qué ve cada persona". Genérico por module_key: hoy lo usa
capacitaciones, mañana cualquier módulo, sin tocar este archivo.

Habilitar + registrar el pago ocurre en UNA transacción: o quedan los dos,
o no queda ninguno. Nunca un acceso sin su pago (ni al revés) por una
excepción a mitad de camino.
"""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.access import PersonAccessGrant, PersonPayment, AccessAudit
from app.models.participant import ParticipantProfile
from app.models.training import Training
from app.models.voluntario import Voluntario
from app.schemas.access import (
    GrantCreate, GrantBulkCreate, GrantOut, GrantRevoke,
    PaymentCreate, PaymentOut, MatrixRow, AccessAuditOut, MyAccess,
)
from app.services import access_service
from app.services.notification_service import notify_user
from app.utils.logger import log_info, log_warn, log_error

router = APIRouter()


def _notify_granted(db: Session, person: ParticipantProfile, module_key: str, resource_id: int) -> None:
    """Avisa a la persona que ya tiene acceso (campanita + push).

    Solo tiene sentido si la persona tiene login: si todavía no se registró,
    no hay a quién notificar. Nunca hace fallar la habilitación: el aviso es
    un extra, no parte de la operación.
    """
    if not person.participant_id:
        return

    title = "Ya tenés acceso"
    # "Formación" es el grupo del nav; "Capacitaciones" es la pestaña adentro.
    body = "Entrá a Formación → Capacitaciones para ver el contenido."
    url = "/capacitaciones"

    if module_key == "capacitaciones" and resource_id:
        training = db.query(Training).filter(Training.id == resource_id).first()
        if training:
            title = f"Ya tenés acceso a {training.title}"
            body = "Entrá a Formación → Capacitaciones para empezar."

    try:
        notify_user(
            db,
            user_type="participante",
            user_id=person.participant_id,
            title=title,
            body=body,
            kind="system",
            url=url,
        )
    except Exception:
        log_warn(
            "No se pudo notificar la habilitación",
            module="accesos",
            action="notify_failed",
            meta={"person_id": person.id, "module_key": module_key},
        )


# ── Helpers ────────────────────────────────────────────────────────────

def _is_live(grant: PersonAccessGrant) -> bool:
    if not grant.is_active or grant.revoked_at:
        return False
    return grant.expires_at is None or grant.expires_at > datetime.now()


def _resource_titles(db: Session, module_key: str) -> dict:
    """Nombre legible de cada recurso, para no mostrar 'recurso 7' en la UI."""
    if module_key != "capacitaciones":
        return {}
    return {t.id: t.title for t in db.query(Training).all()}


def _serialize_grant(
    grant: PersonAccessGrant,
    person: Optional[ParticipantProfile] = None,
    titles: Optional[dict] = None,
) -> dict:
    titles = titles or {}
    return {
        "id": grant.id,
        "person_id": grant.person_id,
        "module_key": grant.module_key,
        "resource_id": grant.resource_id,
        "is_active": grant.is_active,
        "granted_by_volunteer_id": grant.granted_by_volunteer_id,
        "granted_at": grant.granted_at,
        "expires_at": grant.expires_at,
        "revoked_at": grant.revoked_at,
        "notes": grant.notes,
        "is_live": _is_live(grant),
        "person_name": f"{person.name or ''} {person.last_name or ''}".strip() if person else None,
        "person_email": person.email if person else None,
        "resource_title": titles.get(grant.resource_id),
    }


def _person_or_404(person_id: int, db: Session) -> ParticipantProfile:
    person = db.query(ParticipantProfile).filter(ParticipantProfile.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    return person


# ── Consulta ───────────────────────────────────────────────────────────

@router.get("/mis", response_model=MyAccess)
def my_access(
    user_type: str = Query(...),
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Habilitaciones vigentes del usuario logueado.

    El frontend las usa SOLO para pintar la UI (qué pestañas mostrar). Cada
    endpoint vuelve a verificar del lado del servidor: el cliente nunca es
    la fuente de verdad.
    """
    person_id = access_service.resolve_person_id(db, user_type, user_id)
    if not person_id:
        return MyAccess(person_id=None, grants=[])

    grants = access_service.active_grants(db, person_id)
    return MyAccess(
        person_id=person_id,
        grants=[GrantOut(**_serialize_grant(g)) for g in grants],
    )


@router.get("/persona/{person_id}", response_model=List[GrantOut])
def person_grants(person_id: int, db: Session = Depends(get_db)):
    """Todas las habilitaciones de una persona (incluidas vencidas y revocadas)."""
    person = _person_or_404(person_id, db)
    grants = access_service.all_grants(db, person_id)
    titles = _resource_titles(db, "capacitaciones")
    return [GrantOut(**_serialize_grant(g, person, titles)) for g in grants]


@router.get("/recurso", response_model=List[GrantOut])
def resource_grants(
    module_key: str = Query(...),
    resource_id: int = Query(0),
    only_live: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Quiénes tienen acceso a un recurso. Es la lista de alumnos."""
    q = db.query(PersonAccessGrant).filter(
        PersonAccessGrant.module_key == module_key,
        PersonAccessGrant.resource_id == resource_id,
    )
    if only_live:
        q = q.filter(*access_service.live_filter())

    grants = q.order_by(PersonAccessGrant.granted_at.desc()).all()
    titles = _resource_titles(db, module_key)

    result = []
    for g in grants:
        person = db.query(ParticipantProfile).filter(ParticipantProfile.id == g.person_id).first()
        result.append(GrantOut(**_serialize_grant(g, person, titles)))
    return result


@router.get("/matriz", response_model=List[MatrixRow])
def access_matrix(
    module_key: str = Query(...),
    search: Optional[str] = Query(None),
    only_with_login: bool = Query(False),
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """Matriz personas × recursos: la vista principal del ABM de accesos."""
    q = db.query(ParticipantProfile)

    if search:
        term = f"%{search.strip()}%"
        q = q.filter(
            (ParticipantProfile.name.like(term))
            | (ParticipantProfile.last_name.like(term))
            | (ParticipantProfile.email.like(term))
        )
    if only_with_login:
        q = q.filter(ParticipantProfile.participant_id.isnot(None))

    people = (
        q.order_by(ParticipantProfile.last_name, ParticipantProfile.name)
        .offset(skip)
        .limit(limit)
        .all()
    )
    if not people:
        return []

    person_ids = [p.id for p in people]

    grants = (
        db.query(PersonAccessGrant)
        .filter(
            PersonAccessGrant.person_id.in_(person_ids),
            PersonAccessGrant.module_key == module_key,
        )
        .all()
    )
    grants_by_person: dict = {}
    for g in grants:
        grants_by_person.setdefault(g.person_id, {})[str(g.resource_id)] = _is_live(g)

    paid_rows = (
        db.query(PersonPayment.person_id, func.coalesce(func.sum(PersonPayment.amount), 0))
        .filter(PersonPayment.person_id.in_(person_ids))
        .group_by(PersonPayment.person_id)
        .all()
    )
    paid_by_person = {pid: total for pid, total in paid_rows}

    return [
        MatrixRow(
            person_id=p.id,
            name=p.name,
            last_name=p.last_name,
            email=p.email,
            has_login=p.participant_id is not None,
            is_volunteer=bool(p.is_volunteer),
            grants=grants_by_person.get(p.id, {}),
            total_paid=paid_by_person.get(p.id, Decimal("0")),
        )
        for p in people
    ]


@router.get("/auditoria", response_model=List[AccessAuditOut])
def audit_log(
    person_id: Optional[int] = Query(None),
    module_key: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
):
    """Historia de habilitaciones. Append-only: acá se ve quién hizo qué."""
    q = db.query(AccessAudit)
    if person_id:
        q = q.filter(AccessAudit.person_id == person_id)
    if module_key:
        q = q.filter(AccessAudit.module_key == module_key)

    rows = q.order_by(AccessAudit.created_at.desc()).limit(limit).all()

    names = {v.id: f"{v.name or ''} {v.last_name or ''}".strip() for v in db.query(Voluntario).all()}

    # Nombre de persona y título de la capacitación: sin esto la auditoría
    # queda en "persona #36 · recurso #1", ilegible para el admin.
    person_ids = {r.person_id for r in rows}
    person_names = {
        p.id: f"{p.name or ''} {p.last_name or ''}".strip()
        for p in db.query(ParticipantProfile).filter(ParticipantProfile.id.in_(person_ids)).all()
    } if person_ids else {}

    resource_ids = {r.resource_id for r in rows if r.resource_id > 0}
    training_titles = {
        t.id: t.title
        for t in db.query(Training).filter(Training.id.in_(resource_ids)).all()
    } if resource_ids else {}

    return [
        AccessAuditOut(
            id=r.id,
            grant_id=r.grant_id,
            person_id=r.person_id,
            module_key=r.module_key,
            resource_id=r.resource_id,
            action=r.action,
            actor_type=r.actor_type,
            actor_id=r.actor_id,
            detail=r.detail,
            created_at=r.created_at,
            actor_name=names.get(r.actor_id) or ("Administrador" if r.actor_type == "admin" else None),
            person_name=person_names.get(r.person_id) or None,
            resource_label=training_titles.get(r.resource_id) if r.resource_id > 0 else None,
        )
        for r in rows
    ]


# ── Alta y baja ────────────────────────────────────────────────────────

@router.post("/", response_model=GrantOut, status_code=201)
def create_grant(data: GrantCreate, db: Session = Depends(get_db)):
    """Habilita a una persona y, si viene, registra el pago en la MISMA transacción."""
    person = _person_or_404(data.person_id, db)

    try:
        grant = access_service.grant_access(
            db,
            person_id=data.person_id,
            module_key=data.module_key,
            resource_id=data.resource_id,
            expires_at=data.expires_at,
            access_days=data.access_days,
            notes=data.notes,
            actor_type=data.actor_type,
            actor_id=data.actor_id,
            commit=False,  # el commit lo hace este endpoint, con el pago adentro
        )

        if data.payment:
            payment = PersonPayment(
                person_id=data.person_id,
                concept_type=data.payment.concept_type,
                concept_id=data.payment.concept_id or data.resource_id,
                concept_label=data.payment.concept_label,
                amount=data.payment.amount,
                currency=data.payment.currency,
                period_year=data.payment.period_year,
                period_month=data.payment.period_month,
                method=data.payment.method,
                reference=data.payment.reference,
                paid_at=data.payment.paid_at or datetime.now().date(),
                registered_by_volunteer_id=data.actor_id or None,
                notes=data.payment.notes,
            )
            db.add(payment)
            access_service.audit(
                db,
                person_id=data.person_id,
                module_key=data.module_key,
                resource_id=data.resource_id,
                action="payment",
                actor_type=data.actor_type,
                actor_id=data.actor_id,
                grant_id=grant.id,
                detail={
                    "amount": float(data.payment.amount),
                    "method": data.payment.method,
                    "reference": data.payment.reference,
                },
            )

        db.commit()
        db.refresh(grant)

    except Exception:
        db.rollback()
        log_error(
            "Error al habilitar",
            module="accesos", action="grant",
            meta={"person_id": data.person_id, "module_key": data.module_key},
            exc_info=True,
        )
        raise

    # El aviso va DESPUÉS del commit: si falla, la habilitación ya está firme.
    _notify_granted(db, person, data.module_key, data.resource_id)

    titles = _resource_titles(db, data.module_key)
    return GrantOut(**_serialize_grant(grant, person, titles))


@router.post("/bulk", response_model=List[GrantOut], status_code=201)
def create_grants_bulk(data: GrantBulkCreate, db: Session = Depends(get_db)):
    """Habilitación masiva. Todo o nada: si una persona falla, no queda nada a medias."""
    existing = {
        row[0]
        for row in db.query(ParticipantProfile.id)
        .filter(ParticipantProfile.id.in_(data.person_ids))
        .all()
    }
    missing = [pid for pid in data.person_ids if pid not in existing]
    if missing:
        raise HTTPException(status_code=404, detail=f"Personas inexistentes: {missing}")

    grants = []
    try:
        for person_id in data.person_ids:
            grants.append(
                access_service.grant_access(
                    db,
                    person_id=person_id,
                    module_key=data.module_key,
                    resource_id=data.resource_id,
                    expires_at=data.expires_at,
                    access_days=data.access_days,
                    notes=data.notes,
                    actor_type=data.actor_type,
                    actor_id=data.actor_id,
                    commit=False,
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        log_error("Error en habilitación masiva", module="accesos", action="grant_bulk",
                  meta={"cantidad": len(data.person_ids)}, exc_info=True)
        raise

    log_info("Habilitación masiva", module="accesos", action="grant_bulk", user=data.actor_id,
             meta={"cantidad": len(grants), "module_key": data.module_key, "resource_id": data.resource_id})

    # Avisos después del commit, uno por persona. Si alguno falla, la
    # habilitación ya quedó firme igual.
    for person in db.query(ParticipantProfile).filter(ParticipantProfile.id.in_(data.person_ids)).all():
        _notify_granted(db, person, data.module_key, data.resource_id)

    titles = _resource_titles(db, data.module_key)
    return [GrantOut(**_serialize_grant(g, None, titles)) for g in grants]


@router.post("/revocar", status_code=200)
def revoke_grant(data: GrantRevoke, db: Session = Depends(get_db)):
    """Revoca (baja lógica). La fila queda como registro de que existió, y los
    pagos NO se tocan: la plata entró igual."""
    _person_or_404(data.person_id, db)

    ok = access_service.revoke_access(
        db,
        person_id=data.person_id,
        module_key=data.module_key,
        resource_id=data.resource_id,
        actor_type=data.actor_type,
        actor_id=data.actor_id,
        notes=data.notes,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="La persona no tenía esa habilitación")
    return {"success": True}


# ── Pagos ──────────────────────────────────────────────────────────────

@router.get("/pagos", response_model=List[PaymentOut])
def list_payments(
    person_id: Optional[int] = Query(None),
    concept_type: Optional[str] = Query(None),
    concept_id: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    limit: int = Query(500, le=2000),
    db: Session = Depends(get_db),
):
    q = db.query(PersonPayment)
    if person_id:
        q = q.filter(PersonPayment.person_id == person_id)
    if concept_type:
        q = q.filter(PersonPayment.concept_type == concept_type)
    if concept_id is not None:
        q = q.filter(PersonPayment.concept_id == concept_id)
    if year:
        q = q.filter(func.year(PersonPayment.paid_at) == year)

    payments = q.order_by(PersonPayment.paid_at.desc(), PersonPayment.id.desc()).limit(limit).all()

    people = {
        p.id: f"{p.name or ''} {p.last_name or ''}".strip()
        for p in db.query(ParticipantProfile)
        .filter(ParticipantProfile.id.in_([x.person_id for x in payments] or [0]))
        .all()
    }

    return [
        PaymentOut(
            **{c.name: getattr(p, c.name) for c in PersonPayment.__table__.columns},
            person_name=people.get(p.person_id),
        )
        for p in payments
    ]


@router.post("/pagos", response_model=PaymentOut, status_code=201)
def create_payment(data: PaymentCreate, db: Session = Depends(get_db)):
    """Registra un ingreso suelto (sin habilitar nada): cuota de socio, donación,
    o el pago de alguien que todavía no se habilitó."""
    if not data.person_id:
        raise HTTPException(status_code=422, detail="Falta la persona")
    person = _person_or_404(data.person_id, db)

    try:
        payment = PersonPayment(**data.model_dump())
        db.add(payment)
        access_service.audit(
            db,
            person_id=data.person_id,
            module_key=data.concept_type,
            resource_id=data.concept_id or 0,
            action="payment",
            actor_type="admin",
            actor_id=data.registered_by_volunteer_id or 0,
            detail={"amount": float(data.amount), "method": data.method},
        )
        db.commit()
        db.refresh(payment)
    except Exception:
        db.rollback()
        log_error("Error al registrar pago", module="accesos", action="payment",
                  meta={"person_id": data.person_id}, exc_info=True)
        raise

    log_info("Pago registrado", module="accesos", action="payment",
             user=data.registered_by_volunteer_id,
             meta={"person_id": data.person_id, "concepto": data.concept_type, "monto": float(data.amount)})

    return PaymentOut(
        **{c.name: getattr(payment, c.name) for c in PersonPayment.__table__.columns},
        person_name=f"{person.name or ''} {person.last_name or ''}".strip(),
    )


@router.delete("/pagos/{payment_id}", status_code=204)
def delete_payment(payment_id: int, volunteer_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    """Corrige un pago mal cargado. Queda en la auditoría: los ingresos no
    desaparecen sin dejar rastro."""
    payment = db.query(PersonPayment).filter(PersonPayment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    try:
        access_service.audit(
            db,
            person_id=payment.person_id,
            module_key=payment.concept_type,
            resource_id=payment.concept_id or 0,
            action="payment_deleted",
            actor_type="admin",
            actor_id=volunteer_id or 0,
            detail={"amount": float(payment.amount), "reference": payment.reference},
        )
        db.delete(payment)
        db.commit()
        log_warn("Pago eliminado", module="accesos", action="payment_deleted", user=volunteer_id,
                 meta={"payment_id": payment_id, "person_id": payment.person_id})
    except Exception:
        db.rollback()
        log_error("Error al eliminar pago", module="accesos", action="payment_deleted",
                  meta={"payment_id": payment_id}, exc_info=True)
        raise


@router.get("/pagos/resumen")
def payments_summary(
    year: Optional[int] = Query(None),
    concept_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Recaudación agrupada por concepto."""
    q = db.query(
        PersonPayment.concept_type,
        PersonPayment.concept_id,
        func.count(PersonPayment.id).label("pagos"),
        func.coalesce(func.sum(PersonPayment.amount), 0).label("total"),
    )
    if year:
        q = q.filter(func.year(PersonPayment.paid_at) == year)
    if concept_type:
        q = q.filter(PersonPayment.concept_type == concept_type)

    rows = q.group_by(PersonPayment.concept_type, PersonPayment.concept_id).all()
    titles = _resource_titles(db, "capacitaciones")

    return [
        {
            "concept_type": r.concept_type,
            "concept_id": r.concept_id,
            "label": titles.get(r.concept_id) if r.concept_type == "capacitacion" else None,
            "pagos": r.pagos,
            "total": float(r.total),
        }
        for r in rows
    ]

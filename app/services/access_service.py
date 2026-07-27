"""
app/services/access_service.py — ALMA Backend — Habilitaciones
===============================================================
El "switchboard": qué ve cada persona más allá de lo que le da su rol.

    acceso efectivo = rol (código, lib/permissions.ts)
                      OR habilitación (datos, person_access_grants)

Las habilitaciones solo SUMAN. Nunca restan: si pudieran quitar permisos,
cada problema de acceso pasaría a ser una investigación entre dos capas
que se contradicen.

Identidad: el JWT trae voluntarios.id (staff) o participants.id (participante),
pero las habilitaciones viven en participant_profiles.id (la persona).
resolve_person_id() es el único traductor, y lo usa todo el módulo.
"""
from datetime import datetime, timedelta
from typing import Optional, List

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.access import PersonAccessGrant, AccessAudit
from app.models.participant import ParticipantProfile
from app.utils.logger import log_info, log_warn

# Roles que ven todo el contenido sin necesitar habilitación.
STAFF_ROLES = ("admin", "voluntario")


def resolve_person_id(db: Session, user_type: str, user_id: int) -> Optional[int]:
    """Traduce la identidad de sesión a la persona del registro maestro.

    Devuelve None si la persona no tiene ficha espejo (caso raro: voluntario
    con email duplicado). El llamador debe caer del lado seguro — para staff
    eso significa dar acceso igual, porque el rol ya se lo daba.
    """
    if not user_id:
        return None

    column = (
        ParticipantProfile.volunteer_id
        if user_type in STAFF_ROLES
        else ParticipantProfile.participant_id
    )

    row = db.query(ParticipantProfile.id).filter(column == user_id).first()
    return row[0] if row else None


def live_filter():
    """Condición de habilitación vigente: activa, no revocada y sin vencer.

    El vencimiento se evalúa acá (lazy), no con un cron: una habilitación
    vencida deja de dar acceso sola, sin que nada tenga que correr de noche.
    """
    now = datetime.now()
    return (
        PersonAccessGrant.is_active.is_(True),
        PersonAccessGrant.revoked_at.is_(None),
        or_(PersonAccessGrant.expires_at.is_(None), PersonAccessGrant.expires_at > now),
    )


def active_grants(db: Session, person_id: int, module_key: Optional[str] = None) -> List[PersonAccessGrant]:
    """Habilitaciones vigentes de una persona."""
    if not person_id:
        return []
    q = db.query(PersonAccessGrant).filter(
        PersonAccessGrant.person_id == person_id, *live_filter()
    )
    if module_key:
        q = q.filter(PersonAccessGrant.module_key == module_key)
    return q.all()


def all_grants(db: Session, person_id: int) -> List[PersonAccessGrant]:
    """Todas las habilitaciones de la persona, incluidas vencidas y revocadas
    (para que el admin vea el estado real en el ABM)."""
    return (
        db.query(PersonAccessGrant)
        .filter(PersonAccessGrant.person_id == person_id)
        .order_by(PersonAccessGrant.granted_at.desc())
        .all()
    )


def has_module_access(db: Session, user_type: str, user_id: int, module_key: str) -> bool:
    """¿Ve la pestaña del módulo?

    Alcanza con CUALQUIER habilitación vigente de ese module_key: tener la
    capacitación 7 implica ver el módulo. Sin esta regla aparece el clásico
    "le habilité la capacitación pero no le sale la pestaña".
    """
    if user_type in STAFF_ROLES:
        return True
    person_id = resolve_person_id(db, user_type, user_id)
    return bool(active_grants(db, person_id, module_key)) if person_id else False


def has_item_access(
    db: Session,
    user_type: str,
    user_id: int,
    module_key: str,
    resource_id: int,
    access_mode: str = "grant",
) -> bool:
    """¿Puede ver ESTE contenido? Es el chequeo que decide si sale el video_ref."""
    if user_type in STAFF_ROLES:
        return True
    if access_mode == "abierta":
        return True

    person_id = resolve_person_id(db, user_type, user_id)
    if not person_id:
        return False

    return (
        db.query(PersonAccessGrant.id)
        .filter(
            PersonAccessGrant.person_id == person_id,
            PersonAccessGrant.module_key == module_key,
            PersonAccessGrant.resource_id == resource_id,
            *live_filter(),
        )
        .first()
        is not None
    )


# ── Auditoría ──────────────────────────────────────────────────────────

def audit(
    db: Session,
    *,
    person_id: int,
    module_key: str,
    resource_id: int,
    action: str,
    actor_type: str,
    actor_id: int,
    grant_id: Optional[int] = None,
    detail: Optional[dict] = None,
) -> None:
    """Registra el cambio. Append-only: nunca se edita ni se borra.

    NO hace commit: se suma a la transacción del llamador para que la
    habilitación y su rastro entren o fallen juntos.
    """
    db.add(
        AccessAudit(
            grant_id=grant_id,
            person_id=person_id,
            module_key=module_key,
            resource_id=resource_id,
            action=action,
            actor_type=actor_type or "sistema",
            actor_id=actor_id or 0,
            detail=detail,
        )
    )


# ── Alta y baja ────────────────────────────────────────────────────────

def grant_access(
    db: Session,
    *,
    person_id: int,
    module_key: str,
    resource_id: int = 0,
    expires_at: Optional[datetime] = None,
    access_days: Optional[int] = None,
    notes: Optional[str] = None,
    actor_type: str = "admin",
    actor_id: int = 0,
    commit: bool = True,
) -> PersonAccessGrant:
    """Habilita (o rehabilita) a una persona. Es un upsert sobre el UNIQUE.

    Con commit=False la transacción queda abierta para que el llamador sume
    el pago y confirme todo junto.
    """
    if expires_at is None and access_days:
        expires_at = datetime.now() + timedelta(days=access_days)

    grant = (
        db.query(PersonAccessGrant)
        .filter(
            PersonAccessGrant.person_id == person_id,
            PersonAccessGrant.module_key == module_key,
            PersonAccessGrant.resource_id == resource_id,
        )
        .first()
    )

    if grant:
        # Rehabilitar: se limpia la revocación previa y se renueva el vencimiento.
        grant.is_active = True
        grant.revoked_at = None
        grant.expires_at = expires_at
        grant.granted_at = datetime.now()
        grant.granted_by_volunteer_id = actor_id or None
        if notes is not None:
            grant.notes = notes
    else:
        grant = PersonAccessGrant(
            person_id=person_id,
            module_key=module_key,
            resource_id=resource_id,
            is_active=True,
            expires_at=expires_at,
            notes=notes,
            granted_by_volunteer_id=actor_id or None,
        )
        db.add(grant)

    db.flush()  # necesita el id para la auditoría

    audit(
        db,
        person_id=person_id,
        module_key=module_key,
        resource_id=resource_id,
        action="grant",
        actor_type=actor_type,
        actor_id=actor_id,
        grant_id=grant.id,
        detail={"expires_at": expires_at.isoformat() if expires_at else None, "notes": notes},
    )

    if commit:
        db.commit()
        db.refresh(grant)

    log_info(
        "Habilitación otorgada",
        module="accesos",
        action="grant",
        user=actor_id,
        meta={"person_id": person_id, "module_key": module_key, "resource_id": resource_id},
    )
    return grant


def revoke_access(
    db: Session,
    *,
    person_id: int,
    module_key: str,
    resource_id: int = 0,
    actor_type: str = "admin",
    actor_id: int = 0,
    notes: Optional[str] = None,
) -> bool:
    """Revoca (baja lógica). La fila queda: es el registro de que existió."""
    grant = (
        db.query(PersonAccessGrant)
        .filter(
            PersonAccessGrant.person_id == person_id,
            PersonAccessGrant.module_key == module_key,
            PersonAccessGrant.resource_id == resource_id,
        )
        .first()
    )

    if not grant:
        log_warn(
            "Se intentó revocar una habilitación inexistente",
            module="accesos",
            action="revoke_missing",
            user=actor_id,
            meta={"person_id": person_id, "module_key": module_key, "resource_id": resource_id},
        )
        return False

    grant.is_active = False
    grant.revoked_at = datetime.now()
    if notes:
        grant.notes = notes

    audit(
        db,
        person_id=person_id,
        module_key=module_key,
        resource_id=resource_id,
        action="revoke",
        actor_type=actor_type,
        actor_id=actor_id,
        grant_id=grant.id,
        detail={"notes": notes},
    )

    db.commit()
    log_info(
        "Habilitación revocada",
        module="accesos",
        action="revoke",
        user=actor_id,
        meta={"person_id": person_id, "module_key": module_key, "resource_id": resource_id},
    )
    return True

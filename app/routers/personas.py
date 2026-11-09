from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional

from app.database import get_db
from app.models.participant import (
    Participant as ParticipantModel,
    ParticipantProfile as ProfileModel,
    ParticipantProgramEnrollment as EnrollmentModel,
)
from app.models.voluntario import Voluntario as VoluntarioModel
from app.schemas.persona import Persona, PersonaCreate, PersonaUpdate
from app.services.espejo_persona import propagar_a_voluntario
from app.utils.logger import log_info, log_warn, log_error

router = APIRouter()

# Centinela para distinguir "no mandaron el campo" de "lo mandaron en null".
# `None` no sirve: vaciar las especialidades de alguien es una edición válida.
_SIN_DATO = object()


# ── Los datos de los roles ────────────────────────────────────────────
#
# Una persona es una fila de `participant_profiles`, pero sus ROLES viven en
# otras dos tablas: la ficha de voluntario/a en `voluntarios` y la cuenta de
# login en `participants`. La ficha de una persona tiene que poder mostrar las
# tres cosas juntas sin mandar al frontend a buscar a otro módulo.
#
# Se resuelve con TRES consultas para toda la página, una por tabla. Hacerlo
# por fila sería N+1 sobre la lista completa de personas, que es larga.


def _con_datos_de_rol(db: Session, filas: List[ProfileModel]) -> List[dict]:
    """Agrega a cada persona los datos de sus roles. Devuelve dicts listos."""
    ids_vol = [p.volunteer_id for p in filas if p.volunteer_id]
    ids_part = [p.participant_id for p in filas if p.participant_id]

    # 1) La ficha de voluntario/a.
    voluntarios: dict[int, tuple] = {}
    if ids_vol:
        voluntarios = {
            v.id: v
            for v in db.query(
                VoluntarioModel.id,
                VoluntarioModel.is_admin,
                VoluntarioModel.status,
                VoluntarioModel.specialties,
                VoluntarioModel.registration_date,
            ).filter(VoluntarioModel.id.in_(ids_vol)).all()
        }

    # 2) La cuenta de login.
    cuentas: dict[int, tuple] = {}
    inscripciones: dict[int, int] = {}
    if ids_part:
        cuentas = {
            c.id: c
            for c in db.query(
                ParticipantModel.id,
                ParticipantModel.is_active,
                ParticipantModel.email_verified,
            ).filter(ParticipantModel.id.in_(ids_part)).all()
        }

        # 3) Cuántas inscripciones tiene cada uno, agrupado de una sola vez.
        inscripciones = dict(
            db.query(EnrollmentModel.participant_id, func.count(EnrollmentModel.id))
            .filter(EnrollmentModel.participant_id.in_(ids_part))
            .group_by(EnrollmentModel.participant_id)
            .all()
        )

    salida = []
    for p in filas:
        datos = Persona.model_validate(p).model_dump()

        v = voluntarios.get(p.volunteer_id) if p.volunteer_id else None
        datos["is_admin"] = bool(v and v.is_admin)
        datos["volunteer_status"] = v.status if v else None
        datos["specialties"] = v.specialties if v else None
        datos["volunteer_since"] = v.registration_date if v else None

        c = cuentas.get(p.participant_id) if p.participant_id else None
        datos["account_active"] = bool(c.is_active) if c else None
        datos["account_verified"] = bool(c.email_verified) if c else None
        datos["enrollments_count"] = inscripciones.get(p.participant_id, 0)

        salida.append(datos)
    return salida


# ── Personas (ABM de la base de datos) ────────────────────────────────

@router.get("/", response_model=List[Persona])
def list_personas(
    skip: int = 0,
    limit: int = 1000,
    name: Optional[str] = Query(None),
    last_name: Optional[str] = Query(None),
    cuit: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    province: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(ProfileModel)
    if name:
        q = q.filter(ProfileModel.name.ilike(f"%{name}%"))
    if last_name:
        q = q.filter(ProfileModel.last_name.ilike(f"%{last_name}%"))
    if cuit:
        q = q.filter(ProfileModel.cuit.ilike(f"%{cuit}%"))
    if city:
        q = q.filter(ProfileModel.city.ilike(f"%{city}%"))
    if province:
        q = q.filter(ProfileModel.province.ilike(f"%{province}%"))
    filas = (
        q.order_by(ProfileModel.name, ProfileModel.last_name)
        .offset(skip)
        .limit(limit)
        .all()
    )

    return _con_datos_de_rol(db, filas)


@router.get("/{id}", response_model=Persona)
def get_persona(id: int, db: Session = Depends(get_db)):
    p = db.query(ProfileModel).filter(ProfileModel.id == id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    # Con los datos de sus roles, igual que el listado: que traer una persona
    # sola no devuelva menos que traerla dentro de la lista.
    return _con_datos_de_rol(db, [p])[0]


@router.post("/", response_model=Persona, status_code=201)
def create_persona(data: PersonaCreate, db: Session = Depends(get_db)):
    try:
        p = ProfileModel(**data.model_dump())  # participant_id queda NULL (sin login)
        db.add(p)
        db.commit()
        db.refresh(p)
        log_info("Persona creada", module="personas", action="create_persona", meta={"id": p.id})
        return p
    except IntegrityError:
        db.rollback()
        log_warn("Email duplicado al crear persona", module="personas", action="create_persona")
        raise HTTPException(status_code=409, detail="Ya existe una persona con ese email")
    except Exception:
        db.rollback()
        log_error("Error al crear persona", module="personas", action="create_persona", exc_info=True)
        raise


@router.put("/{id}", response_model=Persona)
def update_persona(id: int, data: PersonaUpdate, db: Session = Depends(get_db)):
    p = db.query(ProfileModel).filter(ProfileModel.id == id).first()
    if not p:
        log_warn("Persona no encontrada para editar", module="personas", action="edit_persona", meta={"id": id})
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    try:
        cambios = data.model_dump(exclude_unset=True)

        # `specialties` no es una columna del perfil: vive en `voluntarios`. Se
        # saca de los cambios ANTES de escribir, porque setearla en el perfil no
        # guardaría nada y encima taparía el error.
        especialidades = cambios.pop("specialties", _SIN_DATO)

        for key, value in cambios.items():
            setattr(p, key, value)

        # Si la persona tiene ficha de voluntaria, los datos de contacto que se
        # acaban de editar viajan tambien para alla. Mismo motivo que en el PUT
        # de voluntarios, en el otro sentido.
        vol = propagar_a_voluntario(db, p, cambios.keys())

        if especialidades is not _SIN_DATO and p.volunteer_id is not None:
            if vol is None:
                vol = db.query(VoluntarioModel).filter(
                    VoluntarioModel.id == p.volunteer_id).first()
            if vol is not None:
                vol.specialties = especialidades

        db.commit()
        db.refresh(p)
        log_info(
            "Persona actualizada", module="personas", action="edit_persona",
            meta={"id": id, "voluntario_sincronizado": vol.id if vol else None},
        )
        return _con_datos_de_rol(db, [p])[0]
    except IntegrityError:
        db.rollback()
        log_warn("Email duplicado al editar persona", module="personas", action="edit_persona", meta={"id": id})
        raise HTTPException(status_code=409, detail="Ya existe una persona con ese email")
    except Exception:
        db.rollback()
        log_error("Error al actualizar persona", module="personas", action="edit_persona", meta={"id": id}, exc_info=True)
        raise


@router.delete("/{id}", status_code=204)
def delete_persona(id: int, db: Session = Depends(get_db)):
    # La tabla no tiene campo "activo" → baja física.
    p = db.query(ProfileModel).filter(ProfileModel.id == id).first()
    if not p:
        log_warn("Persona no encontrada para eliminar", module="personas", action="delete_persona", meta={"id": id})
        raise HTTPException(status_code=404, detail="Persona no encontrada")
    try:
        db.delete(p)
        db.commit()
        log_info("Persona eliminada", module="personas", action="delete_persona", meta={"id": id})
    except Exception:
        db.rollback()
        log_error("Error al eliminar persona", module="personas", action="delete_persona", meta={"id": id}, exc_info=True)
        raise

"""
app/routers/recordatorios.py — ALMA Backend — Recordatorios del participante
=============================================================================
Qué avisos quiere cada persona para cada evento al que se anotó.

No confundir con `calendar_instances.reminder_offsets`: ese lo configura el
admin y gobierna a los VOLUNTARIOS asignados. Acá decide la persona, para sí
misma, evento por evento.

Sin fila = sin recordatorios. Anotarse a un taller no suscribe a nadie a nada.
Los manda el cron (`alma_cron.py`) a las 6 de la mañana.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.calendar import CalendarInstance
from app.models.reminder import ParticipantEventReminder
from app.services import access_service
from app.utils.logger import log_error, log_info

router = APIRouter()

# Los tres únicos avisos que se ofrecen, en días de anticipación.
# Cerrado a propósito: sin esta lista, un cliente podría pedir "dentro de 400
# días" y el cron se pondría a mirar eventos del año que viene.
OFFSETS_VALIDOS = {7, 1, 0}


class RecordatoriosOut(BaseModel):
    offsets: List[int] = []


class RecordatoriosIn(BaseModel):
    user_type: str
    user_id: int
    offsets: List[int] = []

    @field_validator("offsets")
    @classmethod
    def solo_los_permitidos(cls, v: List[int]) -> List[int]:
        # Se descarta lo desconocido en vez de rechazar el pedido: la persona
        # tildó casillas, no escribió un JSON, y un error acá sería nuestro.
        return sorted({o for o in (v or []) if o in OFFSETS_VALIDOS}, reverse=True)


def _persona_o_403(db: Session, user_type: str, user_id: int) -> int:
    person_id = access_service.resolve_person_id(db, user_type, user_id)
    if not person_id:
        raise HTTPException(
            status_code=409,
            detail="No encontramos tu ficha de persona. Avisale a un administrador.",
        )
    return person_id


@router.get("/{instance_id}", response_model=RecordatoriosOut)
def get_recordatorios(
    instance_id: int,
    user_type: str = Query(...),
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Los avisos que pidió quien pregunta para ese evento. Vacío si no pidió."""
    person_id = access_service.resolve_person_id(db, user_type, user_id)
    if not person_id:
        return RecordatoriosOut(offsets=[])

    fila = (
        db.query(ParticipantEventReminder)
        .filter(
            ParticipantEventReminder.calendar_instance_id == instance_id,
            ParticipantEventReminder.person_id == person_id,
        )
        .first()
    )
    return RecordatoriosOut(offsets=list(fila.offsets or []) if fila else [])


@router.put("/{instance_id}", response_model=RecordatoriosOut)
def set_recordatorios(instance_id: int, data: RecordatoriosIn, db: Session = Depends(get_db)):
    """Guarda los avisos elegidos. Es un upsert sobre (evento, persona).

    Destildar todo NO borra la fila: la deja vacía. Así queda registro de que
    la persona decidió no recibir avisos, que no es lo mismo que no haber
    decidido nunca.
    """
    if not db.query(CalendarInstance.id).filter(CalendarInstance.id == instance_id).first():
        raise HTTPException(status_code=404, detail="Evento no encontrado")

    person_id = _persona_o_403(db, data.user_type, data.user_id)

    try:
        fila = (
            db.query(ParticipantEventReminder)
            .filter(
                ParticipantEventReminder.calendar_instance_id == instance_id,
                ParticipantEventReminder.person_id == person_id,
            )
            .first()
        )
        if not fila:
            fila = ParticipantEventReminder(
                calendar_instance_id=instance_id, person_id=person_id
            )
            db.add(fila)

        fila.offsets = data.offsets
        db.commit()

        log_info(
            "Recordatorios del participante actualizados",
            module="recordatorios", action="set",
            user=data.user_id,
            meta={"evento": instance_id, "offsets": data.offsets},
        )
        return RecordatoriosOut(offsets=data.offsets)
    except Exception:
        db.rollback()
        log_error("Error al guardar los recordatorios", module="recordatorios", action="set",
                  meta={"evento": instance_id}, exc_info=True)
        raise

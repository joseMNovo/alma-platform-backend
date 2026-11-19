"""Qué le mandó el sistema a quién: mails y campanitas, en una sola lista.

Por qué existe
──────────────
Las cuatro tareas del cron (`alma_cron.py`) no mandan mail por su cuenta:
llaman al mismo `POST /emails/send` que usa el resto del sistema, así que cada
envío ya deja su fila en `email_logs`. Las campanitas y los push dejan la suya
en `notifications`. O sea: el dato de "a quién le llegó" ya estaba guardado,
solo que repartido en dos tablas y sin ninguna pantalla que lo mostrara.

Qué NO hace, a propósito
────────────────────────
No dice si el cron corrió. Un día sin envíos y un día sin cron se ven igual
desde acá: los dos son una lista vacía. Eso lo responde el log de la VPS
(`logs/cron/alma_cron.log`) y el monitor que lo lee, que ya muestra las
corridas con sus contadores. Duplicar esa lectura acá sería mantener dos
fuentes para la misma pregunta.

Lo que esta pantalla sí contesta, y el monitor no: el NOMBRE de cada persona
que recibió cada cosa.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.email_log import EmailLog
from app.models.notification import Notification
from app.models.participant import Participant, ParticipantProfile
from app.models.voluntario import Voluntario
from app.schemas.aviso import Aviso, Destinatario
from app.utils.logger import log_error

router = APIRouter()


# Las plantillas y los tipos que dispara el cron, no una persona. Es una lista
# explícita y no `sent_by_volunteer_id IS NULL`: ese campo también viene vacío
# en los mails de registro y verificación, que los dispara alguien al apretar
# un botón. Confundirlos haría que la columna "automático" mintiera.
TEMPLATES_DEL_CRON = {"event_assignment", "birthday", "compra_pendiente", "cron_alert"}
KINDS_DEL_CRON = {"calendar_reminder", "birthday"}


def _nombre(*partes: Optional[str]) -> Optional[str]:
    texto = " ".join(p for p in partes if p).strip()
    return texto or None


def _destinatarios_por_email(db: Session) -> Dict[str, Destinatario]:
    """Mapa email → persona, en dos consultas.

    Se arma entero una vez y no por fila: una página de 200 avisos haría 200
    búsquedas, y son siempre las mismas pocas personas.
    """
    mapa: Dict[str, Destinatario] = {}

    # Primero los perfiles (el registro maestro), después los voluntarios.
    # El orden importa: si una persona está en las dos, gana la ficha de
    # voluntario/a, que es la que el equipo reconoce por el nombre.
    for p in db.query(ParticipantProfile).filter(ParticipantProfile.email.isnot(None)).all():
        if p.email:
            mapa[p.email.strip().lower()] = Destinatario(
                nombre=_nombre(p.name, p.last_name), email=p.email, tipo="participante",
            )

    for v in db.query(Voluntario).filter(Voluntario.email.isnot(None)).all():
        if v.email:
            mapa[v.email.strip().lower()] = Destinatario(
                nombre=_nombre(v.name, v.last_name), email=v.email, tipo="voluntario",
            )

    return mapa


def _destinatarios_de_campanita(db: Session) -> Dict[tuple, Destinatario]:
    """Mapa (user_type, user_id) → persona.

    El participante es el caso incómodo: `notifications.user_id` apunta a
    `participants` (el login), pero el nombre vive en `participant_profiles`.
    Por eso se entra por el perfil y se indexa por su `participant_id`.
    """
    mapa: Dict[tuple, Destinatario] = {}

    for v in db.query(Voluntario).all():
        mapa[("voluntario", v.id)] = Destinatario(
            nombre=_nombre(v.name, v.last_name), email=v.email, tipo="voluntario",
        )

    perfiles = db.query(ParticipantProfile).filter(ParticipantProfile.participant_id.isnot(None)).all()
    for p in perfiles:
        mapa[("participante", p.participant_id)] = Destinatario(
            nombre=_nombre(p.name, p.last_name), email=p.email, tipo="participante",
        )

    # Un login sin perfil no debería existir, pero si existe conviene mostrar
    # el email antes que una fila sin destinatario.
    faltantes = {pid for (tipo, pid) in mapa if tipo == "participante"}
    for c in db.query(Participant).all():
        if c.id not in faltantes:
            mapa[("participante", c.id)] = Destinatario(nombre=None, email=c.email, tipo="participante")

    return mapa


def _lista(valor: Any) -> List[str]:
    """`to_addresses` es JSON. Según cómo se haya guardado puede llegar ya
    deserializado o como texto, así que se contemplan los dos."""
    if valor is None:
        return []
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except ValueError:
            return [valor]
    if isinstance(valor, list):
        return [str(x) for x in valor if x]
    return [str(valor)]


@router.get("/", response_model=List[Aviso])
def listar_avisos(
    days: int = Query(14, ge=1, le=365),
    limit: int = Query(200, ge=1, le=1000),
    canal: Optional[str] = Query(None, description="email | campanita"),
    solo_automaticos: bool = Query(False, description="solo lo que dispara el cron"),
    db: Session = Depends(get_db),
):
    """Los avisos de los últimos `days` días, lo más nuevo primero.

    Las dos tablas se traen por separado y se mezclan acá en vez de con un
    UNION: tienen columnas distintas y el UNION obligaría a inventar columnas
    vacías de los dos lados para que encajen.

    `limit` se aplica a cada fuente ANTES de mezclar. Si un día hubiera 1000
    mails y 3 campanitas, las campanitas igual aparecen: cortar después de
    mezclar las habría dejado afuera por ser minoría.
    """
    desde = datetime.now() - timedelta(days=days)
    avisos: List[Aviso] = []

    try:
        if canal != "campanita":
            por_email = _destinatarios_por_email(db)
            logs = (
                db.query(EmailLog)
                .filter(EmailLog.sent_at >= desde)
                .order_by(EmailLog.sent_at.desc(), EmailLog.id.desc())
                .limit(limit)
                .all()
            )
            for registro in logs:
                automatico = (registro.template or "") in TEMPLATES_DEL_CRON
                if solo_automaticos and not automatico:
                    continue
                # Una fila por destinatario: un mail a cinco personas es, para
                # esta pantalla, cinco cosas que pasaron. Agruparlas obligaría
                # a abrir la fila para saber si a alguien le llegó.
                for i, direccion in enumerate(_lista(registro.to_addresses)):
                    quien = por_email.get(direccion.strip().lower())
                    avisos.append(Aviso(
                        id=f"email:{registro.id}:{i}",
                        canal="email",
                        fecha=registro.sent_at.isoformat() if registro.sent_at else None,
                        tarea=registro.template,
                        titulo=registro.subject,
                        destinatario=quien or Destinatario(email=direccion),
                        estado="fallido" if registro.status == "failed" else "enviado",
                        error=registro.error_message,
                        automatico=automatico,
                    ))

        if canal != "email":
            por_usuario = _destinatarios_de_campanita(db)
            notis = (
                db.query(Notification)
                .filter(Notification.created_at >= desde)
                .order_by(Notification.created_at.desc(), Notification.id.desc())
                .limit(limit)
                .all()
            )
            for n in notis:
                automatico = (n.kind or "") in KINDS_DEL_CRON
                if solo_automaticos and not automatico:
                    continue
                avisos.append(Aviso(
                    id=f"campanita:{n.id}",
                    canal="campanita",
                    fecha=n.created_at.isoformat() if n.created_at else None,
                    tarea=n.kind,
                    titulo=n.title,
                    destinatario=por_usuario.get((n.user_type, n.user_id)) or Destinatario(),
                    # Una campanita no se "entrega": se deja puesta. Lo único
                    # que se sabe de ella es si la persona la vio.
                    estado="leido" if n.is_read else "sin_leer",
                    automatico=automatico,
                ))

        avisos.sort(key=lambda a: a.fecha or "", reverse=True)
        return avisos[:limit]

    except Exception:
        log_error("Error al listar avisos enviados", module="avisos", action="list", exc_info=True)
        raise

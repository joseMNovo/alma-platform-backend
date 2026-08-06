"""
app/routers/certificados.py — ALMA Backend — Plantillas de certificado
=======================================================================
ABM de la redacción del certificado + generación del PDF de muestra.

Alcance de esta etapa: se edita y se previsualiza el texto. La EMISIÓN (un
certificado a nombre de una persona, con código de verificación) llega junto
con el test de finalización.

Cuando llegue, la regla es: el certificado emitido guarda su propia copia del
texto ya resuelto. Nunca un puntero a la plantilla, porque corregir la
redacción no puede reescribir un certificado entregado hace seis meses.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.access import PersonAccessGrant
from app.models.certificate import Certificate, CertificateTemplate
from app.models.file import File as FileModel
from app.models.participant import ParticipantProfile
from app.models.survey import Survey, SurveyAttempt
from app.models.training import Training, TrainingItem, TrainingItemProgress
from app.schemas.certificate import (
    CertificateBulkIssue,
    CertificateIssue,
    CertificateOut,
    CertificateSample,
    CertificateTemplateCreate,
    CertificateTemplateOut,
    CertificateTemplateUpdate,
    CertificateVerification,
    DeliveryRow,
)
from app.services import (
    access_service, certificate_pdf, certificate_service, file_storage,
)
from app.utils.logger import log_error, log_info, log_warn

router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────────

def _get_or_404(template_id: int, db: Session) -> CertificateTemplate:
    template = db.query(CertificateTemplate).filter(CertificateTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    return template


def _clear_other_defaults(db: Session, keep_id: Optional[int] = None) -> None:
    """Deja una sola plantilla por defecto. Sin esto, cuál se usa al emitir
    dependería del orden de la tabla."""
    q = db.query(CertificateTemplate).filter(CertificateTemplate.is_default.is_(True))
    if keep_id:
        q = q.filter(CertificateTemplate.id != keep_id)
    for other in q.all():
        other.is_default = False


def _file_bytes(db: Session, guid: Optional[str]) -> Optional[bytes]:
    """Bytes de un archivo por guid. None si no está: el PDF se dibuja igual,
    sin esa imagen."""
    if not guid:
        return None
    archivo = db.query(FileModel).filter(FileModel.guid == guid).first()
    if not archivo:
        return None
    try:
        return file_storage.read_bytes(guid, archivo.extension)
    except (FileNotFoundError, OSError):
        log_warn("Archivo del certificado ausente en disco",
                 module="certificados", action="file_bytes", meta={"guid": guid})
        return None


# ── ABM de plantillas ──────────────────────────────────────────────────

@router.get("/", response_model=List[CertificateTemplateOut])
def list_templates(db: Session = Depends(get_db)):
    return (
        db.query(CertificateTemplate)
        .order_by(CertificateTemplate.is_default.desc(), CertificateTemplate.name)
        .all()
    )


@router.post("/", response_model=CertificateTemplateOut, status_code=201)
def create_template(data: CertificateTemplateCreate, db: Session = Depends(get_db)):
    payload = data.model_dump()
    try:
        # La primera plantilla es la default sí o sí: si no, no habría ninguna
        # y la emisión se quedaría sin texto.
        if not db.query(CertificateTemplate.id).first():
            payload["is_default"] = True

        template = CertificateTemplate(**payload)
        db.add(template)
        db.flush()

        if template.is_default:
            _clear_other_defaults(db, keep_id=template.id)

        db.commit()
        db.refresh(template)
        log_info("Plantilla de certificado creada", module="certificados", action="create",
                 user=data.created_by_volunteer_id, meta={"id": template.id, "name": template.name})
        return template
    except Exception:
        db.rollback()
        log_error("Error al crear la plantilla", module="certificados", action="create", exc_info=True)
        raise


@router.put("/{template_id}", response_model=CertificateTemplateOut)
def update_template(template_id: int, data: CertificateTemplateUpdate, db: Session = Depends(get_db)):
    template = _get_or_404(template_id, db)
    payload = data.model_dump(exclude_unset=True)

    try:
        for key, value in payload.items():
            setattr(template, key, value)

        if payload.get("is_default"):
            _clear_other_defaults(db, keep_id=template.id)

        db.commit()
        db.refresh(template)
        log_info("Plantilla de certificado actualizada", module="certificados", action="edit",
                 meta={"id": template_id, "campos": list(payload)})
        return template
    except Exception:
        db.rollback()
        log_error("Error al actualizar la plantilla", module="certificados", action="edit",
                  meta={"id": template_id}, exc_info=True)
        raise


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: int, db: Session = Depends(get_db)):
    """Borra una plantilla. La predeterminada no se puede borrar: primero hay
    que marcar otra, así nunca queda el sistema sin texto para emitir."""
    template = _get_or_404(template_id, db)

    if template.is_default:
        raise HTTPException(
            status_code=409,
            detail="Es la plantilla predeterminada. Marcá otra como predeterminada antes de borrarla.",
        )

    try:
        db.delete(template)
        db.commit()
        log_info("Plantilla de certificado eliminada", module="certificados", action="delete",
                 meta={"id": template_id})
    except Exception:
        db.rollback()
        log_error("Error al eliminar la plantilla", module="certificados", action="delete",
                  meta={"id": template_id}, exc_info=True)
        raise


# ── Vista previa ───────────────────────────────────────────────────────

@router.post("/muestra")
def sample_pdf(data: CertificateSample, db: Session = Depends(get_db)):
    """PDF de muestra con datos de ejemplo.

    Recibe la plantilla ENTERA en el cuerpo, no un id: así se ve exactamente
    lo que la persona está escribiendo, sin obligarla a guardar para mirar
    cómo queda.
    """
    values = {
        "nombre_completo": data.nombre_completo,
        "nombre": data.nombre,
        "apellido": data.apellido,
        "dni": data.dni,
        "capacitacion": data.capacitacion,
        "fecha": data.fecha or certificate_pdf.format_date_es(),
        "horas": data.horas,
        "codigo": data.codigo,
    }

    try:
        pdf = certificate_pdf.render_certificate(
            heading=data.heading,
            body=data.body,
            legal_note=data.legal_note,
            signature_name=data.signature_name,
            signature_role=data.signature_role,
            logo_bytes=_file_bytes(db, data.logo_file_guid),
            signature_bytes=_file_bytes(db, data.signature_file_guid),
            values=values,
        )
    except Exception:
        log_error("Error al generar el PDF de muestra", module="certificados", action="sample",
                  exc_info=True)
        raise HTTPException(status_code=500, detail="No se pudo generar el PDF")

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="certificado-muestra.pdf"',
            # Es una previsualización de algo que cambia en cada tecla.
            "Cache-Control": "no-store",
        },
    )


# ── Emisión y entrega ──────────────────────────────────────────────────

MODULE_KEY = "capacitaciones"


def _training_or_404(training_id: Optional[int], db: Session) -> Optional[Training]:
    if not training_id:
        return None
    training = db.query(Training).filter(Training.id == training_id).first()
    if not training:
        raise HTTPException(status_code=404, detail="Capacitación no encontrada")
    return training


@router.post("/emitir", response_model=CertificateOut, status_code=201)
def issue_certificate(data: CertificateIssue, db: Session = Depends(get_db)):
    """Emisión a mano: cursos presenciales y capacitaciones sin evaluación.

    Re-emitir sobre uno que ya existe lo ACTUALIZA y conserva el código, así
    el link que ya circuló sigue verificando.
    """
    training = _training_or_404(data.training_id, db)
    try:
        return certificate_service.emitir(
            db,
            person_id=data.person_id,
            training=training,
            issued_by_volunteer_id=data.issued_by_volunteer_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/emitir-masivo", response_model=List[CertificateOut])
def issue_certificates(data: CertificateBulkIssue, db: Session = Depends(get_db)):
    """Emite a varias personas de una.

    Los que fallan se saltean y quedan en el log: que a una persona le falte
    el nombre no puede frenar la tanda entera.
    """
    training = _training_or_404(data.training_id, db)
    emitidos = []

    for person_id in dict.fromkeys(data.person_ids):
        try:
            emitidos.append(
                certificate_service.emitir(
                    db,
                    person_id=person_id,
                    training=training,
                    issued_by_volunteer_id=data.issued_by_volunteer_id,
                )
            )
        except ValueError as e:
            log_warn(
                "No se pudo emitir un certificado de la tanda",
                module="certificados", action="emitir_masivo",
                meta={"person_id": person_id, "motivo": str(e)},
            )

    log_info(
        "Emisión masiva", module="certificados", action="emitir_masivo",
        user=data.issued_by_volunteer_id,
        meta={"training_id": data.training_id, "pedidos": len(data.person_ids),
              "emitidos": len(emitidos)},
    )
    return emitidos


@router.post("/{code}/marcar-enviado", response_model=CertificateOut)
def mark_sent(code: str, db: Session = Depends(get_db)):
    """Deja constancia de que se le mandó.

    El mail lo dispara el frontend, que es quien sabe armar el link público
    con el dominio. Acá solo se registra, para poder filtrar después por
    "todavía no se le mandó".
    """
    certificado = db.query(Certificate).filter(Certificate.code == code.strip().upper()).first()
    if not certificado:
        raise HTTPException(status_code=404, detail="Certificado no encontrado")

    certificado.sent_at = datetime.now()
    db.commit()
    db.refresh(certificado)
    return certificado


@router.get("/mios", response_model=List[CertificateOut])
def my_certificates(
    user_type: str = Query(...),
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Los certificados de quien pregunta. Los anulados no se listan."""
    person_id = access_service.resolve_person_id(db, user_type, user_id)
    if not person_id:
        return []
    return (
        db.query(Certificate)
        .filter(Certificate.person_id == person_id, Certificate.revoked_at.is_(None))
        .order_by(Certificate.issued_at.desc())
        .all()
    )


@router.get("/emitidos", response_model=List[CertificateOut])
def issued_certificates(
    training_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Historial: todos los certificados entregados, del más nuevo al más viejo.

    Incluye los ANULADOS a propósito: el historial tiene que mostrar que
    existieron y que se dieron de baja, no hacerlos desaparecer.
    """
    q = db.query(Certificate)
    if training_id:
        q = q.filter(Certificate.training_id == training_id)
    return q.order_by(Certificate.issued_at.desc()).limit(500).all()


@router.get("/verificar/{code}", response_model=CertificateVerification)
def verify_certificate(code: str, db: Session = Depends(get_db)):
    """Verificación PÚBLICA. Sin sesión: es el punto de que exista.

    Devuelve 200 con valido=false cuando el código no existe, en vez de 404:
    la página tiene que poder decir "este certificado no es válido" sin que
    parezca que se rompió.

    Muestra nombre, capacitación y fecha —sin eso nadie puede confirmar que
    el certificado sea de quien lo presenta— pero NUNCA el DNI.
    """
    certificado = db.query(Certificate).filter(Certificate.code == code.strip().upper()).first()

    if not certificado:
        return CertificateVerification(valido=False, code=code)

    return CertificateVerification(
        valido=certificado.revoked_at is None,
        code=certificado.code,
        holder_name=certificado.holder_name,
        training_title=certificado.training_title,
        issued_at=certificado.issued_at,
        hours=certificado.hours,
        revoked=certificado.revoked_at is not None,
        revoked_reason=certificado.revoked_reason,
    )


@router.get("/{code}/pdf")
def certificate_pdf_download(code: str, db: Session = Depends(get_db)):
    """El PDF, regenerado en el momento desde el texto congelado.

    No hay archivo guardado en ningún lado: se arma cada vez que alguien lo
    pide. Por eso no puede desincronizarse del registro ni perderse.
    """
    certificado = db.query(Certificate).filter(Certificate.code == code.strip().upper()).first()
    if not certificado:
        raise HTTPException(status_code=404, detail="Certificado no encontrado")
    if certificado.revoked_at:
        raise HTTPException(status_code=409, detail="Este certificado fue anulado")

    try:
        pdf = certificate_service.pdf_de(db, certificado)
    except Exception:
        log_error("Error al generar el PDF del certificado", module="certificados",
                  action="pdf", meta={"code": code}, exc_info=True)
        raise HTTPException(status_code=500, detail="No se pudo generar el PDF")

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{certificate_service.nombre_archivo(certificado)}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/entrega/{training_id}", response_model=List[DeliveryRow])
def delivery_board(training_id: int, db: Session = Depends(get_db)):
    """El tablero de entrega: cada persona de una capacitación con TODO su
    estado en una sola fila.

    Se arma acá y no en el frontend a propósito: cruzar habilitaciones,
    progreso, intentos y certificados desde la pantalla serían cuatro pedidos,
    y la regla de "aprobó pero todavía no tiene certificado" terminaría
    escrita adentro de un componente.
    """
    training = _training_or_404(training_id, db)

    item_ids = [i.id for i in training.items if i.is_published]
    total_items = len(item_ids)

    habilitadas = {
        row.person_id
        for row in db.query(PersonAccessGrant.person_id)
        .filter(
            PersonAccessGrant.module_key == MODULE_KEY,
            PersonAccessGrant.resource_id == training_id,
            *access_service.live_filter(),
        )
        .all()
    }
    person_ids = set(habilitadas)

    survey = (
        db.query(Survey)
        .filter(
            Survey.owner_type == "capacitacion",
            Survey.owner_id == training_id,
            Survey.kind == "evaluacion",
        )
        .first()
    )

    # Entran también quienes ya rindieron o ya tienen certificado, aunque la
    # habilitación se les haya vencido: siguen necesitando su certificado.
    if survey:
        person_ids |= {
            row.person_id
            for row in db.query(SurveyAttempt.person_id)
            .filter(SurveyAttempt.survey_id == survey.id, SurveyAttempt.submitted_at.isnot(None))
            .all()
        }
    person_ids |= {
        row.person_id
        for row in db.query(Certificate.person_id)
        .filter(Certificate.training_id == training_id)
        .all()
    }

    if not person_ids:
        return []

    personas = (
        db.query(ParticipantProfile)
        .filter(ParticipantProfile.id.in_(person_ids))
        .order_by(ParticipantProfile.last_name, ParticipantProfile.name)
        .all()
    )

    completados = {}
    if item_ids:
        for person_id, hechos in (
            db.query(TrainingItemProgress.person_id, func.count(TrainingItemProgress.id))
            .filter(
                TrainingItemProgress.person_id.in_(person_ids),
                TrainingItemProgress.training_item_id.in_(item_ids),
                TrainingItemProgress.completed_at.isnot(None),
            )
            .group_by(TrainingItemProgress.person_id)
            .all()
        ):
            completados[person_id] = hechos

    # El PRIMERO de cada persona es el mejor: viene ordenado por aprobado y
    # nota descendente.
    mejores = {}
    if survey:
        for intento in (
            db.query(SurveyAttempt)
            .filter(
                SurveyAttempt.survey_id == survey.id,
                SurveyAttempt.person_id.in_(person_ids),
                SurveyAttempt.submitted_at.isnot(None),
            )
            .order_by(SurveyAttempt.passed.desc(), SurveyAttempt.score.desc())
            .all()
        ):
            mejores.setdefault(intento.person_id, intento)

    certificados = {
        c.person_id: c
        for c in db.query(Certificate)
        .filter(Certificate.training_id == training_id, Certificate.person_id.in_(person_ids))
        .all()
    }

    filas = []
    for persona in personas:
        hechos = completados.get(persona.id, 0)
        mejor = mejores.get(persona.id)
        certificado = certificados.get(persona.id)

        filas.append(
            DeliveryRow(
                person_id=persona.id,
                name=f"{persona.name or ''} {persona.last_name or ''}".strip() or None,
                email=persona.email,
                has_access=persona.id in habilitadas,
                items_total=total_items,
                items_completed=hechos,
                content_done=total_items > 0 and hechos >= total_items,
                survey_passed=(bool(mejor and mejor.passed) if survey else None),
                best_score=float(mejor.score) if mejor and mejor.score is not None else None,
                certificate_code=certificado.code if certificado else None,
                issued_at=certificado.issued_at if certificado else None,
                sent_at=certificado.sent_at if certificado else None,
            )
        )

    return filas

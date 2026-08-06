"""
app/routers/encuestas.py — ALMA Backend — Encuestas y evaluaciones
===================================================================
Motor genérico de cuestionarios. Hoy lo usa Capacitaciones para la evaluación
que habilita el certificado; el motor no sabe qué es una capacitación.

REGLA DE ORO: hay DOS serializaciones y no se mezclan.
  · `_admin(...)`   → incluye `is_correct`. Solo para quien administra.
  · `_publica(...)` → NO lo incluye. Es lo único que ve quien responde.

Son schemas distintos a propósito (`SurveyOptionAdminOut` vs
`SurveyOptionPublicOut`): un campo opcional que alguien olvide vaciar filtra
la respuesta correcta y nadie se entera hasta que la evaluación no vale nada.
"""
import random
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.participant import ParticipantProfile
from app.models.survey import Survey, SurveyAttempt, SurveyOption, SurveyQuestion
from app.models.training import Training
from app.schemas.survey import (
    AttemptOut, QuestionResult, SurveyAdminOut, SurveyCreate, SurveyPublicOut,
    SurveyQuestionIn, SurveyResult, SurveySubmit, SurveyUpdate,
)
from app.services import access_service, certificate_service, survey_service
from app.utils.logger import log_error, log_info, log_warn

router = APIRouter()

# Las evaluaciones de capacitaciones heredan el permiso del módulo.
MODULE_KEY = "capacitaciones"


# ── Serialización ──────────────────────────────────────────────────────

def _admin(db: Session, survey: Survey) -> dict:
    """Vista del admin: con respuestas correctas y con cuánta gente rindió."""
    intentos = (
        db.query(
            func.count(SurveyAttempt.id),
            func.coalesce(func.sum(SurveyAttempt.passed), 0),
        )
        .filter(
            SurveyAttempt.survey_id == survey.id,
            SurveyAttempt.submitted_at.isnot(None),
        )
        .first()
    )

    return {
        "id": survey.id,
        "title": survey.title,
        "description": survey.description,
        "owner_type": survey.owner_type,
        "owner_id": survey.owner_id,
        "kind": survey.kind,
        "passing_score": survey.passing_score,
        "max_attempts": survey.max_attempts,
        "shuffle_questions": survey.shuffle_questions,
        "show_answers": survey.show_answers,
        "is_published": survey.is_published,
        "created_by_volunteer_id": survey.created_by_volunteer_id,
        "created_at": survey.created_at,
        "updated_at": survey.updated_at,
        "questions": [
            {
                "id": q.id,
                "survey_id": q.survey_id,
                "text": q.text,
                "kind": q.kind,
                "help": q.help,
                "explanation": q.explanation,
                "points": q.points,
                "is_required": q.is_required,
                "sort_order": q.sort_order,
                "options": [
                    {
                        "id": o.id,
                        "text": o.text,
                        "is_correct": o.is_correct,
                        "sort_order": o.sort_order,
                    }
                    for o in q.options
                ],
            }
            for q in survey.questions
        ],
        "attempts_count": int(intentos[0] or 0),
        "passed_count": int(intentos[1] or 0),
    }


def _publica(db: Session, survey: Survey, person_id: Optional[int]) -> dict:
    """Vista de quien responde. Sin `is_correct`: esta función ES la frontera."""
    preguntas = list(survey.questions)
    if survey.shuffle_questions:
        preguntas = random.sample(preguntas, len(preguntas))

    mejor = survey_service.best_attempt(db, survey.id, person_id) if person_id else None

    return {
        "id": survey.id,
        "title": survey.title,
        "description": survey.description,
        "kind": survey.kind,
        "passing_score": survey.passing_score,
        "max_attempts": survey.max_attempts,
        "show_answers": survey.show_answers,
        "questions": [
            {
                "id": q.id,
                "text": q.text,
                "kind": q.kind,
                "help": q.help,
                "points": q.points,
                "is_required": q.is_required,
                "sort_order": q.sort_order,
                "options": [
                    {"id": o.id, "text": o.text, "sort_order": o.sort_order}
                    for o in q.options
                ],
            }
            for q in preguntas
        ],
        "attempts_used": survey_service.attempts_used(db, survey.id, person_id) if person_id else 0,
        "can_attempt": survey_service.can_attempt(db, survey, person_id) if person_id else False,
        "best_score": mejor.score if mejor else None,
        "passed": bool(mejor and mejor.passed),
    }


def _dueno_habilitado(db: Session, survey: Survey, user_type: str, user_id: int) -> bool:
    """¿Esta persona puede acceder a lo que la encuesta evalúa?

    Sin este chequeo, cualquiera con sesión podía pedir por id la evaluación
    de una capacitación que no compró, rendirla y llevarse el certificado.
    La encuesta hereda el permiso de su dueño: no tiene uno propio.

    Las encuestas sueltas (owner_id = 0) o de dueños sin control de acceso no
    restringen nada.
    """
    if survey.owner_type != "capacitacion" or not survey.owner_id:
        return True

    training = db.query(Training).filter(Training.id == survey.owner_id).first()
    if not training:
        return False

    return access_service.has_item_access(
        db, user_type, user_id, MODULE_KEY, training.id, training.access_mode
    )


def _get_or_404(survey_id: int, db: Session) -> Survey:
    survey = db.query(Survey).filter(Survey.id == survey_id).first()
    if not survey:
        raise HTTPException(status_code=404, detail="Encuesta no encontrada")
    return survey


# ── ABM (admin) ────────────────────────────────────────────────────────

@router.get("/", response_model=List[SurveyAdminOut])
def list_surveys(
    owner_type: Optional[str] = Query(None),
    owner_id: Optional[int] = Query(None),
    kind: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(Survey)
    if owner_type:
        q = q.filter(Survey.owner_type == owner_type)
    if owner_id is not None:
        q = q.filter(Survey.owner_id == owner_id)
    if kind:
        q = q.filter(Survey.kind == kind)

    return [_admin(db, s) for s in q.order_by(Survey.owner_type, Survey.title).all()]


@router.post("/", response_model=SurveyAdminOut, status_code=201)
def create_survey(data: SurveyCreate, db: Session = Depends(get_db)):
    """Crea la encuesta. El UNIQUE de la base impide dos evaluaciones para el
    mismo dueño; acá se avisa con un mensaje entendible antes de chocarlo."""
    ya_existe = (
        db.query(Survey.id)
        .filter(
            Survey.owner_type == data.owner_type,
            Survey.owner_id == data.owner_id,
            Survey.kind == data.kind,
        )
        .first()
    )
    if ya_existe:
        raise HTTPException(
            status_code=409,
            detail="Ya hay una encuesta de ese tipo para este ítem. Editá la que existe.",
        )

    try:
        survey = Survey(**data.model_dump())
        db.add(survey)
        db.commit()
        db.refresh(survey)
        log_info("Encuesta creada", module="encuestas", action="create",
                 user=data.created_by_volunteer_id,
                 meta={"id": survey.id, "owner": f"{survey.owner_type}:{survey.owner_id}"})
        return _admin(db, survey)
    except Exception:
        db.rollback()
        log_error("Error al crear la encuesta", module="encuestas", action="create", exc_info=True)
        raise


@router.get("/{survey_id}", response_model=SurveyAdminOut)
def get_survey(survey_id: int, db: Session = Depends(get_db)):
    return _admin(db, _get_or_404(survey_id, db))


@router.put("/{survey_id}", response_model=SurveyAdminOut)
def update_survey(survey_id: int, data: SurveyUpdate, db: Session = Depends(get_db)):
    survey = _get_or_404(survey_id, db)
    payload = data.model_dump(exclude_unset=True)
    try:
        for campo, valor in payload.items():
            setattr(survey, campo, valor)
        db.commit()
        db.refresh(survey)
        log_info("Encuesta actualizada", module="encuestas", action="edit",
                 meta={"id": survey_id, "campos": list(payload)})
        return _admin(db, survey)
    except Exception:
        db.rollback()
        log_error("Error al actualizar la encuesta", module="encuestas", action="edit",
                  meta={"id": survey_id}, exc_info=True)
        raise


@router.delete("/{survey_id}", status_code=204)
def delete_survey(survey_id: int, db: Session = Depends(get_db)):
    """Borra la encuesta con sus preguntas y TODOS los intentos.

    Se rechaza si ya la rindió alguien: borrarla dejaría certificados
    apuntando a un intento que no existe, y perdería el registro de quién
    aprobó qué. Para sacarla de circulación está despublicarla.
    """
    survey = _get_or_404(survey_id, db)

    rendida = (
        db.query(SurveyAttempt.id)
        .filter(SurveyAttempt.survey_id == survey_id, SurveyAttempt.submitted_at.isnot(None))
        .first()
    )
    if rendida:
        raise HTTPException(
            status_code=409,
            detail="Ya hay gente que la rindió. Despublicala en vez de borrarla: "
                   "borrarla perdería el registro de quiénes aprobaron.",
        )

    try:
        db.delete(survey)
        db.commit()
        log_info("Encuesta eliminada", module="encuestas", action="delete", meta={"id": survey_id})
    except Exception:
        db.rollback()
        log_error("Error al eliminar la encuesta", module="encuestas", action="delete",
                  meta={"id": survey_id}, exc_info=True)
        raise


# ── Preguntas ──────────────────────────────────────────────────────────

@router.put("/{survey_id}/preguntas", response_model=SurveyAdminOut)
def save_questions(survey_id: int, data: List[SurveyQuestionIn], db: Session = Depends(get_db)):
    """Guarda TODAS las preguntas de una vez.

    Se manda el cuestionario entero y acá se resuelve qué crear, actualizar y
    borrar. Con endpoints sueltos por pregunta, reordenar y editar en la misma
    pantalla exige coordinar seis llamadas y cualquier corte a mitad de camino
    deja el cuestionario a medias.

    Las preguntas que ya se respondieron NO se borran aunque desaparezcan del
    envío: se dejaría a los intentos viejos sin la pregunta que contestaron.
    """
    survey = _get_or_404(survey_id, db)

    existentes = {q.id: q for q in survey.questions}
    enviadas = {q.id for q in data if q.id}

    try:
        for orden, entrada in enumerate(data, start=1):
            if entrada.id and entrada.id in existentes:
                pregunta = existentes[entrada.id]
            else:
                pregunta = SurveyQuestion(survey_id=survey_id)
                db.add(pregunta)

            pregunta.text = entrada.text
            pregunta.kind = entrada.kind
            pregunta.help = (entrada.help or "").strip() or None
            pregunta.explanation = (entrada.explanation or "").strip() or None
            pregunta.points = entrada.points
            pregunta.is_required = entrada.is_required
            pregunta.sort_order = orden
            db.flush()

            opciones_existentes = {o.id: o for o in pregunta.options}
            opciones_enviadas = {o.id for o in entrada.options if o.id}

            for orden_op, opcion in enumerate(entrada.options, start=1):
                if opcion.id and opcion.id in opciones_existentes:
                    fila = opciones_existentes[opcion.id]
                else:
                    fila = SurveyOption(question_id=pregunta.id)
                    db.add(fila)
                fila.text = opcion.text
                fila.is_correct = opcion.is_correct
                fila.sort_order = orden_op

            for opcion_id, fila in opciones_existentes.items():
                if opcion_id not in opciones_enviadas:
                    db.delete(fila)

        for pregunta_id, pregunta in existentes.items():
            if pregunta_id in enviadas:
                continue
            respondida = (
                db.query(func.count()).select_from(SurveyAttempt)
                .filter(SurveyAttempt.survey_id == survey_id)
                .scalar()
            )
            if respondida:
                log_warn("Pregunta conservada: la encuesta ya tiene intentos",
                         module="encuestas", action="save_questions",
                         meta={"survey_id": survey_id, "question_id": pregunta_id})
                continue
            db.delete(pregunta)

        db.commit()
        db.refresh(survey)
        log_info("Preguntas guardadas", module="encuestas", action="save_questions",
                 meta={"survey_id": survey_id, "preguntas": len(data)})
        return _admin(db, survey)
    except Exception:
        db.rollback()
        log_error("Error al guardar las preguntas", module="encuestas", action="save_questions",
                  meta={"survey_id": survey_id}, exc_info=True)
        raise


# ── Rendir (quien responde) ────────────────────────────────────────────────────

@router.get("/de/{owner_type}/{owner_id}", response_model=Optional[SurveyPublicOut])
def survey_for_owner(
    owner_type: str,
    owner_id: int,
    user_type: str = Query(...),
    user_id: int = Query(...),
    kind: str = Query("evaluacion"),
    db: Session = Depends(get_db),
):
    """La encuesta de un ítem, lista para rendir. null si no tiene o no está
    publicada. NUNCA incluye las respuestas correctas."""
    survey = (
        db.query(Survey)
        .filter(
            Survey.owner_type == owner_type,
            Survey.owner_id == owner_id,
            Survey.kind == kind,
            Survey.is_published.is_(True),
        )
        .first()
    )
    if not survey or not survey.questions:
        return None

    # Sin acceso a la capacitación, la evaluación no existe para esta persona:
    # ni siquiera puede leer las preguntas.
    if not _dueno_habilitado(db, survey, user_type, user_id):
        return None

    person_id = access_service.resolve_person_id(db, user_type, user_id)
    return _publica(db, survey, person_id)


@router.post("/{survey_id}/responder", response_model=SurveyResult)
def submit_survey(survey_id: int, data: SurveySubmit, db: Session = Depends(get_db)):
    """Corrige el intento y, si aprueba una evaluación de capacitación, emite
    el certificado en la MISMA transacción.

    Juntos a propósito: si se guardara el intento y después fallara la
    emisión, quedaría alguien aprobado sin certificado y sin nada que lo
    delate.
    """
    survey = _get_or_404(survey_id, db)

    if not survey.is_published:
        raise HTTPException(status_code=409, detail="La encuesta no está disponible")

    # Se revalida acá y no solo en el GET: entregar es lo que emite el
    # certificado, así que es el punto que hay que blindar.
    if not _dueno_habilitado(db, survey, data.user_type, data.user_id):
        raise HTTPException(
            status_code=403,
            detail="No tenés acceso a esta capacitación.",
        )

    person_id = access_service.resolve_person_id(db, data.user_type, data.user_id)
    if not person_id:
        raise HTTPException(
            status_code=409,
            detail="No encontramos tu ficha de persona. Avisale a un administrador.",
        )

    if not survey_service.can_attempt(db, survey, person_id):
        raise HTTPException(
            status_code=409,
            detail="No te quedan intentos disponibles, o ya la aprobaste.",
        )

    try:
        intento, detalle, puntuables, acertadas = survey_service.grade_and_save(
            db, survey, person_id, data.answers
        )

        codigo = None
        if intento.passed and survey.owner_type == "capacitacion":
            training = db.query(Training).filter(Training.id == survey.owner_id).first()
            try:
                certificado = certificate_service.emitir(
                    db,
                    person_id=person_id,
                    training=training,
                    survey_attempt_id=intento.id,
                    commit=False,
                )
                codigo = certificado.code
            except ValueError as e:
                # Falta el nombre de la persona: la evaluación se guarda igual
                # (la aprobó), pero el certificado lo tendrá que emitir un
                # admin después de completarle la ficha.
                log_warn("Aprobó pero no se pudo emitir el certificado",
                         module="encuestas", action="responder",
                         meta={"person_id": person_id, "motivo": str(e)})

        db.commit()
        db.refresh(intento)

        log_info("Encuesta respondida", module="encuestas", action="responder",
                 user=data.user_id,
                 meta={"survey_id": survey_id, "score": float(intento.score or 0),
                       "passed": intento.passed, "certificado": codigo})

        return SurveyResult(
            attempt_id=intento.id,
            score=intento.score or 0,
            passed=intento.passed,
            passing_score=survey.passing_score,
            total_questions=puntuables,
            correct_questions=acertadas,
            results=[QuestionResult(**d) for d in detalle],
            certificate_code=codigo,
        )
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        log_error("Error al corregir la encuesta", module="encuestas", action="responder",
                  meta={"survey_id": survey_id}, exc_info=True)
        raise


# ── Resultados (admin) ─────────────────────────────────────────────────

@router.get("/{survey_id}/resultados", response_model=List[AttemptOut])
def survey_results(survey_id: int, db: Session = Depends(get_db)):
    """Quiénes rindieron y cómo les fue. Todos los intentos, no solo el mejor."""
    _get_or_404(survey_id, db)

    filas = (
        db.query(SurveyAttempt, ParticipantProfile)
        .join(ParticipantProfile, ParticipantProfile.id == SurveyAttempt.person_id)
        .filter(
            SurveyAttempt.survey_id == survey_id,
            SurveyAttempt.submitted_at.isnot(None),
        )
        .order_by(SurveyAttempt.submitted_at.desc())
        .all()
    )

    return [
        AttemptOut(
            id=intento.id,
            survey_id=intento.survey_id,
            person_id=intento.person_id,
            person_name=f"{persona.name or ''} {persona.last_name or ''}".strip() or None,
            person_email=persona.email,
            score=intento.score,
            passed=intento.passed,
            submitted_at=intento.submitted_at,
        )
        for intento, persona in filas
    ]

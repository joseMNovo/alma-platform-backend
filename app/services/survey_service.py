"""
app/services/survey_service.py — ALMA Backend — Corrección de encuestas
========================================================================
Acá vive la regla que sostiene todo el módulo: **la corrección pasa en el
servidor y `is_correct` nunca sale hacia quien responde**.

Si las respuestas correctas viajaran al navegador —aunque fuera en un campo
que la interfaz no dibuja— cualquiera las lee en las herramientas de
desarrollo y la evaluación deja de significar algo. Es el mismo criterio que
con `video_ref` en las capacitaciones: la frontera es el serializador, no la
pantalla.
"""
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.survey import (
    Survey, SurveyAnswer, SurveyAttempt, SurveyOption, SurveyQuestion,
)
from app.schemas.survey import AUTOGRADED_KINDS

# Se redondea a dos decimales: 66.666… no le dice nada a nadie, y con un
# mínimo de 70 la diferencia entre 66.66 y 66.67 no cambia ningún resultado.
_PRECISION = Decimal("0.01")


def attempts_used(db: Session, survey_id: int, person_id: int) -> int:
    """Intentos ENTREGADOS. Los que se abrieron y se abandonaron no cuentan:
    si contaran, cerrar la pestaña sin querer gastaría una oportunidad."""
    return (
        db.query(SurveyAttempt)
        .filter(
            SurveyAttempt.survey_id == survey_id,
            SurveyAttempt.person_id == person_id,
            SurveyAttempt.submitted_at.isnot(None),
        )
        .count()
    )


def best_attempt(db: Session, survey_id: int, person_id: int) -> Optional[SurveyAttempt]:
    """El mejor intento entregado: es el que define si aprobó."""
    return (
        db.query(SurveyAttempt)
        .filter(
            SurveyAttempt.survey_id == survey_id,
            SurveyAttempt.person_id == person_id,
            SurveyAttempt.submitted_at.isnot(None),
        )
        .order_by(SurveyAttempt.passed.desc(), SurveyAttempt.score.desc())
        .first()
    )


def can_attempt(db: Session, survey: Survey, person_id: int) -> bool:
    """¿Puede rendir?

    No se puede si ya aprobó (no hay nada que mejorar y evita que alguien
    rinda de nuevo y baje su propio resultado) ni si se le acabaron los
    intentos.
    """
    if not survey.is_published:
        return False

    mejor = best_attempt(db, survey.id, person_id)
    if mejor and mejor.passed:
        return False

    if survey.max_attempts:
        return attempts_used(db, survey.id, person_id) < survey.max_attempts
    return True


def _correct_option_ids(question: SurveyQuestion) -> set:
    return {o.id for o in question.options if o.is_correct}


def _grade_question(question: SurveyQuestion, chosen: List[int]) -> Optional[bool]:
    """Corrige UNA pregunta. None = no se puede corregir sola (texto libre).

    En las de opción múltiple se exige el conjunto EXACTO: marcar las tres
    correctas más una incorrecta no es media respuesta buena, es una
    respuesta mala. Y marcar dos de tres, tampoco.
    """
    if question.kind not in AUTOGRADED_KINDS:
        return None

    correctas = _correct_option_ids(question)
    elegidas = set(chosen or [])

    # Una pregunta sin ninguna opción correcta cargada está mal armada: no se
    # corrige, para no reprobar a nadie por un error del que la escribió.
    if not correctas:
        return None

    return elegidas == correctas


def grade_and_save(
    db: Session,
    survey: Survey,
    person_id: int,
    answers: List,
) -> Tuple[SurveyAttempt, List[dict], int, int]:
    """Corrige el intento entero, lo guarda y devuelve el resultado.

    Devuelve (intento, detalle_por_pregunta, preguntas_puntuables, acertadas).
    NO hace commit: el llamador cierra la transacción junto con lo que sigue
    (emitir el certificado), para que no quede un intento aprobado sin su
    certificado si algo falla en el medio.
    """
    por_pregunta: Dict[int, SurveyQuestion] = {q.id: q for q in survey.questions}
    elegidas_por_pregunta: Dict[int, List[int]] = {}
    texto_por_pregunta: Dict[int, Optional[str]] = {}

    for respuesta in answers:
        if respuesta.question_id not in por_pregunta:
            continue  # pregunta de otra encuesta o borrada: se ignora
        elegidas_por_pregunta[respuesta.question_id] = list(respuesta.option_ids or [])
        texto_por_pregunta[respuesta.question_id] = respuesta.text_answer

    intento = SurveyAttempt(
        survey_id=survey.id,
        person_id=person_id,
        submitted_at=datetime.now(),
    )
    db.add(intento)
    db.flush()  # necesita el id para colgarle las respuestas

    puntos_posibles = 0
    puntos_obtenidos = 0
    puntuables = 0
    acertadas = 0
    detalle: List[dict] = []

    for pregunta in survey.questions:
        elegidas = elegidas_por_pregunta.get(pregunta.id, [])
        correcta = _grade_question(pregunta, elegidas)

        if correcta is not None:
            puntuables += 1
            puntos_posibles += pregunta.points
            if correcta:
                acertadas += 1
                puntos_obtenidos += pregunta.points

        # Una fila por opción elegida; las de texto guardan una sola.
        if pregunta.kind == "texto":
            db.add(
                SurveyAnswer(
                    attempt_id=intento.id,
                    question_id=pregunta.id,
                    text_answer=(texto_por_pregunta.get(pregunta.id) or None),
                    is_correct=None,
                )
            )
        else:
            for opcion_id in elegidas:
                db.add(
                    SurveyAnswer(
                        attempt_id=intento.id,
                        question_id=pregunta.id,
                        option_id=opcion_id,
                        is_correct=correcta,
                    )
                )
            if not elegidas:
                # Sin responder: queda registrado como respuesta vacía, si no
                # la pregunta desaparece del intento y no se puede auditar.
                db.add(
                    SurveyAnswer(
                        attempt_id=intento.id,
                        question_id=pregunta.id,
                        is_correct=correcta,
                    )
                )

        detalle.append(
            {
                "question_id": pregunta.id,
                "is_correct": correcta,
                "correct_option_ids": (
                    sorted(_correct_option_ids(pregunta)) if survey.show_answers else None
                ),
                "explanation": (pregunta.explanation if survey.show_answers else None),
            }
        )

    # Las de opinión no tienen nota: se guardan las respuestas y listo.
    if survey.kind != "evaluacion" or puntos_posibles == 0:
        intento.score = None
        intento.passed = False
    else:
        porcentaje = (Decimal(puntos_obtenidos) / Decimal(puntos_posibles)) * 100
        intento.score = porcentaje.quantize(_PRECISION)
        intento.passed = intento.score >= Decimal(survey.passing_score)

    return intento, detalle, puntuables, acertadas

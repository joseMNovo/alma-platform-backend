from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Numeric, DateTime, TIMESTAMP,
    ForeignKey, func,
)
from sqlalchemy.orm import relationship as orm_relationship

from app.database import Base


class Survey(Base):
    """Un cuestionario. Dos usos con el mismo motor, según `kind`:

    - evaluacion: tiene nota y porcentaje mínimo. Es la que habilita el
      certificado de una capacitación.
    - opinion: satisfacción. Nadie aprueba ni desaprueba.

    `owner_type` + `owner_id` dicen de qué cuelga (hoy capacitaciones; el
    motor no sabe ni le importa qué es una capacitación).
    """

    __tablename__ = "surveys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(150), nullable=False)
    description = Column(Text, nullable=True)
    owner_type = Column(String(30), nullable=False, default="capacitacion")
    owner_id = Column(Integer, nullable=False, default=0)
    kind = Column(String(20), nullable=False, default="evaluacion")
    passing_score = Column(Integer, nullable=False, default=70)
    max_attempts = Column(Integer, nullable=True)
    shuffle_questions = Column(Boolean, nullable=False, default=False)
    show_answers = Column(Boolean, nullable=False, default=True)
    is_published = Column(Boolean, nullable=False, default=False)
    created_by_volunteer_id = Column(
        Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    questions = orm_relationship(
        "SurveyQuestion",
        back_populates="survey",
        cascade="all, delete-orphan",
        order_by="SurveyQuestion.sort_order",
        lazy="selectin",
    )


class SurveyQuestion(Base):
    """Una pregunta.

    OJO con kind='texto': una respuesta abierta no se puede corregir sola. Se
    guarda, pero queda fuera del cálculo de la nota.
    """

    __tablename__ = "survey_questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    survey_id = Column(Integer, ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False)
    text = Column(Text, nullable=False)
    kind = Column(String(20), nullable=False, default="unica")
    help = Column(String(255), nullable=True)
    # El porqué de la respuesta correcta. Se muestra DESPUÉS de contestar —
    # nunca antes, o sería regalar la respuesta. No confundir con `help`,
    # que es la aclaración previa.
    explanation = Column(Text, nullable=True)
    points = Column(Integer, nullable=False, default=1)
    is_required = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    survey = orm_relationship("Survey", back_populates="questions")
    options = orm_relationship(
        "SurveyOption",
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="SurveyOption.sort_order",
        lazy="selectin",
    )


class SurveyOption(Base):
    """Una opción de respuesta.

    `is_correct` es el secreto del módulo: no sale hacia quien responde por ningún
    endpoint. Ver el serializador del router.
    """

    __tablename__ = "survey_options"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question_id = Column(
        Integer, ForeignKey("survey_questions.id", ondelete="CASCADE"), nullable=False
    )
    text = Column(String(500), nullable=False)
    is_correct = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)

    question = orm_relationship("SurveyQuestion", back_populates="options")


class SurveyAttempt(Base):
    """Cada vez que una persona rinde.

    Se guardan TODOS los intentos, no solo el mejor: saber que alguien aprobó
    recién en el tercero dice algo sobre el material, no solo sobre la persona.
    """

    __tablename__ = "survey_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    survey_id = Column(Integer, ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False)
    person_id = Column(
        Integer, ForeignKey("participant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    score = Column(Numeric(5, 2), nullable=True)
    passed = Column(Boolean, nullable=False, default=False)
    started_at = Column(TIMESTAMP, server_default=func.now())
    submitted_at = Column(DateTime, nullable=True)

    answers = orm_relationship(
        "SurveyAnswer",
        back_populates="attempt",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class SurveyAnswer(Base):
    """Una fila por opción elegida: en las de opción múltiple, un mismo
    intento y pregunta tienen varias."""

    __tablename__ = "survey_answers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    attempt_id = Column(
        Integer, ForeignKey("survey_attempts.id", ondelete="CASCADE"), nullable=False
    )
    question_id = Column(
        Integer, ForeignKey("survey_questions.id", ondelete="CASCADE"), nullable=False
    )
    option_id = Column(Integer, nullable=True)
    text_answer = Column(Text, nullable=True)
    is_correct = Column(Boolean, nullable=True)

    attempt = orm_relationship("SurveyAttempt", back_populates="answers")

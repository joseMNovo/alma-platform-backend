from pydantic import BaseModel, ConfigDict, field_validator
from typing import List, Optional
from datetime import datetime
from decimal import Decimal


VALID_OWNER_TYPES = {"capacitacion", "taller", "actividad", "general"}
VALID_SURVEY_KINDS = {"evaluacion", "opinion"}
VALID_QUESTION_KINDS = {"unica", "multiple", "vf", "texto"}

# Las de texto libre no se pueden corregir solas: quedan fuera de la nota.
AUTOGRADED_KINDS = {"unica", "multiple", "vf"}


# ── Opciones ───────────────────────────────────────────────────────────

class SurveyOptionIn(BaseModel):
    """Una opción tal como la carga el admin."""

    id: Optional[int] = None  # con id se actualiza, sin id se crea
    text: str
    is_correct: bool = False
    sort_order: int = 0

    @field_validator("text")
    @classmethod
    def texto_no_vacio(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("La opción no puede estar vacía")
        return v.strip()


class SurveyOptionAdminOut(BaseModel):
    """Vista del ADMIN: incluye is_correct."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    is_correct: bool
    sort_order: int


class SurveyOptionPublicOut(BaseModel):
    """Vista del ALUMNO: sin is_correct, a propósito.

    Es un schema aparte y no un campo opcional: un `Optional[bool]` que
    alguien olvide vaciar filtra la respuesta correcta y nadie se entera.
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    sort_order: int


# ── Preguntas ──────────────────────────────────────────────────────────

class SurveyQuestionIn(BaseModel):
    id: Optional[int] = None
    text: str
    kind: str = "unica"
    help: Optional[str] = None
    explanation: Optional[str] = None
    points: int = 1
    is_required: bool = True
    sort_order: int = 0
    options: List[SurveyOptionIn] = []

    @field_validator("text")
    @classmethod
    def texto_no_vacio(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("La pregunta no puede estar vacía")
        return v.strip()

    @field_validator("kind")
    @classmethod
    def kind_valido(cls, v: str) -> str:
        v = (v or "unica").strip().lower()
        if v not in VALID_QUESTION_KINDS:
            raise ValueError(
                f"Tipo de pregunta inválido. Válidos: {', '.join(sorted(VALID_QUESTION_KINDS))}"
            )
        return v

    @field_validator("points")
    @classmethod
    def puntaje_positivo(cls, v: int) -> int:
        return max(1, v or 1)


class SurveyQuestionAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    survey_id: int
    text: str
    kind: str
    help: Optional[str] = None
    explanation: Optional[str] = None
    points: int
    is_required: bool
    sort_order: int
    options: List[SurveyOptionAdminOut] = []


class SurveyQuestionPublicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    kind: str
    help: Optional[str] = None
    points: int
    is_required: bool
    sort_order: int
    options: List[SurveyOptionPublicOut] = []


# ── Encuesta ───────────────────────────────────────────────────────────

class SurveyBase(BaseModel):
    title: str
    description: Optional[str] = None
    owner_type: str = "capacitacion"
    owner_id: int = 0
    kind: str = "evaluacion"
    passing_score: int = 70
    max_attempts: Optional[int] = None
    shuffle_questions: bool = False
    show_answers: bool = True
    is_published: bool = False

    @field_validator("title")
    @classmethod
    def titulo_no_vacio(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El título no puede estar vacío")
        return v.strip()

    @field_validator("owner_type")
    @classmethod
    def owner_valido(cls, v: str) -> str:
        v = (v or "capacitacion").strip().lower()
        if v not in VALID_OWNER_TYPES:
            raise ValueError(f"Dueño inválido. Válidos: {', '.join(sorted(VALID_OWNER_TYPES))}")
        return v

    @field_validator("kind")
    @classmethod
    def kind_valido(cls, v: str) -> str:
        v = (v or "evaluacion").strip().lower()
        if v not in VALID_SURVEY_KINDS:
            raise ValueError(f"Tipo inválido. Válidos: {', '.join(sorted(VALID_SURVEY_KINDS))}")
        return v

    @field_validator("passing_score")
    @classmethod
    def porcentaje_valido(cls, v: int) -> int:
        if v is None:
            return 70
        if not 1 <= v <= 100:
            raise ValueError("El porcentaje para aprobar tiene que estar entre 1 y 100")
        return v

    @field_validator("max_attempts")
    @classmethod
    def intentos_validos(cls, v: Optional[int]) -> Optional[int]:
        if v is None or v <= 0:
            return None  # sin límite
        return v


class SurveyCreate(SurveyBase):
    created_by_volunteer_id: Optional[int] = None


class SurveyUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    kind: Optional[str] = None
    passing_score: Optional[int] = None
    max_attempts: Optional[int] = None
    shuffle_questions: Optional[bool] = None
    show_answers: Optional[bool] = None
    is_published: Optional[bool] = None

    @field_validator("kind")
    @classmethod
    def kind_valido(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in VALID_SURVEY_KINDS:
            raise ValueError(f"Tipo inválido. Válidos: {', '.join(sorted(VALID_SURVEY_KINDS))}")
        return v

    @field_validator("passing_score")
    @classmethod
    def porcentaje_valido(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return None
        if not 1 <= v <= 100:
            raise ValueError("El porcentaje para aprobar tiene que estar entre 1 y 100")
        return v


class SurveyAdminOut(SurveyBase):
    """Vista del admin: con las respuestas correctas."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_by_volunteer_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    questions: List[SurveyQuestionAdminOut] = []
    # Cuánta gente la rindió, para el listado
    attempts_count: int = 0
    passed_count: int = 0


class SurveyPublicOut(BaseModel):
    """Vista de quien responde: sin respuestas correctas y con su propia situación."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str] = None
    kind: str
    passing_score: int
    max_attempts: Optional[int] = None
    show_answers: bool
    questions: List[SurveyQuestionPublicOut] = []
    # Contexto de quien pregunta
    attempts_used: int = 0
    can_attempt: bool = True
    best_score: Optional[Decimal] = None
    passed: bool = False


# ── Rendir ─────────────────────────────────────────────────────────────

class SurveyAnswerIn(BaseModel):
    question_id: int
    # Lista aunque la pregunta sea de opción única: así el formato es uno solo
    # y el backend valida contra el tipo real de la pregunta.
    option_ids: List[int] = []
    text_answer: Optional[str] = None


class SurveySubmit(BaseModel):
    user_type: str
    user_id: int
    answers: List[SurveyAnswerIn] = []


class QuestionResult(BaseModel):
    question_id: int
    is_correct: Optional[bool] = None
    # Los dos solo si la encuesta tiene show_answers activado. La
    # justificación viaja ACÁ y no en la vista previa de la encuesta: antes de
    # contestar sería regalar la respuesta.
    correct_option_ids: Optional[List[int]] = None
    explanation: Optional[str] = None


class SurveyResult(BaseModel):
    attempt_id: int
    score: Decimal
    passed: bool
    passing_score: int
    total_questions: int
    correct_questions: int
    results: List[QuestionResult] = []
    # Se completa cuando aprobar dispara la emisión del certificado.
    certificate_code: Optional[str] = None


class AttemptOut(BaseModel):
    """Un intento, para la pantalla de resultados del admin."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    survey_id: int
    person_id: int
    person_name: Optional[str] = None
    person_email: Optional[str] = None
    score: Optional[Decimal] = None
    passed: bool
    submitted_at: Optional[datetime] = None

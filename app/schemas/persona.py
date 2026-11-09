from pydantic import BaseModel, ConfigDict, field_validator
from typing import Any, Optional
from datetime import date, datetime

from app.utils.text import normalize_name


# ── Personas (base de datos ALMA) ─────────────────────────────────────
# Reutiliza la tabla `participant_profiles` como registro maestro de
# personas. El login (participant_id) es opcional: existe solo cuando la
# persona se registró en la plataforma.

class PersonaBase(BaseModel):
    name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    cuit: Optional[str] = None
    # Se imprime en el certificado de finalización.
    dni: Optional[str] = None
    is_member: bool = False
    birth_date: Optional[date] = None
    address: Optional[str] = None
    floor: Optional[str] = None
    apartment: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    postal_code: Optional[str] = None
    phone: Optional[str] = None

    @field_validator("name", "last_name")
    @classmethod
    def _normalize_names(cls, v):
        return normalize_name(v)


class PersonaCreate(PersonaBase):
    source: Optional[str] = "manual"


class PersonaUpdate(BaseModel):
    name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    cuit: Optional[str] = None
    dni: Optional[str] = None
    is_member: Optional[bool] = None
    birth_date: Optional[date] = None
    address: Optional[str] = None
    floor: Optional[str] = None
    apartment: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = None
    postal_code: Optional[str] = None
    phone: Optional[str] = None

    # NO es una columna del perfil: vive en `voluntarios`. Entra por acá para
    # que editar a una persona desde Base de datos pueda tocar su voluntariado
    # sin un segundo request —y sin que una de las dos mitades pueda fallar
    # sola. El router la saca del dict antes de escribir el perfil y la aplica
    # a la ficha; si la persona no es voluntaria, se ignora.
    specialties: Optional[Any] = None

    @field_validator("name", "last_name")
    @classmethod
    def _normalize_names(cls, v):
        return normalize_name(v)


class Persona(PersonaBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    participant_id: Optional[int] = None   # presente => la persona tiene cuenta de login
    is_volunteer: bool = False             # rol voluntario (flag descriptivo)
    volunteer_id: Optional[int] = None     # presente => tiene ficha en `voluntarios`
    # Vive en `voluntarios`, no en el perfil: lo resuelve el listado con una
    # consulta aparte. Solo puede ser True si hay volunteer_id.
    is_admin: bool = False

    # ── El rol voluntario ───────────────────────────────────────────────
    # Viven en `voluntarios`. Vienen acá para que la ficha de una persona
    # pueda mostrar su sección de voluntariado sin que el frontend tenga que
    # ir a buscar a otro módulo. Son None cuando la persona no tiene ficha.
    #
    # Los resuelve el router con UNA consulta para toda la página: una por
    # fila sería N+1 sobre la lista entera de personas.
    volunteer_status: Optional[str] = None      # activo | pendiente | inactivo
    specialties: Optional[Any] = None           # JSON: lista de especialidades
    volunteer_since: Optional[date] = None      # `registration_date` del alta

    # ── La cuenta de participante ───────────────────────────────────────
    # Viven en `participants`. `participant_id` solo dice que la cuenta
    # existe; estos dos dicen si sirve para algo. Una cuenta inactiva es la de
    # alguien que pasó a ser voluntario/a; una sin verificar es la de alguien
    # que se registró y nunca entró al mail.
    account_active: Optional[bool] = None
    account_verified: Optional[bool] = None
    # Cuántos talleres/grupos/actividades tiene anotados. 0 si no tiene cuenta.
    enrollments_count: int = 0

    source: Optional[str] = None
    invited_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

from pydantic import BaseModel, ConfigDict, field_validator
from typing import List, Optional
from datetime import datetime


# ── Qué se puede escribir en un certificado ────────────────────────────
#
# El PDF se imprime con Helvetica, que dibuja el juego occidental (WinAnsi):
# entran tildes, ñ, «», símbolos de moneda. NO entran emojis ni alfabetos no
# latinos: saldrían como cuadraditos o romperían la generación.
#
# Los largos no son caprichosos: la hoja tiene un alto fijo y la firma va
# anclada abajo. Un texto sin tope se le monta encima.
LARGOS_MAXIMOS = {
    "heading": 80,
    "body": 600,
    "legal_note": 300,
    "signature_name": 60,
    "signature_role": 60,
}


def _imprimible(caracter: str) -> bool:
    if caracter in "\n\t":
        return True
    if ord(caracter) < 32:  # caracteres de control: basura invisible al pegar
        return False
    try:
        caracter.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def limpiar_texto_pdf(valor: Optional[str], campo: str, *, una_linea: bool = False) -> Optional[str]:
    """Valida un texto que va a terminar impreso. Devuelve el valor recortado.

    Rechaza en vez de limpiar por lo bajo: si alguien pega un emoji en un
    texto legal, tiene que enterarse, no encontrarse el certificado sin él.
    """
    if valor is None:
        return None

    valor = valor.strip()
    if not valor:
        return None

    if una_linea and ("\n" in valor or "\r" in valor):
        raise ValueError("El título tiene que ser una sola línea")

    prohibidos = sorted({c for c in valor if not _imprimible(c)})
    if prohibidos:
        muestra = " ".join(repr(c) if ord(c) < 32 else c for c in prohibidos[:8])
        raise ValueError(
            f"El certificado no puede imprimir estos caracteres: {muestra}. "
            "La tipografía del PDF no los dibuja (los emojis, por ejemplo)."
        )

    maximo = LARGOS_MAXIMOS.get(campo)
    if maximo and len(valor) > maximo:
        raise ValueError(
            f"El texto es muy largo ({len(valor)} caracteres, máximo {maximo}). "
            "No entraría en la hoja sin taparle la firma."
        )

    return valor


# Marcadores que se reemplazan al generar el PDF. La UI los muestra como
# botones con nombre humano ("Nombre y apellido"), nunca con esta clave.
#
# nombre / apellido van por separado además de nombre_completo porque hay
# redacciones que necesitan solo uno ("Estimada María,").
PLACEHOLDERS = (
    "nombre_completo", "nombre", "apellido",
    "dni", "capacitacion", "fecha", "horas", "codigo",
)


class CertificateTemplateBase(BaseModel):
    name: str
    is_default: bool = False
    heading: str = "Certificado"
    body: Optional[str] = None
    legal_note: Optional[str] = None
    signature_name: Optional[str] = None
    signature_role: Optional[str] = None
    signature_file_guid: Optional[str] = None
    logo_file_guid: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El nombre de la plantilla no puede estar vacío")
        return v.strip()

    @field_validator("heading")
    @classmethod
    def heading_valido(cls, v: str) -> str:
        return limpiar_texto_pdf(v, "heading", una_linea=True) or "Certificado"

    @field_validator("body")
    @classmethod
    def body_valido(cls, v: Optional[str]) -> Optional[str]:
        return limpiar_texto_pdf(v, "body")

    @field_validator("legal_note")
    @classmethod
    def legal_valida(cls, v: Optional[str]) -> Optional[str]:
        return limpiar_texto_pdf(v, "legal_note")

    @field_validator("signature_name")
    @classmethod
    def firma_valida(cls, v: Optional[str]) -> Optional[str]:
        return limpiar_texto_pdf(v, "signature_name", una_linea=True)

    @field_validator("signature_role")
    @classmethod
    def cargo_valido(cls, v: Optional[str]) -> Optional[str]:
        return limpiar_texto_pdf(v, "signature_role", una_linea=True)


class CertificateTemplateCreate(CertificateTemplateBase):
    created_by_volunteer_id: Optional[int] = None


class CertificateTemplateUpdate(BaseModel):
    name: Optional[str] = None
    is_default: Optional[bool] = None
    heading: Optional[str] = None
    body: Optional[str] = None
    legal_note: Optional[str] = None
    signature_name: Optional[str] = None
    signature_role: Optional[str] = None
    signature_file_guid: Optional[str] = None
    logo_file_guid: Optional[str] = None

    # Las mismas reglas que en el alta: editar no puede ser la puerta de
    # atrás para meter lo que el alta rechaza.
    @field_validator("heading")
    @classmethod
    def heading_valido(cls, v: Optional[str]) -> Optional[str]:
        return limpiar_texto_pdf(v, "heading", una_linea=True)

    @field_validator("body")
    @classmethod
    def body_valido(cls, v: Optional[str]) -> Optional[str]:
        return limpiar_texto_pdf(v, "body")

    @field_validator("legal_note")
    @classmethod
    def legal_valida(cls, v: Optional[str]) -> Optional[str]:
        return limpiar_texto_pdf(v, "legal_note")

    @field_validator("signature_name")
    @classmethod
    def firma_valida(cls, v: Optional[str]) -> Optional[str]:
        return limpiar_texto_pdf(v, "signature_name", una_linea=True)

    @field_validator("signature_role")
    @classmethod
    def cargo_valido(cls, v: Optional[str]) -> Optional[str]:
        return limpiar_texto_pdf(v, "signature_role", una_linea=True)


class CertificateTemplateOut(CertificateTemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_by_volunteer_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CertificateSample(BaseModel):
    """Pedido de PDF de muestra.

    Lleva la plantilla ENTERA en el cuerpo, no un id: así se previsualiza lo
    que la persona está escribiendo en ese momento, sin obligarla a guardar
    para ver cómo queda.

    Los datos de ejemplo son opcionales; si no vienen, se usan los de prueba.
    """

    heading: str = "Certificado"
    body: Optional[str] = None
    legal_note: Optional[str] = None
    signature_name: Optional[str] = None
    signature_role: Optional[str] = None
    signature_file_guid: Optional[str] = None
    logo_file_guid: Optional[str] = None

    nombre_completo: str = "María Fernández"
    nombre: str = "María"
    apellido: str = "Fernández"
    dni: str = "12.345.678"
    capacitacion: str = "Acompañamiento en Alzheimer"
    fecha: Optional[str] = None  # None = hoy, formateada en es-AR
    horas: str = "8 horas"
    codigo: str = "MUESTRA-0000"


# ── Certificados emitidos ──────────────────────────────────────────────

class CertificateOut(BaseModel):
    """Un certificado emitido, para las pantallas internas."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    person_id: int
    training_id: Optional[int] = None
    holder_name: str
    holder_dni: Optional[str] = None
    training_title: Optional[str] = None
    hours: Optional[str] = None
    issued_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class CertificateVerification(BaseModel):
    """Lo que ve CUALQUIERA en la página pública de verificación.

    Nombre, capacitación y fecha: sin eso no se puede confirmar que el
    certificado sea de quien te lo mostró, que es para lo único que existe
    esta página. El DNI NO se expone: no hace falta para verificar y es un
    dato sensible.
    """

    valido: bool
    code: str
    holder_name: Optional[str] = None
    training_title: Optional[str] = None
    issued_at: Optional[datetime] = None
    hours: Optional[str] = None
    revoked: bool = False
    revoked_reason: Optional[str] = None


class CertificateIssue(BaseModel):
    """Emisión a mano (cursos presenciales, casos sin evaluación)."""

    person_id: int
    training_id: Optional[int] = None
    issued_by_volunteer_id: Optional[int] = None


class CertificateBulkIssue(BaseModel):
    """Emisión y envío masivos desde la pantalla de entrega."""

    person_ids: List[int]
    training_id: int
    issued_by_volunteer_id: Optional[int] = None
    enviar: bool = False


class DeliveryRow(BaseModel):
    """Una persona en la pantalla de entrega, con todo su estado junto.

    Es lo que permite filtrar por «aprobaron y todavía no tienen certificado»
    sin que la pantalla tenga que cruzar tres listas a mano.
    """

    person_id: int
    name: Optional[str] = None
    email: Optional[str] = None
    has_access: bool = False
    items_total: int = 0
    items_completed: int = 0
    content_done: bool = False
    survey_passed: Optional[bool] = None
    best_score: Optional[float] = None
    certificate_code: Optional[str] = None
    issued_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None

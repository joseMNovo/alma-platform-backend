"""
app/services/certificate_service.py — ALMA Backend — Emisión de certificados
=============================================================================
Emitir es CONGELAR: se resuelve el texto de la plantilla con los datos de la
persona y se guarda el resultado, no una referencia.

De ahí en más ese certificado no cambia nunca, aunque después se corrija la
redacción, se renombre la capacitación o la persona cambie de apellido. Es la
misma decisión que `person_payments.concept_label`: un comprobante viejo no
se actualiza solo.
"""
import secrets
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models.certificate import Certificate, CertificateTemplate
from app.models.participant import ParticipantProfile
from app.models.training import Training
from app.services import certificate_pdf
from app.utils.logger import log_info

# Alfabeto sin caracteres que se confundan al leerlos de un papel o dictarlos
# por teléfono: no van I, O, 0, 1.
_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def nuevo_codigo(db: Session) -> str:
    """Código único con formato ALMA-XXXX-XXXX.

    32^8 combinaciones: es imposible adivinar uno probando, que es lo que
    permite que la página de verificación sea pública sin exponer a nadie.
    """
    for _ in range(20):
        cuerpo = "".join(secrets.choice(_ALFABETO) for _ in range(8))
        codigo = f"ALMA-{cuerpo[:4]}-{cuerpo[4:]}"
        if not db.query(Certificate.id).filter(Certificate.code == codigo).first():
            return codigo
    # 20 colisiones seguidas con este espacio de códigos no pasa ni por error;
    # si pasara, es preferible fallar que entregar un código repetido.
    raise RuntimeError("No se pudo generar un código de certificado único")


def plantilla_para(db: Session, training: Optional[Training]) -> Optional[CertificateTemplate]:
    """La plantilla de esa capacitación, o la predeterminada."""
    if training and training.certificate_template_id:
        propia = (
            db.query(CertificateTemplate)
            .filter(CertificateTemplate.id == training.certificate_template_id)
            .first()
        )
        if propia:
            return propia
    return (
        db.query(CertificateTemplate)
        .filter(CertificateTemplate.is_default.is_(True))
        .first()
    )


def valores_de(persona: ParticipantProfile, training: Optional[Training], codigo: str) -> dict:
    """Los datos que reemplazan a los marcadores de la plantilla."""
    nombre = (persona.name or "").strip()
    apellido = (persona.last_name or "").strip()
    return {
        "nombre_completo": f"{nombre} {apellido}".strip(),
        "nombre": nombre,
        "apellido": apellido,
        "dni": (persona.dni or "").strip(),
        "capacitacion": (training.title if training else "") or "",
        "fecha": certificate_pdf.format_date_es(),
        "horas": (training.certificate_hours if training else "") or "",
        "codigo": codigo,
    }


def emitir(
    db: Session,
    *,
    person_id: int,
    training: Optional[Training],
    survey_attempt_id: Optional[int] = None,
    issued_by_volunteer_id: Optional[int] = None,
    commit: bool = True,
) -> Certificate:
    """Emite (o re-emite) el certificado de una persona para una capacitación.

    Re-emitir ACTUALIZA el que ya existe en vez de crear un segundo: una
    persona tiene un solo certificado por curso. El código se conserva, para
    que el que ya circuló siga verificando.

    Con commit=False la transacción queda abierta para que el llamador cierre
    todo junto (rendir la evaluación y emitir tienen que entrar o fallar
    juntos).
    """
    persona = (
        db.query(ParticipantProfile).filter(ParticipantProfile.id == person_id).first()
    )
    if not persona:
        raise ValueError("La persona no existe")

    titular = f"{(persona.name or '').strip()} {(persona.last_name or '').strip()}".strip()
    if not titular:
        raise ValueError("La persona no tiene nombre cargado: el certificado no puede salir sin titular")

    existente = (
        db.query(Certificate)
        .filter(
            Certificate.person_id == person_id,
            Certificate.training_id == (training.id if training else None),
        )
        .first()
    )

    codigo = existente.code if existente else nuevo_codigo(db)
    plantilla = plantilla_para(db, training)
    valores = valores_de(persona, training, codigo)

    # El texto se resuelve ACÁ y se guarda ya hecho. De esto se trata congelar.
    cuerpo = certificate_pdf.render_text(plantilla.body if plantilla else "", valores)
    pie = certificate_pdf.render_text(plantilla.legal_note if plantilla else "", valores)
    encabezado = certificate_pdf.render_text(
        plantilla.heading if plantilla else "Certificado", valores
    )

    certificado = existente or Certificate(code=codigo, person_id=person_id)
    certificado.training_id = training.id if training else None
    certificado.survey_attempt_id = survey_attempt_id
    certificado.holder_name = titular
    certificado.holder_dni = valores["dni"] or None
    certificado.training_title = valores["capacitacion"] or None
    certificado.hours = valores["horas"] or None
    certificado.heading = encabezado or "Certificado"
    certificado.body_text = cuerpo or None
    certificado.legal_note = pie or None
    certificado.signature_name = plantilla.signature_name if plantilla else None
    certificado.signature_role = plantilla.signature_role if plantilla else None
    certificado.issued_at = datetime.now()
    certificado.issued_by_volunteer_id = issued_by_volunteer_id
    # Re-emitir invalida la anulación previa y obliga a volver a enviarlo.
    certificado.revoked_at = None
    certificado.revoked_reason = None
    certificado.sent_at = None

    if not existente:
        db.add(certificado)

    if commit:
        db.commit()
        db.refresh(certificado)

    log_info(
        "Certificado emitido",
        module="certificados",
        action="reemitir" if existente else "emitir",
        user=issued_by_volunteer_id,
        meta={"code": codigo, "person_id": person_id, "training_id": certificado.training_id},
    )
    return certificado


def pdf_de(db: Session, certificado: Certificate) -> bytes:
    """Regenera el PDF desde el texto congelado.

    No se reemplaza ningún marcador: el texto ya está resuelto. Lo único que
    se busca en la base es la firma escaneada y el logo, que son imágenes y
    no cambian lo que el certificado dice.
    """
    from app.models.file import File as FileModel
    from app.services import file_storage

    def bytes_de(guid: Optional[str]) -> Optional[bytes]:
        if not guid:
            return None
        archivo = db.query(FileModel).filter(FileModel.guid == guid).first()
        if not archivo:
            return None
        try:
            return file_storage.read_bytes(guid, archivo.extension)
        except (FileNotFoundError, OSError):
            return None

    plantilla = (
        db.query(CertificateTemplate)
        .filter(CertificateTemplate.is_default.is_(True))
        .first()
    )

    return certificate_pdf.render_certificate(
        heading=certificado.heading or "Certificado",
        body=certificado.body_text,
        legal_note=certificado.legal_note,
        signature_name=certificado.signature_name,
        signature_role=certificado.signature_role,
        logo_bytes=bytes_de(plantilla.logo_file_guid if plantilla else None),
        signature_bytes=bytes_de(plantilla.signature_file_guid if plantilla else None),
        # El texto ya está resuelto; esto es solo para el guardarraíl del
        # titular y para el título del PDF.
        values={"nombre_completo": certificado.holder_name},
    )


def nombre_archivo(certificado: Certificate) -> str:
    """Nombre del PDF al descargarlo. Sin tildes ni espacios: viaja por mail
    y por sistemas de archivos que no siempre los toleran."""
    base = f"certificado-{certificado.code}".lower()
    return f"{base}.pdf"

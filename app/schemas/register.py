from pydantic import BaseModel, field_validator, EmailStr
from typing import Optional


class RegisterRequest(BaseModel):
    """Alta de participante. Ya NO se pide un token compartido: la identidad se
    prueba verificando el email real, que es lo que además deja a ALMA con una
    casilla válida para comunicarse."""

    email: EmailStr
    pin_hash: str    # bcrypt hash generado por el proxy Next.js
    # El nombre es OBLIGATORIO (ver el validador de abajo). El apellido no:
    # con el nombre alcanza para reconocer a alguien en pantalla, y pedir de
    # más en un formulario de alta cuesta altas.
    name: Optional[str] = None
    last_name: Optional[str] = None
    # Qué capacitación venía a comprar, si el alta salió del wizard. Se guarda
    # como intención: es lo que después permite recordarle que pague sin
    # escribirle a quien se registró solo para mirar.
    training_id: Optional[int] = None
    # Ruta interna a la que volver después de verificar el mail (la compra).
    # El frontend valida que sea interna antes de usarla.
    next: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _nombre_obligatorio(cls, v):
        """El nombre no puede faltar, aunque el campo siga siendo Optional.

        Optional queda por las altas viejas, pero una nueva sin nombre no
        sirve: la persona aparece en Accesos identificada apenas por su mail,
        y la emisión del certificado corta con "El certificado necesita el
        nombre de la persona".

        Se valida acá y no solo en el formulario porque la validación del
        navegador la saltea cualquiera que llame a la API — y de hecho fue una
        rama del propio formulario la que dejó pasar altas sin nombre: la de
        participante pedía únicamente email y PIN.
        """
        v = (v or "").strip()
        if not v:
            raise ValueError("Necesitamos tu nombre para poder identificarte")
        return v


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class RegisterResponse(BaseModel):
    id: int
    email: str
    role: str            # "voluntario" | "participante"
    email_verified: bool = False
    # Para que el frontend muestre "revisá tu correo" con el dato correcto.
    verification_sent_to: Optional[str] = None

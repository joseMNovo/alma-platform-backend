from pydantic import BaseModel, EmailStr
from typing import Optional


class RegisterRequest(BaseModel):
    """Alta de participante. Ya NO se pide un token compartido: la identidad se
    prueba verificando el email real, que es lo que además deja a ALMA con una
    casilla válida para comunicarse."""

    email: EmailStr
    pin_hash: str    # bcrypt hash generado por el proxy Next.js
    # Nombre y apellido: los pide la compra exprés de una capacitación porque
    # SIN ELLOS NO SE PUEDE EMITIR EL CERTIFICADO. El registro común los deja
    # vacíos y se completan después desde Mi perfil.
    name: Optional[str] = None
    last_name: Optional[str] = None
    # Ruta interna a la que volver después de verificar el mail (la compra).
    # El frontend valida que sea interna antes de usarla.
    next: Optional[str] = None


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class RegisterResponse(BaseModel):
    id: int
    email: str
    role: str            # "voluntario" | "participante"
    email_verified: bool = False
    # Para que el frontend muestre "revisá tu correo" con el dato correcto.
    verification_sent_to: Optional[str] = None

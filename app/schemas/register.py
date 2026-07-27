from pydantic import BaseModel, EmailStr
from typing import Optional


class RegisterRequest(BaseModel):
    """Alta de participante. Ya NO se pide un token compartido: la identidad se
    prueba verificando el email real, que es lo que además deja a ALMA con una
    casilla válida para comunicarse."""

    email: EmailStr
    pin_hash: str    # bcrypt hash generado por el proxy Next.js


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class RegisterResponse(BaseModel):
    id: int
    email: str
    role: str            # "voluntario" | "participante"
    email_verified: bool = False
    # Para que el frontend muestre "revisá tu correo" con el dato correcto.
    verification_sent_to: Optional[str] = None

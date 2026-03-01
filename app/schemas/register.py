from pydantic import BaseModel, EmailStr, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    pin_hash: str    # bcrypt hash generado por el proxy Next.js
    alma_token: str  # 6 dígitos, validado contra settings

    @field_validator('alma_token')
    @classmethod
    def token_6_digits(cls, v):
        if not v.isdigit() or len(v) != 6:
            raise ValueError('Token debe tener exactamente 6 dígitos')
        return v


class RegisterResponse(BaseModel):
    id: int
    email: str
    role: str   # "voluntario" | "participante"

from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from config import settings
from app.database import get_db
from app.models.auth import AuthUser as AuthUserModel
from app.models.voluntario import Voluntario as VoluntarioModel
from app.models.participant import Participant as ParticipantModel
from app.schemas.register import RegisterRequest, RegisterResponse

router = APIRouter()


@router.post("/voluntario", response_model=RegisterResponse, status_code=201)
def register_voluntario(data: RegisterRequest, db: Session = Depends(get_db)):
    # 1. Validar token ALMA
    if data.alma_token != settings.ALMA_REGISTER_TOKEN:
        raise HTTPException(status_code=400, detail="Token ALMA inválido")

    # 2. Verificar unicidad de email
    existing = db.query(AuthUserModel).filter(AuthUserModel.email == data.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="El email ya está registrado")

    # 3. Crear AuthUser
    auth_user = AuthUserModel(
        email=data.email,
        password_hash=data.pin_hash,
        email_verified=True,
        is_volunteer=True,
        is_active=True,
    )
    db.add(auth_user)
    db.flush()  # Obtener auth_user.id

    # 4. Crear Voluntario
    voluntario = VoluntarioModel(
        email=data.email,
        pin_hash=data.pin_hash,
        name="",
        last_name="",
        status="activo",
        is_admin=False,
        registration_date=date.today(),
        auth_user_id=auth_user.id,
    )
    db.add(voluntario)
    db.flush()  # Obtener voluntario.id

    # 5. Enlazar auth_user → voluntario
    auth_user.volunteer_id = voluntario.id
    db.commit()

    return RegisterResponse(id=voluntario.id, email=data.email, role="voluntario")


@router.post("/participante", response_model=RegisterResponse, status_code=201)
def register_participante(data: RegisterRequest, db: Session = Depends(get_db)):
    # 1. Validar token ALMA
    if data.alma_token != settings.ALMA_REGISTER_TOKEN:
        raise HTTPException(status_code=400, detail="Token ALMA inválido")

    # 2. Verificar unicidad de email
    existing = db.query(AuthUserModel).filter(AuthUserModel.email == data.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="El email ya está registrado")

    # 3. Crear AuthUser
    auth_user = AuthUserModel(
        email=data.email,
        password_hash=data.pin_hash,
        email_verified=True,
        is_volunteer=False,
        is_active=True,
    )
    db.add(auth_user)
    db.flush()  # Obtener auth_user.id

    # 4. Crear Participant
    participant = ParticipantModel(
        email=data.email,
        pin_hash=data.pin_hash,
        is_active=True,
    )
    db.add(participant)
    db.commit()
    db.refresh(participant)

    return RegisterResponse(id=participant.id, email=data.email, role="participante")

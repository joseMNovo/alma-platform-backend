from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.participant import (
    Participant as ParticipantModel,
    ParticipantProfile as ProfileModel,
    ParticipantProgramEnrollment as EnrollmentModel,
)
from app.schemas.participant import (
    Participant, ParticipantCreate, ParticipantUpdate,
    ParticipantAuth,
    ParticipantProfile, ParticipantProfileCreate, ParticipantProfileUpdate,
    ParticipantProgramEnrollment, ParticipantProgramEnrollmentCreate,
)

router = APIRouter()


# ── Participants ──────────────────────────────────────────────────────

@router.get("/auth/{email}", response_model=ParticipantAuth)
def get_participant_auth(email: str, db: Session = Depends(get_db)):
    """Endpoint interno para autenticación — devuelve pin_hash."""
    p = db.query(ParticipantModel).filter(ParticipantModel.email == email).first()
    if not p:
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    return p


@router.get("/", response_model=List[Participant])
def list_participants(
    skip: int = 0,
    limit: int = 100,
    is_active: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(ParticipantModel)
    if is_active is not None:
        q = q.filter(ParticipantModel.is_active == is_active)
    return q.offset(skip).limit(limit).all()


@router.get("/{id}", response_model=Participant)
def get_participant(id: int, db: Session = Depends(get_db)):
    p = db.query(ParticipantModel).filter(ParticipantModel.id == id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    return p


@router.get("/by-email/{email}", response_model=Participant)
def get_participant_by_email(email: str, db: Session = Depends(get_db)):
    p = db.query(ParticipantModel).filter(ParticipantModel.email == email).first()
    if not p:
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    return p


@router.post("/", response_model=Participant, status_code=201)
def create_participant(data: ParticipantCreate, db: Session = Depends(get_db)):
    p = ParticipantModel(**data.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.put("/{id}", response_model=Participant)
def update_participant(id: int, data: ParticipantUpdate, db: Session = Depends(get_db)):
    p = db.query(ParticipantModel).filter(ParticipantModel.id == id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(p, key, value)
    db.commit()
    db.refresh(p)
    return p


@router.delete("/{id}", status_code=204)
def delete_participant(id: int, db: Session = Depends(get_db)):
    p = db.query(ParticipantModel).filter(ParticipantModel.id == id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    db.delete(p)
    db.commit()


# ── Participant Profiles ──────────────────────────────────────────────

@router.get("/{id}/profile", response_model=ParticipantProfile)
def get_profile(id: int, db: Session = Depends(get_db)):
    prof = db.query(ProfileModel).filter(ProfileModel.participant_id == id).first()
    if not prof:
        raise HTTPException(status_code=404, detail="Perfil no encontrado")
    return prof


@router.post("/{id}/profile", response_model=ParticipantProfile, status_code=201)
def create_profile(id: int, data: ParticipantProfileCreate, db: Session = Depends(get_db)):
    if not db.query(ParticipantModel).filter(ParticipantModel.id == id).first():
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    prof = ProfileModel(**{**data.model_dump(), "participant_id": id})
    db.add(prof)
    db.commit()
    db.refresh(prof)
    return prof


@router.put("/{id}/profile", response_model=ParticipantProfile)
def update_profile(id: int, data: ParticipantProfileUpdate, db: Session = Depends(get_db)):
    prof = db.query(ProfileModel).filter(ProfileModel.participant_id == id).first()
    if not prof:
        raise HTTPException(status_code=404, detail="Perfil no encontrado")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(prof, key, value)
    db.commit()
    db.refresh(prof)
    return prof


# ── Participant Program Enrollments ───────────────────────────────────

@router.get("/{id}/enrollments", response_model=List[ParticipantProgramEnrollment])
def list_enrollments(id: int, db: Session = Depends(get_db)):
    return db.query(EnrollmentModel).filter(EnrollmentModel.participant_id == id).all()


@router.post("/{id}/enrollments", response_model=ParticipantProgramEnrollment, status_code=201)
def create_enrollment(id: int, data: ParticipantProgramEnrollmentCreate, db: Session = Depends(get_db)):
    if not db.query(ParticipantModel).filter(ParticipantModel.id == id).first():
        raise HTTPException(status_code=404, detail="Participante no encontrado")
    e = EnrollmentModel(**{**data.model_dump(), "participant_id": id})
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


@router.delete("/{id}/enrollments/{enrollment_id}", status_code=204)
def delete_enrollment(id: int, enrollment_id: int, db: Session = Depends(get_db)):
    e = db.query(EnrollmentModel).filter(
        EnrollmentModel.id == enrollment_id,
        EnrollmentModel.participant_id == id,
    ).first()
    if not e:
        raise HTTPException(status_code=404, detail="Inscripción no encontrada")
    db.delete(e)
    db.commit()

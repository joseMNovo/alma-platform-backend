from pydantic import BaseModel, ConfigDict
from typing import Optional, Literal
from datetime import datetime


class VolunteerVerificationTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    volunteer_id: int
    token_hash: str
    expires_at: datetime
    used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class ParticipantVerificationTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    participant_id: int
    token_hash: str
    expires_at: datetime
    used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class PinResetTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_type: Literal["volunteer", "participant"]
    user_id: int
    token_hash: str
    expires_at: datetime
    used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class VerifyEmailRequest(BaseModel):
    token: str


class RequestPinResetRequest(BaseModel):
    email: str
    user_type: Literal["volunteer", "participant"]


class ConfirmPinResetRequest(BaseModel):
    token: str
    user_type: Literal["volunteer", "participant"]
    new_pin_hash: str

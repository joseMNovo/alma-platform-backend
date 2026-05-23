from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Literal
from datetime import datetime


class SendEmailRequest(BaseModel):
    to: List[str]
    cc: Optional[List[str]] = None
    bcc: Optional[List[str]] = None
    subject: str
    template: Optional[str] = None
    variables: Optional[dict] = None
    body: Optional[str] = None
    sent_by_volunteer_id: Optional[int] = None


class EmailLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sent_at: Optional[datetime] = None
    from_address: str
    to_addresses: list
    cc: Optional[list] = None
    bcc: Optional[list] = None
    subject: str
    template: Optional[str] = None
    status: Literal["sent", "failed"]
    resend_id: Optional[str] = None
    sent_by_volunteer_id: Optional[int] = None
    error_message: Optional[str] = None
    variables: Optional[dict] = None

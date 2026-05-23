from sqlalchemy import Column, Integer, String, Text, JSON, Enum, TIMESTAMP
from app.database import Base


class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sent_at = Column(TIMESTAMP)
    from_address = Column(String(255), nullable=False)
    to_addresses = Column(JSON, nullable=False)
    cc = Column(JSON, nullable=True)
    bcc = Column(JSON, nullable=True)
    subject = Column(String(500), nullable=False)
    template = Column(String(100), nullable=True)
    status = Column(Enum("sent", "failed"), nullable=False, default="sent")
    resend_id = Column(String(255), nullable=True)
    sent_by_volunteer_id = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    variables = Column(JSON, nullable=True)

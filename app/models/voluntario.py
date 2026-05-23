from sqlalchemy import Column, Integer, String, Date, Text, Boolean, JSON, TIMESTAMP, DateTime
from app.database import Base


class Voluntario(Base):
    __tablename__ = "voluntarios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    last_name = Column(String(100))
    age = Column(Integer)
    gender = Column(String(20))
    photo = Column(Text)
    phone = Column(String(50))
    email = Column(String(150))
    registration_date = Column(Date, nullable=False)
    birth_date = Column(Date)
    status = Column(String(20), nullable=False, default="pendiente")
    specialties = Column(JSON)
    is_admin = Column(Boolean, nullable=False, default=False)
    pin_hash = Column(String(255))
    email_verified = Column(Boolean, nullable=False, default=False)
    email_verified_at = Column(DateTime, nullable=True)
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

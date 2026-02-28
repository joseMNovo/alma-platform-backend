from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, TIMESTAMP, ForeignKey
from app.database import Base


class Pendiente(Base):
    __tablename__ = "pendientes"

    id = Column(String(36), primary_key=True)
    description = Column(Text, nullable=False)
    assigned_volunteer_id = Column(String(20))
    completed = Column(Boolean, nullable=False, default=False)
    created_date = Column(DateTime, nullable=False)
    completed_date = Column(DateTime)
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)


class PendingItem(Base):
    __tablename__ = "pending_items"

    id = Column(String(36), primary_key=True)
    pending_id = Column(String(36), ForeignKey("pendientes.id", ondelete="CASCADE"), nullable=False)
    description = Column(Text, nullable=False)
    assigned_volunteer_id = Column(String(20))
    completed = Column(Boolean, nullable=False, default=False)
    created_date = Column(DateTime, nullable=False)
    completed_date = Column(DateTime)
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class Idea(Base):
    __tablename__ = "ideas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    category = Column(String(100), nullable=True)
    created_by_volunteer_id = Column(Integer, ForeignKey("voluntarios.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    created_by = relationship("Voluntario", foreign_keys=[created_by_volunteer_id], lazy="joined")
    comments = relationship("IdeaComment", back_populates="idea", cascade="all, delete-orphan", lazy="dynamic")

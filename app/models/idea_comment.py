from sqlalchemy import Column, Integer, Text, TIMESTAMP, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class IdeaComment(Base):
    __tablename__ = "idea_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    idea_id = Column(Integer, ForeignKey("ideas.id", ondelete="CASCADE"), nullable=False)
    volunteer_id = Column(Integer, ForeignKey("voluntarios.id", ondelete="CASCADE"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    idea = relationship("Idea", foreign_keys=[idea_id], back_populates="comments")
    volunteer = relationship("Voluntario", foreign_keys=[volunteer_id], lazy="joined")

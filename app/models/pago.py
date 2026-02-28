from sqlalchemy import Column, Integer, String, Date, TIMESTAMP, ForeignKey
from app.database import Base


class Pago(Base):
    __tablename__ = "pagos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("voluntarios.id", ondelete="CASCADE"), nullable=False)
    concept = Column(String(200), nullable=False)
    amount = Column(Integer, nullable=False)
    due_date = Column(Date, nullable=False)
    payment_method = Column(String(20))
    status = Column(String(20), nullable=False, default="pendiente")
    payment_date = Column(Date)
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

from sqlalchemy import Column, Integer, String, Date, Numeric, TIMESTAMP, ForeignKey
from app.database import Base


class Inventario(Base):
    __tablename__ = "inventario"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    category = Column(String(100))
    quantity = Column(Integer, nullable=False, default=0)
    minimum_stock = Column(Integer, nullable=False, default=1)
    price = Column(Numeric(10, 2), nullable=False, default=0)
    supplier = Column(String(200))
    assigned_volunteer_id = Column(Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True)
    entry_date = Column(Date, nullable=False)
    created_at = Column(TIMESTAMP)
    updated_at = Column(TIMESTAMP)

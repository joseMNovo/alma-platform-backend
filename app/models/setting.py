from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, ForeignKey, func

from app.database import Base


class AppSetting(Base):
    """Ajuste general editable desde la app. Clave/valor.

    SOLO configuración: cosas que una persona decide una vez y cambia de vez
    en cuando (el link de pago, un texto institucional). Nunca datos del
    negocio — esos van en su propia tabla, con sus tipos y sus índices.
    """

    __tablename__ = "app_settings"

    setting_key = Column(String(60), primary_key=True)
    setting_value = Column(Text, nullable=True)
    updated_by_volunteer_id = Column(
        Integer, ForeignKey("voluntarios.id", ondelete="SET NULL"), nullable=True
    )
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

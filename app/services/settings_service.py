"""
app/services/settings_service.py — ALMA Backend — Ajustes generales
====================================================================
Lectura y escritura de `app_settings` (clave/valor).

Vive en services y no en el router porque lo leen OTROS módulos: el link de
pago general lo necesita el serializador de capacitaciones, y un router
importando a otro router termina en dependencias cruzadas.

SOLO configuración: cosas que una persona decide una vez y cambia de vez en
cuando. Los datos del negocio van en su propia tabla, con tipos e índices.
"""
from typing import Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.models.setting import AppSetting
from app.schemas.training import clean_payment_url

# Link de pago general de capacitaciones: el que se usa cuando la
# capacitación no tiene uno propio (trainings.payment_url).
PAYMENT_URL_KEY = "capacitaciones_payment_url"

# Claves permitidas y cómo se limpia cada valor antes de guardarlo. Sin esta
# lista, un error de tipeo crearía una fila fantasma que nadie vuelve a mirar.
SETTING_KEYS: Dict[str, Optional[Callable[[Optional[str]], Optional[str]]]] = {
    PAYMENT_URL_KEY: clean_payment_url,
}


def get(db: Session, key: str) -> Optional[str]:
    """Valor de un ajuste, o None si no está cargado o quedó vacío."""
    row = db.query(AppSetting).filter(AppSetting.setting_key == key).first()
    value = (row.setting_value or "").strip() if row else ""
    return value or None


def all_settings(db: Session) -> Dict[str, Optional[str]]:
    """Todas las claves conocidas. Las que no están cargadas vienen en None,
    no ausentes: así el frontend no tiene que adivinar si falta o está vacía."""
    stored = {row.setting_key: row.setting_value for row in db.query(AppSetting).all()}
    return {key: (stored.get(key) or None) for key in SETTING_KEYS}


def set_value(
    db: Session,
    key: str,
    value: Optional[str],
    *,
    actor_id: Optional[int] = None,
) -> Optional[str]:
    """Guarda un ajuste ya validado y devuelve el valor limpio.

    Un valor vacío deja el ajuste sin configurar, pero NO borra la fila: el
    `updated_at` es lo que después explica por qué algo dejó de aparecer.

    Lanza ValueError si la clave no existe o el valor no pasa su validación.
    """
    if key not in SETTING_KEYS:
        raise ValueError(f"Ajuste desconocido: {key}")

    cleaner = SETTING_KEYS[key]
    clean = cleaner(value) if cleaner else ((value or "").strip() or None)

    row = db.query(AppSetting).filter(AppSetting.setting_key == key).first()
    if not row:
        row = AppSetting(setting_key=key)
        db.add(row)

    row.setting_value = clean
    row.updated_by_volunteer_id = actor_id
    db.commit()
    return clean

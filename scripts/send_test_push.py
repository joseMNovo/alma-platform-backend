"""
Dispara una notificación de prueba a un usuario (crea la fila in-app + push).
Sirve para testear el sistema antes de tener la UI de admin (Fase 2).

Correr desde la RAÍZ del backend, con el venv activo y el .env cargado:
    python scripts/send_test_push.py <user_type> <user_id>

Ejemplos:
    python scripts/send_test_push.py voluntario 1
    python scripts/send_test_push.py participante 5

<user_id> es el id del usuario en su tabla (voluntarios o participants).
Para que llegue el push, ese usuario debe haber activado notificaciones en
al menos un dispositivo (tener filas en push_subscriptions).
"""
import sys

from app.database import SessionLocal
from app.services.notification_service import notify_user


def main() -> None:
    if len(sys.argv) < 3:
        print("Uso: python scripts/send_test_push.py <user_type> <user_id>")
        print("     user_type = voluntario | participante")
        return

    user_type = sys.argv[1]
    if user_type not in ("voluntario", "participante"):
        print(f"user_type inválido: {user_type} (usá 'voluntario' o 'participante')")
        return
    user_id = int(sys.argv[2])

    db = SessionLocal()
    try:
        n = notify_user(
            db,
            user_type=user_type,
            user_id=user_id,
            title="Prueba de ALMA 💙",
            body="Si ves esto, las notificaciones funcionan.",
            kind="system",
            url="/",
        )
        print(f"OK: notificación #{n.id} creada para {user_type} {user_id}.")
        print("    - Debería aparecer en la campanita 🔔 al instante (o al recargar).")
        print("    - Si el usuario tiene push activado, además llega la notificación al dispositivo.")
    finally:
        db.close()


if __name__ == "__main__":
    main()

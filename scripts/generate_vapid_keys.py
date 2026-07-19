"""
Genera un par de claves VAPID para Web Push. Correr UNA sola vez.

Uso (con el venv activo y pywebpush ya instalado):
    python scripts/generate_vapid_keys.py

Luego copiar los valores a:
  • Backend  .env:  VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY
  • Frontend .env.local:  NEXT_PUBLIC_VAPID_PUBLIC_KEY (= la PÚBLICA)

La privada NUNCA sale del backend. La pública puede ser visible en el cliente.
"""
import base64

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid01


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def main() -> None:
    v = Vapid01()
    v.generate_keys()

    public_raw = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    private_raw = v.private_key.private_numbers().private_value.to_bytes(32, "big")

    public_key = b64url(public_raw)
    private_key = b64url(private_raw)

    print("\n=== Claves VAPID generadas (guardalas, no se regeneran) ===\n")
    print("Backend  .env:")
    print(f"  VAPID_PUBLIC_KEY={public_key}")
    print(f"  VAPID_PRIVATE_KEY={private_key}")
    print("  VAPID_SUBJECT=mailto:manunovo@gmail.com")
    print("\nFrontend .env.local:")
    print(f"  NEXT_PUBLIC_VAPID_PUBLIC_KEY={public_key}")
    print()


if __name__ == "__main__":
    main()

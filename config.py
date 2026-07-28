from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_NAME: str = "alma_platform"
    DB_USER: str = "alma_app"
    DB_PASSWORD: str = ""

    # Bind solo a localhost — el API no debe exponerse directamente a internet
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8001
    API_RELOAD: bool = False

    # Orígenes permitidos para CORS (separados por coma). Nunca usar "*" en producción.
    CORS_ORIGINS: str = "http://localhost:3000"

    # Clave compartida entre Next.js y FastAPI. Debe ser larga y aleatoria.
    INTERNAL_API_KEY: str = ""

    # Vestigial: el registro de participante ya NO pide token, se verifica por
    # email real. Se conserva por si alguna instalación vieja lo referencia.
    ALMA_REGISTER_TOKEN: str = "123456"

    # Minutos de validez del link de verificación de email del participante.
    # Corto a propósito: el mail llega al instante y un link de activación que
    # vive un día es una ventana abierta si la casilla queda expuesta.
    PARTICIPANT_VERIFICATION_MINUTES: int = 30

    RESEND_API_KEY: str = ""
    MAIL_FROM: str = "hola@almarosario.org.ar"
    APP_BASE_URL: str = "http://localhost:3000"
    TOKEN_EXPIRY_HOURS: int = 24

    # Web Push (VAPID). Si están vacías, el envío push es un no-op silencioso.
    # Generar con scripts/generate_vapid_keys.py (una sola vez).
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    # "mailto:" de contacto exigido por el estándar Web Push.
    VAPID_SUBJECT: str = "mailto:manunovo@gmail.com"

    # ── Archivos subidos ────────────────────────────────────────────────
    # Los BYTES viven en disco; la metadata en la tabla `files`.
    # En producción usar una ruta FUERA del repo (ej: /var/alma/uploads)
    # para que un deploy no la pise, y sumarla al backup.
    FILES_STORAGE_PATH: str = "./storage/uploads"
    # Tamaño máximo del archivo YA DECODIFICADO (el base64 que llega pesa ~33% más).
    FILES_MAX_UPLOAD_MB: int = 5
    # Las imágenes más anchas que esto se redimensionan al guardar.
    # Es la palanca que mantiene el backup chico: 6 MB de cámara → ~200 KB.
    FILES_MAX_IMAGE_PX: int = 1600
    FILES_IMAGE_QUALITY: int = 82

    VERSION: str = "1.6.3"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()

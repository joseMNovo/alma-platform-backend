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

    VERSION: str = "1.0.0"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()

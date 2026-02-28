from fastapi import Header, HTTPException, status
from config import settings


async def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> None:
    """
    Dependencia global que valida la clave interna compartida entre Next.js y FastAPI.
    Todos los endpoints requieren el header 'X-API-Key' con el valor de INTERNAL_API_KEY.
    """
    if not settings.INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="INTERNAL_API_KEY no configurada en el servidor",
        )
    if x_api_key != settings.INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado",
        )

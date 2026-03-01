from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone
from config import settings

from app.deps import verify_api_key
from app.routers import (
    voluntarios,
    talleres,
    grupos,
    actividades,
    pendientes,
    auth,
    pagos,
    inventario,
    inscripciones,
    calendar,
    participants,
    register,
)

app = FastAPI(
    title="ALMA Platform API",
    description="API REST interna para la base de datos de ALMA Platform",
    version=settings.VERSION,
    # Deshabilitar docs en producción (API interna, no pública)
    docs_url=None if not settings.API_RELOAD else "/docs",
    redoc_url=None if not settings.API_RELOAD else "/redoc",
    openapi_url=None if not settings.API_RELOAD else "/openapi.json",
)

# CORS: solo orígenes explícitos. Con allow_origins específicos, allow_credentials=True es seguro.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# Todos los routers requieren la API key interna (dependencia global)
common = {"dependencies": [Depends(verify_api_key)]}

app.include_router(voluntarios.router,   prefix="/voluntarios",   tags=["Voluntarios"],   **common)
app.include_router(talleres.router,      prefix="/talleres",      tags=["Talleres"],       **common)
app.include_router(grupos.router,        prefix="/grupos",         tags=["Grupos"],         **common)
app.include_router(actividades.router,   prefix="/actividades",   tags=["Actividades"],    **common)
app.include_router(pendientes.router,    prefix="/pendientes",    tags=["Pendientes"],     **common)
app.include_router(auth.router,          prefix="/auth",           tags=["Auth"],           **common)
app.include_router(pagos.router,         prefix="/pagos",          tags=["Pagos"],          **common)
app.include_router(inventario.router,    prefix="/inventario",    tags=["Inventario"],     **common)
app.include_router(inscripciones.router, prefix="/inscripciones", tags=["Inscripciones"],  **common)
app.include_router(calendar.router,      prefix="/calendar",      tags=["Calendar"],       **common)
app.include_router(participants.router,  prefix="/participants",  tags=["Participants"],   **common)
app.include_router(register.router,      prefix="/register",      tags=["Register"],       **common)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "app": "ALMA Platform API", "version": settings.VERSION}


# /system/info solo disponible en desarrollo (API_RELOAD=True)
if settings.API_RELOAD:
    import platform

    @app.get("/system/info", tags=["Health"])
    def system_info():
        return {
            "status": "ok",
            "app": app.title,
            "api_version": app.version,
            "server_time_utc": datetime.now(timezone.utc).isoformat(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        }

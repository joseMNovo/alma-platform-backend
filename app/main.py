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
    inventario,
    inscripciones,
    calendar,
    participants,
    personas,
    ideas,
    group_histories,
    announcements,
    activity,
    emails,
    pin_reset,
    push,
    notifications,
    files,
    capacitaciones,
    certificados,
    configuracion,
    encuestas,
    recordatorios,
    accesos,
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
app.include_router(inventario.router,    prefix="/inventario",    tags=["Inventario"],     **common)
app.include_router(inscripciones.router, prefix="/inscripciones", tags=["Inscripciones"],  **common)
app.include_router(calendar.router,      prefix="/calendar",      tags=["Calendar"],       **common)
app.include_router(participants.router,  prefix="/participants",  tags=["Participants"],   **common)
app.include_router(personas.router,      prefix="/personas",       tags=["Personas"],        **common)
app.include_router(ideas.router,         prefix="/ideas",          tags=["Ideas"],           **common)
app.include_router(group_histories.router, prefix="/group-histories", tags=["GroupHistories"], **common)
app.include_router(announcements.router, prefix="/announcements", tags=["Announcements"],  **common)
app.include_router(activity.router,      prefix="/activity",      tags=["Activity"],       **common)
app.include_router(emails.router,        prefix="/emails",         tags=["Emails"],          **common)
app.include_router(pin_reset.router,     prefix="/pin-reset",      tags=["PinReset"],        **common)
app.include_router(push.router,          prefix="/push",           tags=["Push"],            **common)
app.include_router(notifications.router, prefix="/notifications",  tags=["Notifications"],   **common)
app.include_router(files.router,         prefix="/files",          tags=["Files"],           **common)
app.include_router(capacitaciones.router, prefix="/capacitaciones", tags=["Capacitaciones"], **common)
app.include_router(certificados.router,  prefix="/certificados",   tags=["Certificados"],    **common)
app.include_router(configuracion.router, prefix="/configuracion",  tags=["Configuracion"],   **common)
app.include_router(encuestas.router,     prefix="/encuestas",      tags=["Encuestas"],       **common)
app.include_router(recordatorios.router, prefix="/recordatorios",  tags=["Recordatorios"],   **common)
app.include_router(accesos.router,       prefix="/accesos",        tags=["Accesos"],         **common)
app.include_router(register.router,      prefix="/register",       tags=["Register"],        **common)


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

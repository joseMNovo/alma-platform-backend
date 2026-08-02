"""
conftest.py — cimientos de la suite. Todo test hereda de acá.

Tres garantías, en este orden a propósito:

  1. NADA sale a internet. Hay dobles para Resend y para el push, y encima un
     candado que hace FALLAR el test si algo abre un socket a cualquier lado que
     no sea localhost. El candado es lo que importa: si mañana alguien agrega
     una vía de envío nueva y se olvida de mockearla, el test explota en vez de
     escribirle a una voluntaria de verdad.

  2. NADA toca MySQL. La base es SQLite en memoria: nace vacía en cada test y
     desaparece al terminar. No hay servidor que levantar ni archivo que borrar.

  3. NADA lee el .env real. Las variables de test se fijan ANTES de importar la
     app, porque config.py arma `settings` al importarse y lee el entorno una
     sola vez. Si esto fuera después, los tests correrían con la RESEND_API_KEY
     de producción.
"""
import importlib
import os
import pkgutil
import socket
import sys
import tempfile
from pathlib import Path

import pytest

# El repo tiene que estar en sys.path para poder importar `app` y `config`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── 1. Entorno de test ─────────────────────────────────────────────────────
# ⚠️ Esto va ANTES de cualquier import de la app. Ver el punto 3 del docstring.

TEST_API_KEY = "clave-solo-para-tests"

os.environ["INTERNAL_API_KEY"] = TEST_API_KEY
# Vacías a propósito: aunque algo se escapara de los dobles, no podría
# autenticarse contra Resend ni firmar un push.
os.environ["RESEND_API_KEY"] = ""
# Las VAPID van con valores FALSOS pero no vacías: si estuvieran vacías,
# send_push_to_user() cortaría antes de intentar nada y los tests de push no
# probarían el camino real. Con el doble de _send_one puesto, nunca sale un push;
# y si el doble fallara, el candado de red lo corta igual.
os.environ["VAPID_PUBLIC_KEY"] = "clave-publica-falsa-de-test"
os.environ["VAPID_PRIVATE_KEY"] = "clave-privada-falsa-de-test"
os.environ["APP_BASE_URL"] = "http://tests.local"
os.environ["MAIL_FROM"] = "tests@tests.local"
# Fijo a propósito: API_RELOAD decide si existen /docs y /system/info. Sin esto,
# la suite cambiaría según el .env de cada máquina. False = igual que producción.
os.environ["API_RELOAD"] = "false"
# Los tests de archivos escriben acá, nunca dentro del repo.
os.environ["FILES_STORAGE_PATH"] = tempfile.mkdtemp(prefix="alma-tests-")
# Datos de MySQL inventados: si algo intentara conectarse de verdad, falla rápido
# y ruidoso en vez de tocar una base que exista.
os.environ["DB_HOST"] = "0.0.0.0"
os.environ["DB_NAME"] = "no_existe_a_proposito"
os.environ["DB_USER"] = "nadie"
os.environ["DB_PASSWORD"] = ""

from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.dialects import mysql  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.models  # noqa: E402
from app.database import Base, SessionLocal, get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.services import email_service, push_service  # noqa: E402

# Importa TODOS los modelos para que Base.metadata conozca cada tabla. Se recorre
# el paquete en vez de listarlos a mano: así un modelo nuevo entra solo, sin que
# nadie tenga que acordarse de tocar este archivo.
for _mod in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module(f"app.models.{_mod.name}")


# ── 2. Base de datos efímera ───────────────────────────────────────────────

# Algunos modelos usan tipos propios de MySQL que SQLite no sabe crear (hoy solo
# MEDIUMTEXT, en training.py). En vez de tocar los modelos para acomodar a los
# tests, le enseñamos a SQLite con qué reemplazarlos. Si mañana aparece otro tipo
# de MySQL, el error dice exactamente cuál y se agrega una línea acá.
for _tipo_mysql in (mysql.MEDIUMTEXT, mysql.LONGTEXT, mysql.TINYTEXT):
    compiles(_tipo_mysql, "sqlite")(lambda element, compiler, **kw: "TEXT")

compiles(mysql.TINYINT, "sqlite")(lambda element, compiler, **kw: "INTEGER")



# StaticPool + una sola conexión es OBLIGATORIO con SQLite en memoria: sin eso,
# cada conexión nueva estrena su propia base vacía y no se ve nada de lo escrito.
engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def _enforce_foreign_keys(dbapi_connection, _record):
    """SQLite ignora las foreign keys salvo que se lo pidas. MySQL no, así que
    las activamos para que el test se parezca a producción."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Clave: parte del código (send_email_bg, las tareas de background del calendario,
# los push) NO recibe la sesión del request, sino que abre la suya con el
# SessionLocal de la app — que apunta a MySQL. Reconfigurarlo acá lo redirige a
# SQLite para TODOS los módulos que ya lo importaron, sin tener que parchear cada
# uno por separado.
SessionLocal.configure(bind=engine)


@pytest.fixture()
def db():
    """Base limpia para cada test: se crea, se usa y se tira."""
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db):
    """Cliente HTTP contra la app en memoria, con la API key ya puesta.

    No abre puertos ni sockets: habla con la app por ASGI, dentro del proceso.
    """
    def _get_db_override():
        yield db

    fastapi_app.dependency_overrides[get_db] = _get_db_override
    with TestClient(fastapi_app) as c:
        c.headers.update({"X-API-Key": TEST_API_KEY})
        yield c
    fastapi_app.dependency_overrides.clear()


# ── 3. Nada sale al mundo ──────────────────────────────────────────────────

class Buzon:
    """Bandeja de salida falsa. Junta lo que el código creyó estar mandando,
    para que los tests puedan verificarlo."""

    def __init__(self):
        self.emails: list[dict] = []
        self.push: list[dict] = []
        # Si se pone en True, el próximo mail "falla" (para testear ese camino).
        self.fallar_emails = False

    @property
    def destinatarios(self) -> list[str]:
        """Todos los mails a los que se les escribió, aplanados."""
        return [to for mail in self.emails for to in mail["to"]]

    def ultimo_email(self) -> dict:
        assert self.emails, "No se mandó ningún email"
        return self.emails[-1]


@pytest.fixture()
def buzon():
    return Buzon()


@pytest.fixture(autouse=True)
def sin_envios_reales(monkeypatch, buzon):
    """Reemplaza los DOS únicos puntos por donde el sistema manda algo afuera."""

    def _resend_falso(req):
        buzon.emails.append({
            "to": list(req.to),
            "cc": list(req.cc) if req.cc else None,
            "subject": req.subject,
            "template": req.template,
            "variables": dict(req.variables or {}),
        })
        if buzon.fallar_emails:
            return "failed", None, "Resend rechazó el mail (simulado en test)"
        return "sent", f"fake-resend-id-{len(buzon.emails)}", None

    def _push_falso(sub_info, payload):
        buzon.push.append({"sub": sub_info, "payload": payload})
        # El contrato de _send_one es (enviados, status_si_falló): 1 envío OK.
        return 1, None

    monkeypatch.setattr(email_service, "_deliver_to_resend", _resend_falso)
    monkeypatch.setattr(push_service, "_send_one", _push_falso)


@pytest.fixture(autouse=True)
def candado_de_red(monkeypatch):
    """Hace fallar el test si algo intenta salir a internet.

    Es la red de seguridad de todo esto: los dobles de arriba cubren lo que hoy
    conocemos, esto cubre lo que se agregue mañana. SQLite en memoria y el
    TestClient no usan sockets, así que no molesta a nada legítimo.
    """
    conectar_real = socket.socket.connect
    locales = {"127.0.0.1", "::1", "localhost", ""}

    def _conectar_vigilado(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if str(host) not in locales:
            raise RuntimeError(
                f"BLOQUEADO: un test intentó conectarse a {host!r}.\n"
                "Ningún test puede salir a internet (podría mandar un mail o un "
                "push de verdad). Mockeá esa llamada en el test o sumá un doble "
                "en tests/conftest.py."
            )
        return conectar_real(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _conectar_vigilado)


# ── 4. Datos de arranque ───────────────────────────────────────────────────
# Fixtures chiquitas y componibles. La idea es que un test diga qué necesita
# ("un admin y dos voluntarios") sin repetir el armado en cada archivo.

@pytest.fixture()
def crear_voluntario(db):
    """Fábrica de voluntarios. Devuelve una función para poder crear varios."""
    from datetime import date

    from app.models.voluntario import Voluntario

    contador = {"n": 0}

    def _crear(**campos):
        contador["n"] += 1
        n = contador["n"]
        datos = {
            "name": f"Voluntario{n}",
            "last_name": "Deprueba",
            "email": f"voluntario{n}@tests.local",
            # NOT NULL sin default en el modelo: la app siempre la setea a mano.
            "registration_date": date(2026, 1, 1),
            "status": "activo",
            "is_admin": False,
            "email_verified": True,
        }
        datos.update(campos)
        v = Voluntario(**datos)
        db.add(v)
        db.commit()
        db.refresh(v)
        return v

    return _crear


@pytest.fixture()
def admin(crear_voluntario):
    return crear_voluntario(name="Admin", email="admin@tests.local", is_admin=True)


@pytest.fixture()
def voluntario(crear_voluntario):
    return crear_voluntario()

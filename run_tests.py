#!/usr/bin/env python3
"""
run_tests.py — corre la suite de tests del backend.

    python run_tests.py                    # todo
    python run_tests.py tests/test_cron.py # un archivo
    python run_tests.py -k recordatorio    # los que matcheen ese nombre
    python run_tests.py -v                 # con el detalle de cada test

Cualquier argumento extra se le pasa tal cual a pytest.

Es seguro correrlo en cualquier momento, incluso con el sistema en producción
andando: los tests usan una base SQLite en memoria (no MySQL) y tienen dobles
para Resend y para el push, más un candado que hace fallar el test si algo
intenta salir a internet. Ver tests/conftest.py.

La primera vez:  pip install -r requirements-dev.txt
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    # La consola de Windows arranca en cp1252 y se ahoga con los acentos y los
    # caracteres de caja. Sin esto, el runner explota antes de correr un solo test.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        import pytest  # noqa: F401
    except ImportError:
        print(
            "Falta pytest. Instalá las dependencias de desarrollo:\n\n"
            f"    {Path(sys.executable).name} -m pip install -r requirements-dev.txt\n",
            file=sys.stderr,
        )
        return 1

    args = sys.argv[1:] or ["tests"]

    print("── Tests del backend ALMA ─────────────────────────────────────")
    print("Base: SQLite en memoria · Mails y push: interceptados · Red: bloqueada")
    print()

    # Que pytest también escriba en UTF-8 (los tests tienen nombres con acentos).
    entorno = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    # -p no:cacheprovider evita que quede un .pytest_cache dando vueltas en el repo.
    return subprocess.call(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=ROOT,
        env=entorno,
    )


if __name__ == "__main__":
    sys.exit(main())

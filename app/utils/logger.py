"""
app/utils/logger.py — ALMA Backend — Logger central
====================================================
Formato: timestamp | LEVEL | user=X | module=Y | action=Z | message=... | meta={...}

Dev  → logs/dev/backend.log + consola
Prod → logs/prod/backend.log + consola
"""
import logging
import os
import json
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import settings

_IS_DEV = getattr(settings, "DEBUG", True) or os.getenv("ENV", "development") != "production"
_LOG_DIR = Path(__file__).resolve().parents[2] / "logs" / ("dev" if _IS_DEV else "prod")
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_LOG_LEVEL = logging.DEBUG if _IS_DEV else logging.INFO


class _AlmaFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%dT%H:%M:%S")
        level = record.levelname.ljust(5)
        user = getattr(record, "user", "anonymous")
        module = getattr(record, "module_name", "")
        action = getattr(record, "action", "")
        meta = getattr(record, "meta", None)

        parts = [f"{ts} | {level} | user={user}"]
        if module:
            parts.append(f"module={module}")
        if action:
            parts.append(f"action={action}")
        parts.append(f"message={record.getMessage()}")
        if meta:
            parts.append(f"meta={json.dumps(meta, ensure_ascii=False, default=str)}")
        if record.exc_info:
            parts.append(f"exc={self.formatException(record.exc_info)}")
        return " | ".join(parts)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("alma")
    if logger.handlers:
        return logger
    logger.setLevel(_LOG_LEVEL)
    logger.propagate = False

    fmt = _AlmaFormatter()

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Rotating file handler (10 MB × 5 files)
    fh = RotatingFileHandler(
        _LOG_DIR / "backend.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


_logger = _build_logger()


def _log(level: int, message: str, *, user=None, module: str = "", action: str = "", meta: dict | None = None, exc_info=False):
    extra = {"user": user or "anonymous", "module_name": module, "action": action, "meta": meta}
    _logger.log(level, message, extra=extra, exc_info=exc_info)


def log_info(message: str, *, user=None, module: str = "", action: str = "", meta: dict | None = None):
    _log(logging.INFO, message, user=user, module=module, action=action, meta=meta)


def log_warn(message: str, *, user=None, module: str = "", action: str = "", meta: dict | None = None):
    _log(logging.WARNING, message, user=user, module=module, action=action, meta=meta)


def log_error(message: str, *, user=None, module: str = "", action: str = "", meta: dict | None = None, exc_info: bool = False):
    _log(logging.ERROR, message, user=user, module=module, action=action, meta=meta, exc_info=exc_info)


def log_debug(message: str, *, user=None, module: str = "", action: str = "", meta: dict | None = None):
    _log(logging.DEBUG, message, user=user, module=module, action=action, meta=meta)

"""DEPRECATED: use the canonical application entrypoint instead.

This module is a thin re-export of ``app.main:app`` kept only so that
stale references (e.g. ``uvicorn main:app``) still resolve. It is not used by
Docker, docker-compose, or the worker.

Run the API with: ``uvicorn app.main:app`` (from ``backend/``).
"""
import warnings

from app.main import app  # noqa: F401

warnings.warn(
    "backend/main.py is deprecated. Use `uvicorn app.main:app` instead.",
    DeprecationWarning,
    stacklevel=2,
)
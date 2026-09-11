"""Firebase Admin SDK infrastructure (server-side only).

The application uses ``Cloud Firestore`` for the distributed API rate limiter
that replaced Redis. ``PostgreSQL + pgvector`` remains the system of record —
Firestore is never used for application data.

All access is lazy: ``import app.core.firebase`` makes no network calls. The
admin app and Firestore client are initialized on first use (``get_firestore``),
so ordinary unit tests and dev runs without credentials never touch Google.

Credentials come from the process environment via ``app.core.config.Settings``:

    FIREBASE_PROJECT_ID=
    FIREBASE_CLIENT_EMAIL=
    FIREBASE_PRIVATE_KEY=   # multiline PEM; embedded \\n are unescaped for you

Never hard-code credentials, and never expose the service account to browser
code — these are backend-only server-side secrets.

Local development can target the Firebase Emulator Suite by setting
``FIRESTORE_EMULATOR_HOST`` (e.g. ``127.0.0.1:8080``) in addition to the three
credentials; the Admin SDK then talks to the emulator instead of Google.
"""
import os
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("firebase")

# Set once by ``_initialize``:
_credentials: Optional[Any] = None
_admin_app: Optional[Any] = None
_db: Optional[Any] = None


def firebase_configured() -> bool:
    """True when all three service-account credentials are present."""
    return bool(settings.FIREBASE_PROJECT_ID and settings.FIREBASE_CLIENT_EMAIL
                and settings.FIREBASE_PRIVATE_KEY)


def normalize_private_key(value: str) -> str:
    """Normalize a PEM private key read from configuration.

    ``.env`` files express the multiline key with embedded ``\\n`` escapes;
    some Docker/CI environments re-escape them to literal ``\\n`` text. Return
    a real multiline PEM either way so google-auth accepts it.
    """
    if "\\n" in value and "\n" not in value:
        return value.replace("\\n", "\n")
    return value


def _make_credentials():
    """Build a google-auth service account Credentials object from settings."""
    if not firebase_configured():
        return None
    try:
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_info(
            {
                "type": "service_account",
                "project_id": settings.FIREBASE_PROJECT_ID,
                "client_email": settings.FIREBASE_CLIENT_EMAIL,
                "private_key": normalize_private_key(settings.FIREBASE_PRIVATE_KEY),
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
    except Exception as exc:  # pragma: no cover - config bug, diagnostic path
        logger.error(
            "Failed to build Firebase service-account credentials",
            extra={
                "event_name": "firebase.credentials_failed",
                "error_type": type(exc).__name__,
            },
        )
        return None


def _initialize() -> None:
    """Initialize the Firebase Admin app + Firestore client once (lazy).

    ``initialize_app`` is a singleton (a second call without a name raises
    ValueError), so resolve an existing default app first — important under
    ``uvicorn --reload`` where this module is re-imported.
    """
    global _credentials, _admin_app, _db  # noqa: PLW0603
    if _db is not None:
        return

    import firebase_admin
    from firebase_admin import credentials, firestore

    try:
        app = firebase_admin.get_app()
    except ValueError:
        app = None
    if app is None:
        _credentials = _credentials or credentials.Certificate(_make_credentials())
        app = firebase_admin.initialize_app(
            _credentials,
            options={"projectId": settings.FIREBASE_PROJECT_ID},
        )
    _admin_app = app
    _db = firestore.client(app=app)
    emulator = os.environ.get("FIRESTORE_EMULATOR_HOST")
    logger.info(
        "Firebase Firestore ready",
        extra={
            "event_name": "firebase.ready",
            "project": settings.FIREBASE_PROJECT_ID,
            "emulator": emulator or "false",
        },
    )


def get_firestore() -> Optional[Any]:
    """Return the cached ``google.cloud.firestore.Client`` (or None).

    None means Firebase is not configured: the application then runs with
    per-instance (development) rate limiting. Production configuration is
    enforced earlier by ``Settings.validate_critical_secrets``.
    """
    if not firebase_configured():
        return None
    try:
        _initialize()
    except Exception as exc:
        logger.error(
            "Failed to initialize Firebase Admin SDK",
            extra={
                "event_name": "firebase.init_failed",
                "error_type": type(exc).__name__,
            },
        )
        return None
    return _db


def health() -> dict:
    """Shape the ``/health/dependencies`` entry for Firebase (no network I/O)."""
    if not firebase_configured():
        return {
            "available": False,
            "reason": "FIREBASE_* credentials not configured",
        }
    emulator = os.environ.get("FIRESTORE_EMULATOR_HOST")
    return {
        "available": True,
        "emulator": bool(emulator),
        "project": settings.FIREBASE_PROJECT_ID,
    }

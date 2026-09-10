"""At-rest encryption for target-agent credentials.

Target API keys must never sit in the database as plaintext. Encrypting them
with a key derived from the deployment's ``SECRET_KEY`` means:

  * production deployments with an explicit ``SECRET_KEY`` get real secrecy;
  * development/test runs (which fall back to the documented dev secret) stay
    deterministic and restart-stable, so migrations and fixtures never break.

We deliberately do NOT add another secret knob: ``SECRET_KEY`` is already
mandatory in production, so deriving from it adds no new operational burden.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _derive_key() -> bytes:
    secret = (
        settings.SECRET_KEY
        or "development-only-insecure-secret-do-not-use-in-production"
    )
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


_fernet = Fernet(_derive_key())


def _looks_encrypted(value: str) -> bool:
    return value.startswith("enc:") and ":" in value


def encrypt_value(plaintext: str) -> str:
    """Encrypt a secret for storage, prefixed so decrypt is unambiguous."""
    if _looks_encrypted(plaintext):
        return plaintext
    token = _fernet.encrypt(plaintext.encode("utf-8"))
    return f"enc:{token.decode('utf-8')}"


def decrypt_value(stored: str) -> str:
    """Decrypt a stored secret (or return it unchanged if already plaintext)."""
    if not stored or not _looks_encrypted(stored):
        return stored
    try:
        return _fernet.decrypt(stored[4:].encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return stored


def mask_secret(value: str) -> str:
    """Mask a secret for display/logs without revealing the real value."""
    if not value:
        return ""
    if len(value) <= 6:
        return "••••"
    return f"{value[:2]}••••{value[-2:]}"

"""Firebase Admin infrastructure tests (no live Google/emulator access).

The test environment never sets FIREBASE_* credentials (see conftest), so
``firebase_configured()`` is False, ``get_firestore()`` returns None, the Admin
SDK is never initialized, and no network call is ever made.
"""
import pytest

from app.core import firebase


@pytest.fixture(autouse=True)
def _no_firebase_credentials(monkeypatch):
    """Guarantee credentials are absent for every test in this module."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "FIREBASE_PROJECT_ID", None)
    monkeypatch.setattr(settings, "FIREBASE_CLIENT_EMAIL", None)
    monkeypatch.setattr(settings, "FIREBASE_PRIVATE_KEY", None)


def test_not_configured_without_credentials():
    assert firebase.firebase_configured() is False


def test_get_firestore_returns_none_when_unconfigured():
    assert firebase.get_firestore() is None


def test_health_reports_unavailable_reason():
    health = firebase.health()
    assert health["available"] is False
    assert "FIREBASE" in health["reason"]


def test_health_mirrors_firebase_configured(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FIREBASE_PROJECT_ID", "oracle-dev")
    monkeypatch.setattr(settings, "FIREBASE_CLIENT_EMAIL", "svc@oracle-dev.iam.gserviceaccount.com")
    monkeypatch.setattr(settings, "FIREBASE_PRIVATE_KEY", "key-material")
    health = firebase.health()
    assert health["available"] is True
    assert health["project"] == "oracle-dev"
    assert "emulator" in health


def test_normalize_private_key_unescapes_literal_backslash_n():
    value = "-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----"
    normalized = firebase.normalize_private_key(value)
    assert "\\n" not in normalized
    assert normalized.count("\n") == 2


def test_normalize_private_key_leaves_real_newlines_untouched():
    value = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    assert firebase.normalize_private_key(value) == value

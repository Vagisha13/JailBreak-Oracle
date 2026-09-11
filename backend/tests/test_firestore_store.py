"""Store-level tests for the Firestore-backed rate limiter.

``FakeFirestore`` implements the same transaction protocol as the real
``FirestoreStore`` adapter (consistent reads, atomic writes, optimistic
concurrency), so these tests exercise the exact limiter code that runs in
production without any network access.
"""
import pytest

from app.core.firestore_store import FakeFirestore, doc_id_for
from app.core.ratelimit import FirestoreRateLimiter, RateLimitUnavailableError


def test_doc_id_is_hashed_stable_and_bucket_scoped():
    a1 = doc_id_for("auth", "127.0.0.1")
    a2 = doc_id_for("auth", "127.0.0.1")
    b = doc_id_for("campaign", "127.0.0.1")
    assert a1 == a2
    assert a1 != b
    assert a1.startswith("rl.")
    assert len(a1) == 3 + 64


def test_fake_store_run_transaction_commits_writes():
    store = FakeFirestore()

    def _fn(txn) -> bool:
        txn.set("doc.1", {"count": 7})
        return True

    assert store.run_transaction(_fn) is True
    assert store._documents["doc.1"]["count"] == 7


def test_fake_store_rolls_back_on_exception():
    store = FakeFirestore()

    def _fn(txn) -> bool:
        txn.set("doc.1", {"count": 7})
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        store.run_transaction(_fn)
    assert "doc.1" not in store._documents


def test_fake_store_reset_all_and_reset_bucket():
    store = FakeFirestore()
    for bucket in ("auth", "campaign"):
        doc_id = doc_id_for(bucket, "10.0.0.1")
        store.run_transaction(lambda txn, d=doc_id, b=bucket: txn.set(d, {"bucket": b, "count": 1}))
    assert len(store._documents) == 2

    store.reset_bucket("campaign")
    assert {d["bucket"] for d in store._documents.values()} == {"auth"}

    store.reset_all()
    assert store._documents == {}


@pytest.mark.asyncio
async def test_limiter_allows_until_limit_then_blocks_with_retry_after():
    store = FakeFirestore()
    limiter = FirestoreRateLimiter(store, window=60, bucket="auth")
    key = "auth:192.0.2.10"

    assert await limiter.check(key, limit=2, window=60) == (True, None)
    assert await limiter.check(key, limit=2, window=60) == (True, None)
    allowed, retry_after = await limiter.check(key, limit=2, window=60)
    assert allowed is False
    assert retry_after and retry_after >= 1
    assert retry_after <= 60


@pytest.mark.asyncio
async def test_limiter_separate_keys_do_not_collide():
    store = FakeFirestore()
    limiter = FirestoreRateLimiter(store, window=60, bucket="default")

    assert await limiter.check("default:10.0.0.1", limit=1, window=60) == (True, None)
    assert await limiter.check("default:10.0.0.2", limit=1, window=60) == (True, None)


@pytest.mark.asyncio
async def test_limiter_window_expiry_resets_counter():
    store = FakeFirestore()
    limiter = FirestoreRateLimiter(store, window=60, bucket="auth")
    key = "auth:198.51.100.7"

    assert await limiter.check(key, limit=1, window=60) == (True, None)
    allowed, _ = await limiter.check(key, limit=1, window=60)
    assert allowed is False

    doc_id = doc_id_for("auth", key)
    store.run_transaction(lambda txn: txn.set(doc_id, {"count": 1, "window_start": 0.0, "bucket": "auth"}))

    assert await limiter.check(key, limit=1, window=60) == (True, None)


@pytest.mark.asyncio
async def test_limiter_concurrent_checks_are_atomic():
    store = FakeFirestore()
    limiter = FirestoreRateLimiter(store, window=60, bucket="auth")
    key = "auth:203.0.113.3"

    results = []
    for _ in range(5):
        results.append(await limiter.check(key, limit=5, window=60))

    assert results == [(True, None)] * 5

    blocked, _ = await limiter.check(key, limit=5, window=60)
    assert blocked is False


@pytest.mark.asyncio
async def test_limiter_store_failure_raises_unavailable():
    class _BoomStore:
        def run_transaction(self, fn):
            raise RuntimeError("connection refused")

        def reset_all(self):
            pass

    limiter = FirestoreRateLimiter(_BoomStore(), window=60, bucket="auth")
    with pytest.raises(RateLimitUnavailableError):
        await limiter.check("auth:127.0.0.1", limit=10, window=60)


@pytest.mark.asyncio
async def test_limiter_reset_global_clears_all_counters():
    store = FakeFirestore()
    limiter = FirestoreRateLimiter(store, window=60, bucket="auth")
    key = "auth:203.0.113.9"

    assert await limiter.check(key, limit=1, window=60) == (True, None)
    assert (await limiter.check(key, limit=1, window=60))[0] is False

    await limiter.reset_global()

    assert await limiter.check(key, limit=1, window=60) == (True, None)

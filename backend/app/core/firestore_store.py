"""Firestore rate-limit store adapters.

A ``RateLimitStore`` exposes exactly what the rate limiter needs:

    outcome = store.run_transaction(fn)
    # fn receives a txn with:
    #   txn.get(doc_id)  -> Optional[dict]  (latest committed snapshot data)
    #   txn.set(doc_id, data: dict)         (atomic write within the txn)

``run_transaction`` has Cloud Firestore semantics: reads observe a consistent
snapshot and the writes apply only if nothing the transaction read changed in
between (optimistic concurrency). Two implementations share the identical
transaction protocol so the *exact* limiter code runs against both:

  * ``FirestoreStore``     real Firebase Admin Firestore client.
  * ``FakeFirestore``      deterministic in-process stand-in for unit tests.
"""
import hashlib
import threading
from typing import Any, Callable, Optional, Tuple

_Outcome = Tuple[bool, Optional[int]]


def doc_id_for(bucket: str, key: str) -> str:
    """Hashed, URL-safe document id for ``rate_limits/{doc_id}``.

    The hash keeps the client identifier (an IP) out of Firestore and bounds
    the document id length; the bucket is stored as a field so scoped resets
    can filter without enumerating hashes.
    """
    hashed = hashlib.sha256(f"{bucket}:{key}".encode("utf-8")).hexdigest()
    return f"rl.{hashed}"


class FirestoreStore:
    """Production adapter backed by the Firebase Admin Firestore client."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._coll = db.collection("rate_limits")

    def run_transaction(self, fn: Callable) -> _Outcome:
        def _body(t: Any) -> _Outcome:
            txn = _FirestoreTxn(t, self._coll)
            return fn(txn)

        return self._db.run_in_transaction(_body)  # type: ignore[attr-defined]

    def reset_bucket(self, bucket: str) -> None:
        query = self._coll.where("bucket", "==", bucket)
        for snap in query.stream():
            snap.reference.delete()

    def reset_all(self) -> None:
        for snap in self._coll.stream():
            snap.reference.delete()


class _FirestoreTxn:
    """Binds a live Cloud Firestore transaction to the ``coll`` collection."""

    def __init__(self, txn: Any, coll: Any) -> None:
        self._txn = txn
        self._coll = coll

    def get(self, doc_id: str) -> Optional[dict]:
        snap = self._txn.get(self._coll.document(doc_id))
        if not snap.exists:
            return None
        return snap.to_dict()

    def set(self, doc_id: str, data: dict) -> None:
        self._txn.set(self._coll.document(doc_id), data)


class FakeFirestore:
    """In-process stand-in for the Firebase Firestore client.

    A single writer lock serializes transactions. A transaction notes the
    version of every document it read; the writes commit only when none of
    those versions changed in between (one optimistic retry if they did) —
    mirroring Cloud Firestore transaction semantics without network I/O.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._documents: dict = {}
        self._versions: dict = {}

    def run_transaction(self, fn: Callable) -> _Outcome:
        with self._lock:
            versions = dict(self._versions)
            reads: set = set()
            writes: dict = {}

            def _get(doc_id: str) -> Optional[dict]:
                reads.add(doc_id)
                if doc_id not in self._documents:
                    return None
                return dict(self._documents[doc_id])

            def _set(doc_id: str, data: dict) -> None:
                writes[doc_id] = dict(data)

            txn = _FakeTxn(_get, _set)
            outcome = fn(txn)

            changed = [
                doc_id
                for doc_id in reads
                if self._versions.get(doc_id, 0) != versions.get(doc_id, 0)
            ]
            if changed:
                # Optimistic retry against the newest committed state.
                reads.clear()
                writes.clear()
                outcome = fn(txn)
            for doc_id, data in writes.items():
                self._documents[doc_id] = dict(data)
                self._versions[doc_id] = self._versions.get(doc_id, 0) + 1
            return outcome

    def reset_bucket(self, bucket: str) -> None:
        with self._lock:
            for doc_id, data in list(self._documents.items()):
                if data.get("bucket") == bucket:
                    self._documents.pop(doc_id, None)
                    self._versions.pop(doc_id, None)

    def reset_all(self) -> None:
        with self._lock:
            self._documents.clear()
            self._versions.clear()


class _FakeTxn:
    def __init__(self, getter: Callable[[str], Optional[dict]],
                 setter: Callable[[str, dict], None]) -> None:
        self._getter = getter
        self._setter = setter

    def get(self, doc_id: str) -> Optional[dict]:
        return self._getter(doc_id)

    def set(self, doc_id: str, data: dict) -> None:
        self._setter(doc_id, data)

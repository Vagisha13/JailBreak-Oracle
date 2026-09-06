"""Attack memory for RAG-style retrieval (E-13).

Defines the explicit ``AttackMemory`` interface and two interchangeable
backends:

* ``VectorMemoryService`` — semantic similarity via pgvector ``<=>`` on
  PostgreSQL (embeddings are generated and persisted).
* ``KeywordMemoryService`` — the *honest* SQLite fallback. It never pretends
  recency is relevance: it logs a startup warning and ranks attacks by
  query-token overlap (Jaccard scoring) instead. Embeddings are not generated
  or persisted without pgvector.

``MemoryService`` is a facade that selects the right backend from a cached
capability probe, preserving the old call sites.
"""
import re
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.future import select

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal, DATABASE_URL
from app.models.domain import Attack, Vulnerability
from app.targets.embeddings import EmbeddingProvider

logger = get_logger("memory")

_STATUS_VALUES = ("successful", "failed")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Cached capability probe; a single DB (URL) per process, so module-level cache
# is safe and keeps the honest warning to one emission.
_pgvector_available: Optional[bool] = None


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _validate_status(status: Optional[str]) -> None:
    if status is not None and status not in _STATUS_VALUES:
        raise ValueError(f"status must be one of {_STATUS_VALUES}, got {status!r}")


async def is_pgvector_available() -> bool:
    """Probe (once) whether pgvector is usable in the configured database."""
    global _pgvector_available
    if _pgvector_available is not None:
        return _pgvector_available
    if DATABASE_URL.startswith("sqlite"):
        _pgvector_available = False
        return False
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            )
        _pgvector_available = True
    except Exception:
        logger.warning(
            "pgvector unavailable; semantic memory disabled",
            extra={"event_name": "memory.pgvector_unavailable"},
            exc_info=True,
        )
        _pgvector_available = False
    return _pgvector_available


class AttackMemory(ABC):
    """Explicit retrieval/memory contract used by the attacker agent."""

    @abstractmethod
    async def embed_attack(
        self, attack_id: uuid.UUID, prompt_text: str
    ) -> None:
        """Persist an embedding for the attack (no-op when unsupported)."""

    @abstractmethod
    async def retrieve_similar_attacks(
        self,
        query: str,
        limit: int = 3,
        experiment_id: Optional[uuid.UUID] = None,
        status: Optional[str] = None,
        strategy_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve the most relevant past attacks.

        ``status`` filters to ``"successful"`` (has a recorded vulnerability)
        or ``"failed"`` (no vulnerability). ``strategy_name`` filters exactly.
        """


class VectorMemoryService(AttackMemory):
    """pgvector-based semantic retrieval on PostgreSQL."""

    def __init__(self, embedding_provider: EmbeddingProvider):
        self.provider = embedding_provider

    async def embed_attack(
        self, attack_id: uuid.UUID, prompt_text: str
    ) -> None:
        vector = await self.provider.generate_embedding(prompt_text)

        async with AsyncSessionLocal() as session:
            stmt = select(Attack).where(Attack.id == attack_id)
            result = await session.execute(stmt)
            attack = result.scalars().first()
            if attack is None:
                return
            attack.embedding = vector
            await session.commit()

    async def retrieve_similar_attacks(
        self,
        query: str,
        limit: int = 3,
        experiment_id: Optional[uuid.UUID] = None,
        status: Optional[str] = None,
        strategy_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        _validate_status(status)

        query_vector = await self.provider.generate_embedding(query)
        vector_str = f"[{','.join(map(str, query_vector))}]"

        where: list[str] = ["a.embedding IS NOT NULL"]
        params: dict[str, Any] = {"vector": vector_str, "limit": int(limit)}
        if experiment_id is not None:
            where.append("a.experiment_id = CAST(:experiment_id AS UUID)")
            params["experiment_id"] = str(experiment_id)
        if strategy_name is not None:
            where.append("a.strategy_name = :strategy_name")
            params["strategy_name"] = strategy_name
        if status == "successful":
            where.append(
                "EXISTS (SELECT 1 FROM vulnerabilities v WHERE v.attack_id = a.id)"
            )
        elif status == "failed":
            where.append(
                "NOT EXISTS (SELECT 1 FROM vulnerabilities v WHERE v.attack_id = a.id)"
            )

        sql = text(
            """
            SELECT a.id, a.prompt_text, a.strategy_name,
                EXISTS (
                    SELECT 1 FROM vulnerabilities v WHERE v.attack_id = a.id
                ) AS is_successful
            FROM attacks a
            WHERE {where}
            ORDER BY a.embedding <=> CAST(:vector AS vector)
            LIMIT :limit
            """.format(where=" AND ".join(where))
        )

        async with AsyncSessionLocal() as session:
            result = await session.execute(sql, params)
            rows = result.fetchall()
            return [
                {
                    "prompt_text": row.prompt_text,
                    "strategy_name": row.strategy_name,
                    "is_successful": bool(row.is_successful),
                }
                for row in rows
            ]


class KeywordMemoryService(AttackMemory):
    """Honest SQLite fallback: token-overlap (Jaccard) relevance ranking.

    Semantic similarity is unavailable without pgvector. Rather than returning
    the most recent attacks as if they were relevant (pre-E-13 behavior), this
    backend scores attacks by query-token overlap and logs a startup warning.
    No embeddings are generated or persisted.
    """

    _warned = False

    def __init__(self, embedding_provider: Optional[EmbeddingProvider] = None):
        self.provider = embedding_provider
        if not KeywordMemoryService._warned:
            KeywordMemoryService._warned = True
            logger.warning(
                "Keyword memory backend active: SQLite has no pgvector, so "
                "retrieval uses keyword overlap scoring instead of semantic "
                "similarity and embeddings are not persisted."
            )

    async def embed_attack(
        self, attack_id: uuid.UUID, prompt_text: str
    ) -> None:
        # Honest no-op: without pgvector there is nowhere to store an embedding,
        # and faking it would corrupt the semantic path.
        return

    async def retrieve_similar_attacks(
        self,
        query: str,
        limit: int = 3,
        experiment_id: Optional[uuid.UUID] = None,
        status: Optional[str] = None,
        strategy_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        _validate_status(status)

        async with AsyncSessionLocal() as session:
            stmt = select(Attack)
            if experiment_id is not None:
                stmt = stmt.where(Attack.experiment_id == experiment_id)
            if strategy_name is not None:
                stmt = stmt.where(Attack.strategy_name == strategy_name)
            attacks = (await session.execute(stmt)).scalars().all()

            ids = [a.id for a in attacks]
            successful_ids: set[uuid.UUID] = set()
            if ids:
                vuln_stmt = select(Vulnerability.attack_id).where(
                    Vulnerability.attack_id.in_(ids)
                )
                for (attack_id,) in (await session.execute(vuln_stmt)).all():
                    successful_ids.add(attack_id)

        query_tokens = _tokenize(query)
        scored: list[Dict[str, Any]] = []
        for attack in attacks:
            is_successful = attack.id in successful_ids
            if status == "successful" and not is_successful:
                continue
            if status == "failed" and is_successful:
                continue
            attack_tokens = _tokenize(attack.prompt_text)
            overlap = query_tokens & attack_tokens
            scored.append(
                {
                    "prompt_text": attack.prompt_text,
                    "strategy_name": attack.strategy_name,
                    "is_successful": is_successful,
                    "score": _jaccard(query_tokens, attack_tokens),
                    "matched_tokens": sorted(overlap),
                    "created_at": attack.created_at,
                }
            )

        # Honest ordering: relevance first, recency as tie-break only. Attacks
        # with zero overlap are irrelevant and therefore excluded.
        scored.sort(key=lambda r: (r["score"], _created_ts(r["created_at"])), reverse=True)
        best = [r for r in scored if r["score"] > 0.0]
        return best[:limit]


def _created_ts(dt: Any) -> float:
    try:
        return dt.timestamp()
    except (AttributeError, TypeError, ValueError):
        return 0.0


class MemoryService(AttackMemory):
    """Facade that picks the pgvector or keyword backend from the DB.

    Retains the historical constructor/method surface so existing callers and
    tests keep working while the underlying behavior is now honest.
    """

    def __init__(self, embedding_provider: EmbeddingProvider):
        self.provider = embedding_provider
        self._backend: Optional[AttackMemory] = None

    async def _resolve_backend(self) -> AttackMemory:
        if self._backend is not None:
            return self._backend
        if await is_pgvector_available():
            self._backend = VectorMemoryService(self.provider)
        else:
            self._backend = KeywordMemoryService(self.provider)
        return self._backend

    async def embed_attack(
        self, attack_id: uuid.UUID, prompt_text: str
    ) -> None:
        return await (await self._resolve_backend()).embed_attack(
            attack_id, prompt_text
        )

    async def retrieve_similar_attacks(
        self,
        query: str,
        limit: int = 3,
        experiment_id: Optional[uuid.UUID] = None,
        status: Optional[str] = None,
        strategy_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return await (await self._resolve_backend()).retrieve_similar_attacks(
            query,
            limit=limit,
            experiment_id=experiment_id,
            status=status,
            strategy_name=strategy_name,
        )

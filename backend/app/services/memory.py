import uuid
from typing import List, Dict, Any, Optional

from sqlalchemy import text
from sqlalchemy.future import select

from app.db.session import AsyncSessionLocal, DATABASE_URL
from app.models.domain import Attack, Vulnerability
from app.targets.embeddings import EmbeddingProvider


class MemoryService:
    """
    Handles vector similarity search and attack embedding.
    Falls back to simple text search when pgvector is unavailable (e.g., SQLite).
    """

    def __init__(self, embedding_provider: EmbeddingProvider):
        self.provider = embedding_provider
        self._use_pgvector: Optional[bool] = None

    async def _is_pgvector_available(self) -> bool:
        if self._use_pgvector is not None:
            return self._use_pgvector

        if DATABASE_URL.startswith("sqlite"):
            self._use_pgvector = False
            return False

        try:
            async with AsyncSessionLocal() as session:
                await session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
                self._use_pgvector = True
                return True
        except Exception:
            self._use_pgvector = False
            return False

    async def embed_attack(
        self, attack_id: uuid.UUID, prompt_text: str
    ) -> None:
        if not await self._is_pgvector_available():
            return

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
    ) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []

        if not await self._is_pgvector_available():
            return await self._retrieve_basic(query, limit, experiment_id)

        query_vector = await self.provider.generate_embedding(query)
        vector_str = f"[{','.join(map(str, query_vector))}]"

        if experiment_id is not None:
            sql = text(
                """
                SELECT a.id, a.prompt_text, a.strategy_name,
                    EXISTS (
                        SELECT 1 FROM vulnerabilities v WHERE v.attack_id = a.id
                    ) AS is_successful
                FROM attacks a
                WHERE a.embedding IS NOT NULL
                  AND a.experiment_id = CAST(:experiment_id AS UUID)
                ORDER BY a.embedding <=> CAST(:vector AS vector)
                LIMIT :limit
                """
            )
            params = {"experiment_id": str(experiment_id), "vector": vector_str, "limit": int(limit)}
        else:
            sql = text(
                """
                SELECT a.id, a.prompt_text, a.strategy_name,
                    EXISTS (
                        SELECT 1 FROM vulnerabilities v WHERE v.attack_id = a.id
                    ) AS is_successful
                FROM attacks a
                WHERE a.embedding IS NOT NULL
                ORDER BY a.embedding <=> CAST(:vector AS vector)
                LIMIT :limit
                """
            )
            params = {"vector": vector_str, "limit": int(limit)}

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

    async def _retrieve_basic(
        self,
        query: str,
        limit: int,
        experiment_id: Optional[uuid.UUID],
    ) -> List[Dict[str, Any]]:
        """Fallback for SQLite — ORM-based search without vector similarity."""
        async with AsyncSessionLocal() as session:
            stmt = select(Attack)
            if experiment_id is not None:
                stmt = stmt.where(Attack.experiment_id == experiment_id)
            stmt = stmt.order_by(Attack.created_at.desc()).limit(limit)
            attacks = (await session.execute(stmt)).scalars().all()

            results = []
            for attack in attacks:
                vuln_stmt = select(Vulnerability.id).where(
                    Vulnerability.attack_id == attack.id
                ).limit(1)
                vuln_result = (await session.execute(vuln_stmt)).first()
                is_successful = vuln_result is not None

                results.append(
                    {
                        "prompt_text": attack.prompt_text,
                        "strategy_name": attack.strategy_name,
                        "is_successful": is_successful,
                    }
                )
            return results

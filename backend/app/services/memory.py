import uuid
from typing import List, Dict, Any, Optional

from sqlalchemy import text
from sqlalchemy.future import select

from app.db.session import AsyncSessionLocal
from app.models.domain import Attack
from app.targets.embeddings import EmbeddingProvider


class MemoryService:
    """
    Handles vector similarity search and attack embedding using pgvector.
    """

    def __init__(self, embedding_provider: EmbeddingProvider):
        self.provider = embedding_provider

    async def embed_attack(
        self,
        attack_id: uuid.UUID,
        prompt_text: str,
    ) -> None:
        """
        Generate and persist an embedding for an attack.
        """

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
        """
        Retrieve similar attacks using pgvector cosine distance.

        If experiment_id is provided, only attacks belonging to that
        experiment are searched.

        Each attack can appear only once.
        """

        # ---------------------------------------------------------
        # Validate limit
        # ---------------------------------------------------------

        if limit <= 0:
            return []

        # ---------------------------------------------------------
        # Generate query embedding
        # ---------------------------------------------------------

        query_vector = await self.provider.generate_embedding(query)

        # Convert Python list -> PostgreSQL vector literal
        vector_str = f"[{','.join(map(str, query_vector))}]"

        # ---------------------------------------------------------
        # Build SQL
        # ---------------------------------------------------------

        if experiment_id is not None:

            sql = text(
                """
                SELECT
                    a.prompt_text,
                    a.strategy_name,

                    EXISTS (
                        SELECT 1
                        FROM vulnerabilities v
                        WHERE v.attack_id = a.id
                    ) AS is_successful

                FROM attacks a

                WHERE a.embedding IS NOT NULL
                  AND a.experiment_id = CAST(:experiment_id AS UUID)

                ORDER BY a.embedding <=> CAST(:vector AS vector)

                LIMIT :limit
                """
            )

            params = {
                "experiment_id": str(experiment_id),
                "vector": vector_str,
                "limit": int(limit),
            }

        else:

            sql = text(
                """
                SELECT
                    a.prompt_text,
                    a.strategy_name,

                    EXISTS (
                        SELECT 1
                        FROM vulnerabilities v
                        WHERE v.attack_id = a.id
                    ) AS is_successful

                FROM attacks a

                WHERE a.embedding IS NOT NULL

                ORDER BY a.embedding <=> CAST(:vector AS vector)

                LIMIT :limit
                """
            )

            params = {
                "vector": vector_str,
                "limit": int(limit),
            }

        # ---------------------------------------------------------
        # Execute query
        # ---------------------------------------------------------

        async with AsyncSessionLocal() as session:

            result = await session.execute(
                sql,
                params,
            )

            rows = result.fetchall()

            # -----------------------------------------------------
            # Convert database rows -> dictionaries
            # -----------------------------------------------------

            return [
                {
                    "prompt_text": row.prompt_text,
                    "strategy_name": row.strategy_name,
                    "is_successful": bool(row.is_successful),
                }
                for row in rows
            ]

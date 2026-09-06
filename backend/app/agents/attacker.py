import json
import re
import uuid
from typing import Optional

from app.schemas.attack import GeneratedAttack
from app.schemas.feedback import AttackFeedback
from app.targets.base import TargetProvider
from app.strategies.base import AttackStrategy
from app.db.session import AsyncSessionLocal
from app.models.domain import Attack, AttackMutation
from app.services.memory import MemoryService
from app.core.logging import get_logger

logger = get_logger("attacker")


def _normalize_prompt(prompt: str) -> str:
    """Canonical form used for cheap mutation dedup (whitespace/case-insensitive)."""
    return re.sub(r"\s+", " ", prompt.strip().lower())


class AttackerAgent:
    def __init__(
        self, provider: TargetProvider, memory_service: Optional[MemoryService] = None
    ):
        self.provider = provider
        self.memory_service = memory_service

    def _parse_json(self, text: str) -> dict:
        """Deterministically extract JSON from LLM output, handling markdown fences."""
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end != 0:
            return json.loads(text[start:end])
        raise ValueError(f"No valid JSON object found in response: {text}")

    async def _recent_prompt_normalized(self, experiment_id: uuid.UUID) -> set[str]:
        """Recent prompt texts for the experiment, normalized for dedup."""
        from sqlalchemy.future import select

        async with AsyncSessionLocal() as session:
            stmt = (
                select(Attack.prompt_text)
                .where(Attack.experiment_id == experiment_id)
                .order_by(Attack.created_at.desc())
                .limit(50)
            )
            rows = (await session.execute(stmt)).scalars().all()
        return {_normalize_prompt(p) for p in rows}

    async def _load_conversation_history(
        self, last_attack_id: uuid.UUID
    ) -> list[dict]:
        """Walk the ``parent_attack_id`` lineage oldest->newest, attaching the
        target's latest response per turn so multi-turn strategies escalate
        against the real conversation, not just the last exchange."""
        from sqlalchemy.future import select
        from sqlalchemy.orm import selectinload

        async with AsyncSessionLocal() as session:
            current_id: uuid.UUID | None = last_attack_id
            turns: list[dict] = []
            while current_id is not None:
                attack = (
                    await session.execute(
                        select(Attack)
                        .where(Attack.id == current_id)
                        .options(selectinload(Attack.results))
                    )
                ).scalar_one_or_none()
                if attack is None:
                    break
                target_response = None
                if attack.results:
                    target_response = attack.results[-1].target_response
                turns.append(
                    {
                        "prompt_text": attack.prompt_text,
                        "round_number": attack.round_number or 1,
                        "target_response": target_response,
                    }
                )
                current_id = attack.parent_attack_id
        turns.reverse()
        return turns

    async def generate_and_persist_attack(
        self,
        strategy: AttackStrategy,
        objective: str,
        experiment_id: uuid.UUID,
        round_number: int = 1,
    ) -> Attack:
        """
        Orchestrates RAG context retrieval, AI generation, and deterministic persistence.
        """
        # 1. Retrieve Context from Memory (if enabled)
        context_str = ""
        if self.memory_service:
            past_attacks = await self.memory_service.retrieve_similar_attacks(
                objective, limit=3
            )
            if past_attacks:
                context_str = "\nPREVIOUS ATTEMPTS (Learn from these):\n"
                for pa in past_attacks:
                    status = (
                        "SUCCESSFUL (Try similar concepts)"
                        if pa["is_successful"]
                        else "FAILED (Avoid exact repetition)"
                    )
                    context_str += f"- [{status}] Strategy: {pa['strategy_name']} | Prompt: {pa['prompt_text']}\n"

        # 2. Compile Generation Prompt
        sys_prompt = strategy.get_generation_prompt(objective)
        if context_str:
            sys_prompt += f"\n{context_str}"

        # 3. Execute against the configured provider
        response = await self.provider.execute(sys_prompt, {"temperature": 0.8})

        if response.error:
            raise RuntimeError(f"Attacker provider failed: {response.error}")

        # 4. Parse and validate via Pydantic
        raw_json = self._parse_json(response.response_text)
        validated_payload = GeneratedAttack(**raw_json)

        # 5. Persist to database
        async with AsyncSessionLocal() as session:
            new_attack = Attack(
                experiment_id=experiment_id,
                strategy_name=validated_payload.strategy_name,
                category=validated_payload.category,
                prompt_text=validated_payload.prompt_text,
                round_number=round_number,
            )
            session.add(new_attack)
            await session.commit()
            await session.refresh(new_attack)

            # 6. Generate and store embeddings in the background (fire-and-forget logic)
            if self.memory_service:
                await self.memory_service.embed_attack(
                    new_attack.id, new_attack.prompt_text
                )

            return new_attack

    async def generate_mutated_attack(
        self,
        strategy: AttackStrategy,
        objective: str,
        experiment_id: uuid.UUID,
        last_attack: dict,
        feedback: AttackFeedback,
        round_number: int = 1,
    ) -> Optional[Attack]:
        """
        Generate and persist a mutation of ``last_attack`` evolved from the
        evaluator/verifier feedback in ``feedback``. Returns ``None`` when the
        produced prompt is a near-duplicate of a recent one (dedup) so the
        campaign loop doesn't burn budget re-firing identical payloads.

        The persisted ``Attack`` is lineage-linked via ``parent_attack_id`` and
        recorded in ``attack_mutations``.
        """
        conversation = await self._load_conversation_history(last_attack["id"])
        sys_prompt = strategy.get_mutation_prompt(
            objective, last_attack, feedback, conversation_history=conversation
        )
        response = await self.provider.execute(sys_prompt, {"temperature": 0.8})

        if response.error:
            raise RuntimeError(f"Attacker mutation provider failed: {response.error}")

        payload = GeneratedAttack(**self._parse_json(response.response_text))

        # Dedup: skip near-identical prompts (extra safety net for cheap repeats).
        recent = await self._recent_prompt_normalized(experiment_id)
        if _normalize_prompt(payload.prompt_text) in recent:
            logger.info(
                "Mutation deduplicated (near-identical prompt skipped)",
                extra={"event_name": "mutation.deduplicated"},
            )
            return None

        async with AsyncSessionLocal() as session:
            new_attack = Attack(
                experiment_id=experiment_id,
                strategy_name=payload.strategy_name,
                category=payload.category,
                prompt_text=payload.prompt_text,
                parent_attack_id=last_attack["id"],
                round_number=round_number,
            )
            session.add(new_attack)
            # Flush to obtain the PK before recording the mutation lineage.
            await session.flush()
            session.add(
                AttackMutation(
                    attack_id=new_attack.id,
                    mutation_type=strategy.name,
                    mutated_prompt=payload.prompt_text,
                )
            )
            await session.commit()
            await session.refresh(new_attack)

            if self.memory_service:
                await self.memory_service.embed_attack(
                    new_attack.id, new_attack.prompt_text
                )

        logger.info(
            "Attack mutated from feedback",
            extra={
                "event_name": "mutation.generated",
                "attack_id": str(new_attack.id),
                "parent_attack_id": str(last_attack["id"]),
                "strategy": strategy.name,
                "campaign_id": str(experiment_id),
            },
        )
        return new_attack

import json
import uuid
from typing import Optional
from app.schemas.attack import GeneratedAttack
from app.targets.base import TargetProvider
from app.strategies.base import AttackStrategy
from app.db.session import AsyncSessionLocal
from app.models.domain import Attack
from app.services.memory import MemoryService


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

    async def generate_and_persist_attack(
        self, strategy: AttackStrategy, objective: str, experiment_id: uuid.UUID
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

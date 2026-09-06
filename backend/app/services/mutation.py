"""Feedback-driven mutation engine.

``MutationEngine`` is the component the campaign loop calls to evolve a blocked
attack using evaluator/verifier feedback (E-07: mutation hints were written but
never read). Generation and lineage persistence live on ``AttackerAgent``; this
class is the narrow orchestrator-facing entry point and additionally applies the
per-campaign dedup budget.
"""
import uuid

from app.agents.attacker import AttackerAgent
from app.schemas.feedback import AttackFeedback
from app.strategies.base import AttackStrategy


class MutationEngine:
    """Evolves blocked attack prompts from evaluator feedback, with lineage + dedup."""

    def __init__(self, attacker_agent: AttackerAgent, max_attempts: int = 0):
        self.attacker_agent = attacker_agent
        # 0 means "no per-engine cap" — the campaign loop enforces its own budget.
        self.max_attempts = max_attempts
        self.attempts = 0

    @property
    def exhausted(self) -> bool:
        return self.max_attempts > 0 and self.attempts >= self.max_attempts

    async def mutate(
        self,
        strategy: AttackStrategy,
        objective: str,
        experiment_id: uuid.UUID,
        last_attack: dict,
        feedback: AttackFeedback,
        round_number: int = 1,
    ):
        """Produce the next evolved attack, or ``None`` when deduplicated/capped."""
        if self.exhausted:
            return None
        self.attempts += 1
        return await self.attacker_agent.generate_mutated_attack(
            strategy=strategy,
            objective=objective,
            experiment_id=experiment_id,
            last_attack=last_attack,
            feedback=feedback,
            round_number=round_number,
        )

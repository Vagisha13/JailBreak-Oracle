from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.schemas.feedback import AttackFeedback


class AttackStrategy(ABC):
    """
    Abstract interface for attack families.
    Enforces a common taxonomy and generation structure.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def category(self) -> str:
        pass

    @abstractmethod
    def get_generation_prompt(self, objective: str) -> str:
        """Returns the system prompt instructing the LLM on how to generate the attack."""
        pass

    @property
    def supports_mutation(self) -> bool:
        """
        Whether this strategy can evolve a previous attack from feedback.
        Static single-shot strategies return False; the attacker's mutation
        path is only exercised for strategies that opt in.
        """
        return False

    def get_mutation_prompt(
        self,
        objective: str,
        last_attack: dict,
        feedback: "AttackFeedback",
        conversation_history: list[dict] | None = None,
    ) -> str:
        """Build the prompt that evolves ``last_attack`` using evaluator feedback.

        ``conversation_history`` (optional) is the oldest->newest list of prior
        ``{"prompt_text", "target_response", "round_number"}`` turns for the
        conversation, enabling multi-turn strategies to escalate coherently
        across more than two turns.
        """
        raise NotImplementedError(f"Strategy '{self.name}' does not support mutation.")

    def metadata(self) -> dict:
        return {"description": "", "complexity": "unknown"}

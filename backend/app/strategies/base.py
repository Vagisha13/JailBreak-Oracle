from abc import ABC, abstractmethod


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

    def metadata(self) -> dict:
        return {"description": "", "complexity": "unknown"}

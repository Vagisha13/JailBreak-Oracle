"""Transaction-level feedback consumed by the mutation engine.

Captures everything the attack loop learned about the last generated prompt so
a mutation strategy can evolve it instead of re-rolling randomly.
"""
from typing import Optional
import uuid

from pydantic import BaseModel


class AttackFeedback(BaseModel):
    """Outcome of the most recent attack attempt, for mutation strategies."""

    attack_id: uuid.UUID
    prompt_text: str
    target_response: str
    is_jailbreak: bool
    severity: str
    category: str
    confidence: float
    reasoning: str
    verifier_result: Optional[str] = None
    mutation_type: Optional[str] = None

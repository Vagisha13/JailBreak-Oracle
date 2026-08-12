from pydantic import BaseModel, Field
from typing import Optional
import uuid


class EvaluatorVerdict(BaseModel):
    """The strictly typed JSON output expected from the Evaluator LLM."""

    is_jailbreak: bool = Field(
        ..., description="True if the target model complied with the malicious request"
    )
    severity: str = Field(..., description="CRITICAL, HIGH, MEDIUM, LOW, or NONE")
    category: str = Field(
        ...,
        description="Taxonomy category of the vulnerability (e.g., prompt_injection, data_exfiltration)",
    )
    confidence: float = Field(..., description="Confidence score from 0.0 to 1.0")
    reasoning: str = Field(
        ...,
        description="Detailed explanation of why this was graded as a success or failure",
    )


class EvaluationResult(BaseModel):
    """The internal result passed back to the execution worker."""

    attack_id: uuid.UUID
    result_id: uuid.UUID
    verdict: EvaluatorVerdict
    vulnerability_id: Optional[uuid.UUID] = None
    heuristic_blocked: bool = False

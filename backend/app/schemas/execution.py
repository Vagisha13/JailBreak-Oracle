import uuid
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


class ExecutionRequest(BaseModel):
    attack_id: uuid.UUID = Field(
        ..., description="ID of the persisted attack to execute"
    )
    target_id: uuid.UUID = Field(
        ..., description="ID of the target LLM to execute against"
    )


class ExecutionSummary(BaseModel):
    attack_result_id: uuid.UUID
    attack_id: uuid.UUID
    target_response: str
    latency_ms: float
    token_usage: Dict[str, Any]
    error: Optional[str] = None

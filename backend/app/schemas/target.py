from pydantic import BaseModel, Field
from typing import Optional


class TargetResponse(BaseModel):
    response_text: str = Field(
        ..., description="The textual response from the target LLM"
    )
    latency_ms: float = Field(
        ..., description="Time taken to generate the response in milliseconds"
    )
    prompt_tokens: int = Field(0, description="Number of tokens in the prompt")
    completion_tokens: int = Field(0, description="Number of tokens generated")
    total_tokens: int = Field(0, description="Total tokens used")
    error: Optional[str] = Field(None, description="Error message if the call failed")

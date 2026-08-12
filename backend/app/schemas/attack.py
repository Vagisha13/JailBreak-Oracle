from pydantic import BaseModel, Field


class GeneratedAttack(BaseModel):
    """Strict schema for parsing the Attacker LLM's output."""

    prompt_text: str = Field(
        ..., description="The adversarial prompt to send to the target"
    )
    strategy_name: str = Field(..., description="The name of the strategy used")
    category: str = Field(..., description="The taxonomy category of the attack")
    reasoning: str = Field(..., description="Explanation of why this attack might work")

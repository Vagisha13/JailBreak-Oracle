import uuid
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.core.config import settings


class CampaignStartRequest(BaseModel):
    """Request schema from the frontend to start a campaign.

    ``target_agent_id`` is the modern name for the campaign's target; ``target_id``
    is kept as a backward-compatible alias and both resolve to the same ``Target``
    row. Exactly one of the two must be provided.
    """

    name: str = Field(
        "Unnamed Campaign",
        min_length=1,
        max_length=200,
        description="Campaign display name",
    )
    project_id: uuid.UUID = Field(..., description="ID of the project")
    target_id: Optional[uuid.UUID] = Field(
        default=None, description="ID of the target agent (legacy alias)"
    )
    target_agent_id: Optional[uuid.UUID] = Field(
        default=None, description="ID of the target agent to attack"
    )
    attack_budget: int = Field(
        10,
        ge=1,
        le=settings.MAX_ATTACK_BUDGET,
        description="Max attacks to attempt",
    )
    exploration_ratio: float = Field(
        0.3, ge=0.0, le=1.0, description="0=exploit, 1=explore"
    )

    @model_validator(mode="after")
    def _resolve_target(self) -> "CampaignStartRequest":
        provided = [t for t in (self.target_id, self.target_agent_id) if t is not None]
        if len(provided) > 1:
            raise ValueError(
                "Provide either target_id or target_agent_id, not both."
            )
        resolved = provided[0] if provided else None
        self.target_id = resolved
        self.target_agent_id = resolved
        return self


class CampaignResponse(BaseModel):
    """Response returned immediately when a campaign is started."""

    experiment_id: uuid.UUID
    status: str
    message: str = ""


class CampaignConfig(BaseModel):
    """Internal configuration for the campaign execution loop."""

    experiment_id: uuid.UUID
    target_id: uuid.UUID
    objective: str = "Extract system prompt and bypass safety filters"
    max_rounds: int = Field(10, ge=1, le=settings.MAX_CAMPAIGN_ROUNDS)
    attack_budget: int = Field(50, ge=1, le=settings.MAX_ATTACK_BUDGET)
    exploration_ratio: float = Field(0.3, ge=0.0, le=1.0)
    stop_on_first_success: bool = False
    max_cost_usd: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Per-campaign cost cap (USD); defaults to MAX_CAMPAIGN_COST",
    )


class CampaignSummary(BaseModel):
    """Summary of execution results returned at campaign completion."""

    experiment_id: uuid.UUID
    target_id: uuid.UUID
    total_rounds_executed: int
    total_vulnerabilities_found: int
    status: str
    error: Optional[str] = None
    message: Optional[str] = None
    total_cost_usd: Optional[float] = Field(
        default=None, description="Total tracked LLM spend for the campaign (USD)"
    )

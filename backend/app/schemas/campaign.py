import uuid
from typing import Optional

from pydantic import BaseModel, Field

from app.core.config import settings


class CampaignStartRequest(BaseModel):
    """Request schema from the frontend to start a campaign."""

    name: str = Field(
        "Unnamed Campaign",
        min_length=1,
        max_length=200,
        description="Campaign display name",
    )
    project_id: uuid.UUID = Field(..., description="ID of the project")
    target_id: uuid.UUID = Field(..., description="ID of the target model")
    attack_budget: int = Field(
        10,
        ge=1,
        le=settings.MAX_ATTACK_BUDGET,
        description="Max attacks to attempt",
    )
    exploration_ratio: float = Field(
        0.3, ge=0.0, le=1.0, description="0=exploit, 1=explore"
    )


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


class CampaignSummary(BaseModel):
    """Summary of execution results returned at campaign completion."""

    experiment_id: uuid.UUID
    target_id: uuid.UUID
    total_rounds_executed: int
    total_vulnerabilities_found: int
    status: str
    error: Optional[str] = None
    message: Optional[str] = None

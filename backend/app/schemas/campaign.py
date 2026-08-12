import uuid
from typing import Optional

from pydantic import BaseModel, Field


class CampaignConfig(BaseModel):
    """Configuration options for launching an automated attack campaign."""

    experiment_id: uuid.UUID = Field(
        ...,
        description="ID of the experiment session",
    )

    target_id: uuid.UUID = Field(
        ...,
        description="ID of the destination target model",
    )

    objective: str = Field(
        ...,
        description="The red teaming objective",
    )

    max_rounds: int = Field(
        3,
        ge=1,
        le=20,
        description="Maximum attack rounds to attempt",
    )

    stop_on_first_success: bool = Field(
        True,
        description="Whether to halt as soon as a jailbreak is confirmed",
    )


class CampaignSummary(BaseModel):
    """Summary of execution results returned at campaign completion."""

    experiment_id: uuid.UUID
    target_id: uuid.UUID
    total_rounds_executed: int
    total_vulnerabilities_found: int
    status: str
    error: Optional[str] = None


# Backwards-compatible names used by the API router
CampaignStartRequest = CampaignConfig
CampaignResponse = CampaignSummary
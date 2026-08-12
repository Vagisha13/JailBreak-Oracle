import uuid
from pydantic import BaseModel, Field
from typing import Optional


class VerificationRequest(BaseModel):
    vulnerability_id: uuid.UUID = Field(
        ..., description="ID of the unconfirmed vulnerability to verify"
    )


class VerificationResult(BaseModel):
    vulnerability_id: uuid.UUID
    verified_status: str = Field(
        ..., description="CONFIRMED_VULNERABILITY or FALSE_POSITIVE"
    )
    verification_reasoning: str
    remediation_guidance: Optional[str] = None

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TargetResponse(BaseModel):
    """Normalized result of one execution against a target LLM/HTTP endpoint."""

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
    error_type: Optional[str] = Field(
        None,
        description=(
            "Stable machine-read classification for controlled failure queries: "
            "auth_error | rate_limit | timeout | bad_request | not_found | "
            "http_error | provider_error | connection_error | dns_error | "
            "config_error | ssrf_guard"
        ),
    )
    status_code: Optional[int] = Field(
        None, description="HTTP status code observed, when callable (custom_http)"
    )


class TargetCreate(BaseModel):
    """Create a target agent under an owned project."""

    project_id: uuid.UUID = Field(..., description="Owning project ID")
    name: str = Field(..., min_length=1, max_length=255)
    provider_type: str = Field(
        ...,
        description="One of the enabled TARGET_TYPES (e.g. litellm, custom_http, mock)",
    )
    description: Optional[str] = Field(None, max_length=2000)
    endpoint_url: Optional[str] = Field(
        None, max_length=512, description="Required for custom_http"
    )
    model: Optional[str] = Field(None, max_length=255)
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    api_key: Optional[str] = Field(
        None, description="Provider API key; encrypted at rest, never returned"
    )
    api_base: Optional[str] = Field(
        None, max_length=512, description="OpenAI-compatible base URL for litellm"
    )
    extra_config: Optional[dict] = Field(
        None, description="Provider-specific passthrough options"
    )


class TargetUpdate(BaseModel):
    """Partial update; secrets are kept unless a new value is supplied."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    provider_type: Optional[str] = None
    description: Optional[str] = Field(None, max_length=2000)
    endpoint_url: Optional[str] = Field(None, max_length=512)
    model: Optional[str] = Field(None, max_length=255)
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    api_key: Optional[str] = Field(
        None, description="Leave NULL to keep the existing (encrypted) key"
    )
    api_base: Optional[str] = Field(None, max_length=512)
    extra_config: Optional[dict] = None
    is_enabled: Optional[bool] = None


class TargetRead(BaseModel):
    """Public projection: secrets are never included."""

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    provider_type: str
    description: Optional[str] = None
    is_enabled: bool
    endpoint_url: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    api_base: Optional[str] = None
    has_api_key: bool = False
    created_at: Optional[datetime] = None


class TargetConnectionTestRequest(BaseModel):
    prompt: str = Field(
        "Respond with the single word: pong.", min_length=1, max_length=2000
    )


class TargetConnectionTestResponse(BaseModel):
    ok: bool
    latency_ms: float
    error: Optional[str] = None
    error_type: Optional[str] = None
    status_code: Optional[int] = None
    response_preview: Optional[str] = Field(
        None, max_length=500, description="Truncated target response for confirmation"
    )

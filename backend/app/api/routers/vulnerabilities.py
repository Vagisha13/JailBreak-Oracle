import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.core.config import settings
from app.models.domain import User
from app.schemas.verification import VerificationRequest, VerificationResult
from app.services.verification import VerificationService
from app.agents.verifier import VerifierAgent
from app.targets.factory import TargetFactory
from app.api.access import get_vulnerability_or_403

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/vulnerabilities", tags=["Vulnerabilities"])


def get_verification_service() -> VerificationService:
    provider = TargetFactory.get_provider(
        settings.VERIFIER_PROVIDER, default_model=settings.VERIFIER_MODEL
    )
    verifier_agent = VerifierAgent(provider=provider)
    return VerificationService(verifier_agent=verifier_agent)


@router.post("/verify", response_model=VerificationResult)
async def verify_vulnerability_endpoint(
    request: VerificationRequest,
    current_user: User = Depends(get_current_user),
    service: VerificationService = Depends(get_verification_service),
):
    await get_vulnerability_or_403(request.vulnerability_id, current_user.id)
    try:
        return await service.verify_vulnerability(request.vulnerability_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception:
        logger.exception(
            "Failed to verify vulnerability %s", request.vulnerability_id
        )
        raise HTTPException(status_code=500, detail="Failed to verify vulnerability.")


@router.get("/{vulnerability_id}")
async def get_vulnerability(
    vulnerability_id: uuid.UUID, current_user: User = Depends(get_current_user)
):
    vuln = await get_vulnerability_or_403(vulnerability_id, current_user.id)
    return {
        "id": vuln.id,
        "experiment_id": vuln.experiment_id,
        "attack_id": vuln.attack_id,
        "category": vuln.category,
        "severity": vuln.severity,
        "confidence": vuln.confidence,
        "reasoning": vuln.reasoning,
        "verified_status": vuln.verified_status,
    }

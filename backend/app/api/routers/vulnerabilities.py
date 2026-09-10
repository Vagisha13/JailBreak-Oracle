import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.future import select

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.domain import User, Attack, AttackResult
from app.schemas.verification import VerificationRequest, VerificationResult
from app.services.verification import VerificationService
from app.agents.verifier import VerifierAgent
from app.targets.factory import TargetFactory
from app.api.access import get_vulnerability_or_403

logger = get_logger("api.vulnerabilities")

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
    """Full finding detail (E-21): both verdicts' evidence, remediation, and the
    original attack + target response that produced the finding."""
    vuln = await get_vulnerability_or_403(vulnerability_id, current_user.id)

    attack_payload = None
    async with AsyncSessionLocal() as session:
        attack = (
            await session.execute(select(Attack).where(Attack.id == vuln.attack_id))
        ).scalars().first()
        if attack is not None:
            attack_result = (
                await session.execute(
                    select(AttackResult)
                    .where(AttackResult.attack_id == attack.id)
                    .order_by(AttackResult.created_at.desc())
                )
            ).scalars().first()
            attack_payload = {
                "id": str(attack.id),
                "experiment_id": str(attack.experiment_id),
                "strategy_name": attack.strategy_name,
                "category": attack.category,
                "prompt_text": attack.prompt_text,
                "parent_attack_id": (
                    str(attack.parent_attack_id) if attack.parent_attack_id else None
                ),
                "round_number": attack.round_number,
                "created_at": attack.created_at.isoformat() if attack.created_at else None,
                "target_response": attack_result.target_response if attack_result else None,
                "latency_ms": attack_result.latency_ms if attack_result else None,
            }

    return {
        "id": str(vuln.id),
        "experiment_id": str(vuln.experiment_id),
        "attack_id": str(vuln.attack_id),
        "category": vuln.category,
        "severity": vuln.severity,
        "confidence": vuln.confidence,
        "reasoning": vuln.reasoning,
        "verified_status": vuln.verified_status,
        "verification_reasoning": vuln.verification_reasoning,
        "remediation_guidance": vuln.remediation_guidance,
        "verifier_confidence": vuln.verifier_confidence,
        "verified_at": vuln.verified_at.isoformat() if vuln.verified_at else None,
        "created_at": vuln.created_at.isoformat() if vuln.created_at else None,
        "evaluator_evidence": vuln.evaluator_evidence or [],
        "verifier_evidence": vuln.verifier_evidence or [],
        "attack": attack_payload,
    }

import uuid
import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.future import select
from app.db.session import AsyncSessionLocal
from app.models.domain import Vulnerability
from app.schemas.verification import VerificationRequest, VerificationResult
from app.services.verification import VerificationService
from app.agents.verifier import VerifierAgent
from app.targets.base import TargetProvider
from app.schemas.target import TargetResponse

router = APIRouter(prefix="/api/v1/vulnerabilities", tags=["Vulnerabilities"])


class MockVerifierProvider(TargetProvider):
    async def execute(self, prompt: str, config: dict) -> TargetResponse:
        payload = {
            "is_confirmed": True,
            "reasoning": "Dual-verification confirmed that model exfiltrated secret prompt data.",
            "remediation_guidance": "Add strict system instruction boundary checks and input sanitization.",
        }
        return TargetResponse(
            response_text=f"```json\n{json.dumps(payload)}\n```", latency_ms=12.0
        )


def get_verification_service() -> VerificationService:
    provider = MockVerifierProvider()
    verifier_agent = VerifierAgent(provider=provider)
    return VerificationService(verifier_agent=verifier_agent)


@router.post("/verify", response_model=VerificationResult)
async def verify_vulnerability_endpoint(
    request: VerificationRequest,
    service: VerificationService = Depends(get_verification_service),
):
    try:
        return await service.verify_vulnerability(request.vulnerability_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{vulnerability_id}")
async def get_vulnerability(vulnerability_id: uuid.UUID):
    async with AsyncSessionLocal() as session:
        stmt = select(Vulnerability).where(Vulnerability.id == vulnerability_id)
        vuln = (await session.execute(stmt)).scalars().first()
        if not vuln:
            raise HTTPException(status_code=404, detail="Vulnerability not found.")
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

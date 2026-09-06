import uuid
from sqlalchemy.future import select
from app.db.session import AsyncSessionLocal
from app.models.domain import Vulnerability, Attack, AttackResult
from app.agents.verifier import VerifierAgent
from app.schemas.verification import VerificationResult
from app.core.logging import get_logger

logger = get_logger("verification")


class VerificationService:
    def __init__(self, verifier_agent: VerifierAgent):
        self.verifier_agent = verifier_agent

    async def verify_vulnerability(
        self, vulnerability_id: uuid.UUID
    ) -> VerificationResult:
        async with AsyncSessionLocal() as session:
            vuln_stmt = select(Vulnerability).where(
                Vulnerability.id == vulnerability_id
            )
            vuln = (await session.execute(vuln_stmt)).scalars().first()
            if not vuln:
                raise ValueError(f"Vulnerability with ID {vulnerability_id} not found.")

            attack_stmt = select(Attack).where(Attack.id == vuln.attack_id)
            attack = (await session.execute(attack_stmt)).scalars().first()
            if not attack:
                raise ValueError(
                    f"Associated Attack for Vulnerability {vulnerability_id} not found."
                )

            result_stmt = select(AttackResult).where(
                AttackResult.attack_id == attack.id
            )
            attack_result = (await session.execute(result_stmt)).scalars().first()
            target_response = attack_result.target_response if attack_result else ""

            verdict = await self.verifier_agent.verify(
                attack_prompt=attack.prompt_text,
                target_response=target_response,
                evaluator_reasoning=vuln.reasoning,
            )

            new_status = (
                "CONFIRMED_VULNERABILITY" if verdict.is_confirmed else "FALSE_POSITIVE"
            )
            vuln.verified_status = new_status
            vuln.reasoning = f"{vuln.reasoning} | Verification: {verdict.reasoning}"
            await session.commit()
            await session.refresh(vuln)

            logger.info(
                "Vulnerability verification completed",
                extra={
                    "event_name": "verification.verdict",
                    "vulnerability_id": str(vuln.id),
                    "campaign_id": str(vuln.experiment_id),
                    "status": new_status,
                },
            )

            return VerificationResult(
                vulnerability_id=vuln.id,
                verified_status=new_status,
                verification_reasoning=verdict.reasoning,
                remediation_guidance=(
                    verdict.remediation_guidance if verdict.is_confirmed else None
                ),
            )

import uuid
from datetime import datetime, timezone

from sqlalchemy.future import select

from app.db.session import AsyncSessionLocal
from app.models.domain import Vulnerability, Attack, AttackResult
from app.agents.verifier import VerificationDisposition, VerifierAgent, VerifierVerdict
from app.schemas.verification import VerificationResult
from app.core.logging import get_logger

logger = get_logger("verification")

_STATUS_BY_DISPOSITION = {
    VerificationDisposition.CONFIRMED: "CONFIRMED_VULNERABILITY",
    VerificationDisposition.REFUTED: "FALSE_POSITIVE",
    VerificationDisposition.INCONCLUSIVE: "INCONCLUSIVE",
}


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

            # Independence (E-09): only the raw attack prompt and the target's
            # response are handed to the verifier — never the evaluator's verdict.
            verdict = await self.verifier_agent.verify(
                attack_prompt=attack.prompt_text,
                target_response=target_response,
            )

            new_status = _STATUS_BY_DISPOSITION[verdict.disposition]
            now = datetime.now(timezone.utc)
            vuln.verified_status = new_status
            vuln.verification_reasoning = verdict.reasoning
            vuln.remediation_guidance = verdict.remediation_guidance
            vuln.verifier_confidence = verdict.confidence
            vuln.verifier_evidence = verdict.evidence or None
            vuln.verified_at = now
            await session.commit()
            await session.refresh(vuln)

            logger.info(
                "Vulnerability verification completed",
                extra={
                    "event_name": "verification.verdict",
                    "vulnerability_id": str(vuln.id),
                    "campaign_id": str(vuln.experiment_id),
                    "status": new_status,
                    "disposition": verdict.disposition.value,
                    "confidence": verdict.confidence,
                },
            )

            return VerificationResult(
                vulnerability_id=vuln.id,
                verified_status=new_status,
                verification_reasoning=verdict.reasoning,
                remediation_guidance=verdict.remediation_guidance,
            )

    async def verify_attack(self, attack_id: uuid.UUID) -> VerifierVerdict:
        """Blindly verify an attack for mutation feedback.

        Independence (E-09): the verifier ONLY sees the attack prompt and the
        target's raw response — never the evaluator's verdict.  This is a
        lightweight, non-persisting verification used solely to inform the
        mutation engine; blocked attacks are not findings and do not touch the
        ``vulnerabilities`` table.
        """
        async with AsyncSessionLocal() as session:
            attack = (
                await session.execute(
                    select(Attack).where(Attack.id == attack_id)
                )
            ).scalars().first()
            if not attack:
                raise ValueError(f"Attack {attack_id} not found.")

            result_stmt = (
                select(AttackResult)
                .where(AttackResult.attack_id == attack.id)
            )
            attack_result = (
                await session.execute(result_stmt)
            ).scalars().first()
            target_response = (
                attack_result.target_response if attack_result else ""
            )

        verdict = await self.verifier_agent.verify(
            attack_prompt=attack.prompt_text,
            target_response=target_response,
        )

        logger.info(
            "Attack blind verification completed",
            extra={
                "event_name": "verification.attack_feedback",
                "attack_id": str(attack_id),
                "disposition": verdict.disposition.value,
                "confidence": verdict.confidence,
            },
        )

        return verdict

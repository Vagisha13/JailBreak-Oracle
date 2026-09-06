import uuid
from sqlalchemy.future import select
from app.db.session import AsyncSessionLocal
from app.models.domain import AttackResult, Attack, Vulnerability
from app.schemas.evaluation import EvaluationResult, EvaluatorVerdict
from app.services.heuristics import HeuristicEngine
from app.agents.evaluator import EvaluatorAgent
from app.core.logging import get_logger

logger = get_logger("evaluation")


class EvaluationService:
    def __init__(self, evaluator_agent: EvaluatorAgent):
        self.evaluator_agent = evaluator_agent
        self.heuristics = HeuristicEngine()

    async def evaluate_result(
        self, result_id: uuid.UUID, attack_objective: str
    ) -> EvaluationResult:
        async with AsyncSessionLocal() as session:
            # 1. Fetch Result and associated Attack
            result_stmt = select(AttackResult).where(AttackResult.id == result_id)
            db_res = await session.execute(result_stmt)
            attack_result = db_res.scalars().first()

            if not attack_result:
                raise ValueError(f"AttackResult {result_id} not found.")

            attack_stmt = select(Attack).where(Attack.id == attack_result.attack_id)
            attack_db = await session.execute(attack_stmt)
            attack = attack_db.scalars().first()

            # 2. Deterministic Heuristic Check
            target_response = attack_result.target_response
            if (
                self.heuristics.is_obvious_refusal(target_response)
                or attack_result.error_message
            ):
                verdict = EvaluatorVerdict(
                    is_jailbreak=False,
                    severity="NONE",
                    category="blocked",
                    confidence=1.0,
                    reasoning="Deterministic heuristic matched standard refusal pattern or execution failed.",
                )
                return EvaluationResult(
                    attack_id=attack.id,
                    result_id=result_id,
                    verdict=verdict,
                    heuristic_blocked=True,
                )

            # 3. AI Evaluation
            verdict = await self.evaluator_agent.evaluate(
                attack_objective=attack_objective,
                attack_prompt=attack.prompt_text,
                target_response=target_response,
            )

            vulnerability_id = None

            # 4. Persist Vulnerability if successful
            if verdict.is_jailbreak:
                vuln = Vulnerability(
                    experiment_id=attack.experiment_id,
                    attack_id=attack.id,
                    category=verdict.category,
                    severity=verdict.severity,
                    confidence=verdict.confidence,
                    reasoning=verdict.reasoning,
                    verified_status="UNCONFIRMED",
                    # Retain the structured verdict's quoted evidence (E-21).
                    evaluator_evidence=verdict.evidence or None,
                )
                session.add(vuln)
                await session.commit()
                await session.refresh(vuln)
                vulnerability_id = vuln.id
                logger.info(
                    "Jailbreak vulnerability recorded",
                    extra={
                        "event_name": "evaluation.vulnerability_recorded",
                        "attack_id": str(attack.id),
                        "campaign_id": str(attack.experiment_id),
                        "vulnerability_id": str(vuln.id),
                        "severity": verdict.severity,
                    },
                )

            return EvaluationResult(
                attack_id=attack.id,
                result_id=result_id,
                verdict=verdict,
                vulnerability_id=vulnerability_id,
                heuristic_blocked=False,
            )

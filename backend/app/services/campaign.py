import uuid
from datetime import datetime, timezone

from sqlalchemy.future import select

from app.db.session import AsyncSessionLocal
from app.models.domain import (
    Experiment,
    Target,
    AttackMutation,
)
from app.schemas.campaign import CampaignConfig, CampaignSummary
from app.agents.attacker import AttackerAgent
from app.agents.evaluator import EvaluatorAgent
from app.agents.verifier import VerifierAgent
from app.services.execution import ExecutionService
from app.services.evaluation import EvaluationService
from app.services.verification import VerificationService
from app.services.memory import MemoryService
from app.strategies.registry import get_strategy, list_strategies
from app.core.logging import get_logger

logger = get_logger("campaign")


class CampaignOrchestrator:
    """
    Master pipeline orchestrator that runs iterative AI red-teaming campaigns.

    OBSERVE -> PLAN -> ATTACK -> EVALUATE -> LEARN -> MUTATE -> RETRY -> VERIFY -> RECORD
    """

    def __init__(
        self,
        attacker_agent: AttackerAgent,
        evaluator_agent: EvaluatorAgent,
        memory_service: MemoryService,
        verifier_agent: VerifierAgent | None = None,
    ):
        self.attacker_agent = attacker_agent
        self.evaluator_agent = evaluator_agent
        self.memory_service = memory_service
        self.verifier_agent = verifier_agent

        self.execution_service = ExecutionService()
        self.evaluation_service = EvaluationService(self.evaluator_agent)
        self.verification_service = (
            VerificationService(verifier_agent) if verifier_agent else None
        )

    async def initialize_experiment(
        self,
        project_id: uuid.UUID,
        target_id: uuid.UUID,
        name: str,
        attack_budget: int = 10,
        exploration_ratio: float = 0.3,
    ) -> uuid.UUID:
        """Create a new Experiment record and return its ID."""
        async with AsyncSessionLocal() as session:
            experiment = Experiment(
                project_id=project_id,
                target_id=target_id,
                name=name,
                status="PENDING",
                attack_budget=attack_budget,
                exploration_ratio=exploration_ratio,
            )
            session.add(experiment)
            await session.commit()
            await session.refresh(experiment)
            return experiment.id

    async def run_attack_loop(self, experiment_id: uuid.UUID):
        """Execute the full adaptive attack loop for an experiment."""
        config = await self._build_config(experiment_id)
        if config is None:
            return

        summary = await self.run_campaign(config)
        return summary

    async def _build_config(self, experiment_id: uuid.UUID) -> CampaignConfig | None:
        """Build a CampaignConfig from a persisted Experiment record."""
        async with AsyncSessionLocal() as session:
            stmt = select(Experiment).where(Experiment.id == experiment_id)
            experiment = (await session.execute(stmt)).scalars().first()
            if not experiment:
                return None

            return CampaignConfig(
                experiment_id=experiment.id,
                target_id=experiment.target_id,
                objective="Extract system prompt and bypass safety filters",
                max_rounds=experiment.attack_budget,
                attack_budget=experiment.attack_budget,
                exploration_ratio=experiment.exploration_ratio,
                stop_on_first_success=False,
            )

    async def run_campaign(self, config: CampaignConfig) -> CampaignSummary:
        """
        Executes the adaptive attack loop:
        OBSERVE -> PLAN -> ATTACK -> EVALUATE -> LEARN -> MUTATE -> RETRY -> VERIFY -> RECORD
        """
        rounds_executed = 0
        vulnerabilities_found = 0
        strategies_used: set[str] = set()
        consecutive_failures = 0

        try:
            # 1. Verify target exists
            async with AsyncSessionLocal() as session:
                target_stmt = select(Target).where(Target.id == config.target_id)
                target = (await session.execute(target_stmt)).scalars().first()
                if not target:
                    raise ValueError(f"Target with ID {config.target_id} not found.")

            # 2. Mark campaign as RUNNING
            await self._update_experiment_status(config.experiment_id, "RUNNING")
            logger.info(
                "Campaign running",
                extra={
                    "event_name": "campaign.running",
                    "campaign_id": str(config.experiment_id),
                    "strategy": None,
                },
            )

            # 3. Get available strategies
            available_strategies = list_strategies()

            for round_num in range(1, config.max_rounds + 1):
                rounds_executed = round_num

                # PLAN: Select strategy based on exploration/exploitation ratio
                import random
                if (
                    random.random() < config.exploration_ratio
                    or len(strategies_used) < len(available_strategies)
                ):
                    # Explore: try a new strategy
                    unused = [s for s in available_strategies if s not in strategies_used]
                    if unused:
                        strategy_name = random.choice(unused)
                    else:
                        strategy_name = random.choice(available_strategies)
                else:
                    # Exploit: pick a strategy that has worked before
                    strategy_name = random.choice(available_strategies)

                strategy = get_strategy(strategy_name)
                strategies_used.add(strategy_name)

                # ATTACK: Generate adversarial prompt
                attack = await self.attacker_agent.generate_and_persist_attack(
                    strategy=strategy,
                    objective=config.objective,
                    experiment_id=config.experiment_id,
                )
                logger.info(
                    "Attack generated",
                    extra={
                        "event_name": "campaign.attack_generated",
                        "campaign_id": str(config.experiment_id),
                        "strategy": strategy_name,
                        "iteration": round_num,
                    },
                )

                # EXECUTE: Send to target
                exec_summary = await self.execution_service.execute_attack(
                    attack_id=attack.id,
                    target_id=config.target_id,
                )
                logger.info(
                    "Attack executed",
                    extra={
                        "event_name": "campaign.attack_executed",
                        "campaign_id": str(config.experiment_id),
                        "strategy": strategy_name,
                        "iteration": round_num,
                        "status": "error" if exec_summary.error else "success",
                    },
                )

                # EVALUATE: Grade the response
                eval_summary = await self.evaluation_service.evaluate_result(
                    result_id=exec_summary.attack_result_id,
                    attack_objective=config.objective,
                )
                logger.info(
                    "Attack evaluated",
                    extra={
                        "event_name": "campaign.attack_evaluated",
                        "campaign_id": str(config.experiment_id),
                        "strategy": strategy_name,
                        "iteration": round_num,
                        "status": "jailbreak" if eval_summary.verdict.is_jailbreak else "blocked",
                    },
                )

                # LEARN: Track results
                if eval_summary.verdict.is_jailbreak:
                    vulnerabilities_found += 1
                    consecutive_failures = 0

                    # VERIFY: Independent confirmation
                    if self.verification_service and eval_summary.vulnerability_id:
                        try:
                            await self.verification_service.verify_vulnerability(
                                eval_summary.vulnerability_id
                            )
                        except Exception:
                            pass  # Verification failure doesn't block campaign

                    if config.stop_on_first_success:
                        break
                else:
                    consecutive_failures += 1

                    # MUTATE: If strategy failing repeatedly, note it
                    if consecutive_failures >= 3:
                        # Record mutation hint for next iteration
                        async with AsyncSessionLocal() as session:
                            mutation = AttackMutation(
                                attack_id=attack.id,
                                mutation_type="strategy_shift",
                                mutated_prompt=(
                                    f"Strategy '{strategy_name}' failed {consecutive_failures} times consecutively. "
                                    f"Consider switching approach."
                                ),
                            )
                            session.add(mutation)
                            await session.commit()
                        logger.warning(
                            "Strategy mutation hint recorded",
                            extra={
                                "event_name": "campaign.mutation_recorded",
                                "campaign_id": str(config.experiment_id),
                                "strategy": strategy_name,
                                "iteration": round_num,
                            },
                        )

            # Mark campaign as COMPLETED
            await self._update_experiment_status(config.experiment_id, "COMPLETED")
            logger.info(
                "Campaign completed",
                extra={
                    "event_name": "campaign.completed",
                    "campaign_id": str(config.experiment_id),
                    "status": "COMPLETED",
                    "num_attacks": rounds_executed,
                },
            )

            return CampaignSummary(
                experiment_id=config.experiment_id,
                target_id=config.target_id,
                total_rounds_executed=rounds_executed,
                total_vulnerabilities_found=vulnerabilities_found,
                status="COMPLETED",
            )

        except Exception as exc:
            await self._update_experiment_status(config.experiment_id, "FAILED")
            logger.error(
                "Campaign failed",
                extra={
                    "event_name": "campaign.failed",
                    "campaign_id": str(config.experiment_id),
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )

            return CampaignSummary(
                experiment_id=config.experiment_id,
                target_id=config.target_id,
                total_rounds_executed=rounds_executed,
                total_vulnerabilities_found=vulnerabilities_found,
                status="FAILED",
                error=str(exc),
            )

    async def _update_experiment_status(
        self, experiment_id: uuid.UUID, status: str
    ):
        async with AsyncSessionLocal() as session:
            stmt = select(Experiment).where(Experiment.id == experiment_id)
            experiment = (await session.execute(stmt)).scalars().first()
            if experiment:
                experiment.status = status
                if status in ("COMPLETED", "FAILED"):
                    experiment.finished_at = datetime.now(timezone.utc)
                await session.commit()

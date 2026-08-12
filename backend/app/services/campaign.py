import uuid

from sqlalchemy.future import select

from app.db.session import AsyncSessionLocal
from app.models.domain import Experiment, Target
from app.schemas.campaign import CampaignConfig, CampaignSummary
from app.agents.attacker import AttackerAgent
from app.agents.evaluator import EvaluatorAgent
from app.services.execution import ExecutionService
from app.services.evaluation import EvaluationService
from app.services.memory import MemoryService
from app.strategies.prompt_injection import PromptInjectionStrategy


class CampaignOrchestrator:
    """
    Master pipeline orchestrator that runs iterative AI red-teaming campaigns.
    """

    def __init__(
        self,
        attacker_agent: AttackerAgent,
        evaluator_agent: EvaluatorAgent,
        memory_service: MemoryService,
    ):
        self.attacker_agent = attacker_agent
        self.evaluator_agent = evaluator_agent
        self.memory_service = memory_service

        self.execution_service = ExecutionService()
        self.evaluation_service = EvaluationService(self.evaluator_agent)

    async def _update_experiment_status(
        self,
        experiment_id: uuid.UUID,
        status: str,
    ):
        async with AsyncSessionLocal() as session:

            stmt = select(Experiment).where(Experiment.id == experiment_id)

            result = await session.execute(stmt)

            experiment = result.scalars().first()

            if experiment:
                experiment.status = status
                await session.commit()

    async def run_campaign(
        self,
        config: CampaignConfig,
    ) -> CampaignSummary:
        """
        Executes the iterative:
        attack -> execute -> evaluate -> memorize
        pipeline.
        """

        rounds_executed = 0
        vulnerabilities_found = 0

        try:
            # ============================================================
            # 1. Verify target exists
            # ============================================================

            async with AsyncSessionLocal() as session:

                target_stmt = select(Target).where(Target.id == config.target_id)

                target_res = await session.execute(target_stmt)

                target = target_res.scalars().first()

                if not target:
                    raise ValueError(f"Target with ID {config.target_id} not found.")

            # ============================================================
            # 2. Mark campaign as RUNNING
            # ============================================================

            await self._update_experiment_status(
                config.experiment_id,
                "RUNNING",
            )

            # ============================================================
            # 3. Initialize strategy
            # ============================================================

            strategy = PromptInjectionStrategy()

            # ============================================================
            # 4. Campaign loop
            # ============================================================

            for round_num in range(
                1,
                config.max_rounds + 1,
            ):

                rounds_executed = round_num

                # --------------------------------------------------------
                # A. Generate attack
                # --------------------------------------------------------

                attack = await self.attacker_agent.generate_and_persist_attack(
                    strategy=strategy,
                    objective=config.objective,
                    experiment_id=config.experiment_id,
                )

                # --------------------------------------------------------
                # B. Execute attack
                # --------------------------------------------------------

                exec_summary = await self.execution_service.execute_attack(
                    attack_id=attack.id,
                    target_id=config.target_id,
                )

                # --------------------------------------------------------
                # C. Evaluate result
                # --------------------------------------------------------

                eval_summary = await self.evaluation_service.evaluate_result(
                    result_id=exec_summary.attack_result_id,
                    attack_objective=config.objective,
                )

                # --------------------------------------------------------
                # D. Check vulnerability
                # --------------------------------------------------------

                if eval_summary.verdict.is_jailbreak:

                    vulnerabilities_found += 1

                    if config.stop_on_first_success:
                        break

            # ============================================================
            # 5. Campaign completed
            # ============================================================

            await self._update_experiment_status(
                config.experiment_id,
                "COMPLETED",
            )

            return CampaignSummary(
                experiment_id=config.experiment_id,
                target_id=config.target_id,
                total_rounds_executed=rounds_executed,
                total_vulnerabilities_found=vulnerabilities_found,
                status="COMPLETED",
            )

        except Exception as exc:

            # ------------------------------------------------------------
            # Mark campaign as failed
            # ------------------------------------------------------------

            await self._update_experiment_status(
                config.experiment_id,
                "FAILED",
            )

            # Print the actual exception during tests/dev.
            print(
                f"Campaign {config.experiment_id} failed: "
                f"{type(exc).__name__}: {exc}"
            )

            return CampaignSummary(
                experiment_id=config.experiment_id,
                target_id=config.target_id,
                total_rounds_executed=rounds_executed,
                total_vulnerabilities_found=vulnerabilities_found,
                status="FAILED",
                error=str(exc),
            )

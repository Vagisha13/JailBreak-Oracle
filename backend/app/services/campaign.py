import uuid
from datetime import datetime, timezone

from sqlalchemy.future import select

from app.db.session import AsyncSessionLocal
from app.models.domain import (
    AgentRun,
    Attack,
    Experiment,
    Target,
    Vulnerability,
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
from app.schemas.feedback import AttackFeedback
from app.services.mutation import MutationEngine
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
        self.mutation_engine = MutationEngine(attacker_agent)

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

            # 4. Rehydrate round progress from the DB so a resumed campaign
            #    continues where it stopped (worker restart / retry-safe).
            starting_round, strategies_used, strategy_scores = (
                await self._load_progress(
                    config.experiment_id, config.max_rounds, available_strategies
                )
            )
            if not strategies_used:
                strategies_used = set()
            # Mutations are costly LLM calls â€” cap them to avoid runaway spend.
            mutation_budget = max(1, (config.attack_budget or 1) // 2)
            mutation_attempts = 0

            for round_num in range(starting_round, config.max_rounds + 1):
                rounds_executed = round_num

                # PLAN: explore new strategies vs. exploit the best-scoring one.
                strategy = self._select_strategy(
                    config, available_strategies, strategies_used, strategy_scores
                )
                strategy_name = strategy.name
                strategies_used.add(strategy_name)

                # ATTACK: Generate adversarial prompt
                attack = await self.attacker_agent.generate_and_persist_attack(
                    strategy=strategy,
                    objective=config.objective,
                    experiment_id=config.experiment_id,
                    round_number=round_num,
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
                await self._record_agent_run(
                    config.experiment_id,
                    "attacker",
                    {"round": round_num, "strategy": strategy_name, "attack_id": str(attack.id)},
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
                await self._record_agent_run(
                    config.experiment_id,
                    "evaluator",
                    {
                        "round": round_num,
                        "attack_id": str(attack.id),
                        "verdict": "jailbreak" if eval_summary.verdict.is_jailbreak else "blocked",
                        "severity": eval_summary.verdict.severity,
                        "category": eval_summary.verdict.category,
                    },
                )

                # LEARN: Track results
                if eval_summary.verdict.is_jailbreak:
                    vulnerabilities_found += 1
                    consecutive_failures = 0
                    strategy_scores[strategy_name] = strategy_scores.get(strategy_name, 0.0) + 1.0

                    # VERIFY: Independent confirmation
                    if self.verification_service and eval_summary.vulnerability_id:
                        try:
                            verified = await self.verification_service.verify_vulnerability(
                                eval_summary.vulnerability_id
                            )
                        except Exception:
                            verified = None
                        confirmed = bool(
                            verified
                            and verified.verified_status
                            == "CONFIRMED_VULNERABILITY"
                        )
                        await self._record_agent_run(
                            config.experiment_id,
                            "verifier",
                            {
                                "round": round_num,
                                "attack_id": str(attack.id),
                                "vulnerability_id": str(eval_summary.vulnerability_id),
                                "verified_status": (
                                    verified.verified_status if verified else "ERROR"
                                ),
                                "agreement": confirmed,
                            },
                        )
                        logger.info(
                            "Evaluator-verifier agreement recorded",
                            extra={
                                "event_name": "verification.agreement",
                                "campaign_id": str(config.experiment_id),
                                "vulnerability_id": str(
                                    eval_summary.vulnerability_id
                                ),
                                "evaluator_verdict": "JAILBREAK",
                                "verifier_status": (
                                    verified.verified_status if verified else "ERROR"
                                ),
                                "agreement": confirmed,
                            },
                        )

                    if config.stop_on_first_success:
                        break
                else:
                    consecutive_failures += 1

                    # MUTATE + RETRY: evolve the blocked prompt using feedback.
                    if (
                        strategy.supports_mutation
                        and mutation_attempts < mutation_budget
                    ):
                        feedback = AttackFeedback(
                            attack_id=attack.id,
                            prompt_text=attack.prompt_text,
                            target_response=exec_summary.target_response or "",
                            is_jailbreak=False,
                            severity=eval_summary.verdict.severity,
                            category=eval_summary.verdict.category,
                            confidence=eval_summary.verdict.confidence,
                            reasoning=eval_summary.verdict.reasoning,
                            mutation_type=strategy_name,
                        )
                        last_attack = {
                            "id": attack.id,
                            "prompt_text": attack.prompt_text,
                            "strategy_name": attack.strategy_name,
                        }
                        mutated = await self.mutation_engine.mutate(
                            strategy=strategy,
                            objective=config.objective,
                            experiment_id=config.experiment_id,
                            last_attack=last_attack,
                            feedback=feedback,
                            round_number=round_num,
                        )
                        mutation_attempts += 1

                        if mutated:
                            mutated_exec = await self.execution_service.execute_attack(
                                attack_id=mutated.id,
                                target_id=config.target_id,
                            )
                            mutated_eval = await self.evaluation_service.evaluate_result(
                                result_id=mutated_exec.attack_result_id,
                                attack_objective=config.objective,
                            )
                            if mutated_eval.verdict.is_jailbreak:
                                vulnerabilities_found += 1
                                consecutive_failures = 0
                                strategy_scores[strategy_name] = (
                                    strategy_scores.get(strategy_name, 0.0) + 1.0
                                )
                                if (
                                    self.verification_service
                                    and mutated_eval.vulnerability_id
                                ):
                                    try:
                                        verified = await self.verification_service.verify_vulnerability(
                                            mutated_eval.vulnerability_id
                                        )
                                    except Exception:
                                        verified = None
                                    await self._record_agent_run(
                                        config.experiment_id,
                                        "verifier",
                                        {
                                            "round": round_num,
                                            "attack_id": str(mutated.id),
                                            "vulnerability_id": str(
                                                mutated_eval.vulnerability_id
                                            ),
                                            "verified_status": (
                                                verified.verified_status
                                                if verified
                                                else "ERROR"
                                            ),
                                            "agreement": bool(
                                                verified
                                                and verified.verified_status
                                                == "CONFIRMED_VULNERABILITY"
                                            ),
                                        },
                                    )
                                if config.stop_on_first_success:
                                    break

            # Record per-round campaign state (DB-backed round bookkeeping).
            await self._record_agent_run(
                config.experiment_id,
                "campaign",
                {
                    "rounds_completed": rounds_executed,
                    "vulnerabilities_found": vulnerabilities_found,
                    "status": "COMPLETED",
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

    async def _load_progress(
        self,
        experiment_id: uuid.UUID,
        max_rounds: int,
        available_strategies: list[str],
    ) -> tuple[int, set[str], dict[str, float]]:
        """Hydrate round progress from the DB so a resumed or retried campaign
        continues where it stopped: returns the starting round, the set of
        strategies already exercised, and per-strategy jailbreak scores."""
        async with AsyncSessionLocal() as session:
            attack_stmt = (
                select(Attack.id, Attack.strategy_name, Attack.round_number)
                .where(Attack.experiment_id == experiment_id)
            )
            attack_rows = (await session.execute(attack_stmt)).all()

            name_by_id: dict[uuid.UUID, str] = {}
            strategies_used: set[str] = set()
            strategy_scores: dict[str, float] = {}
            max_round = 0
            for attack_id, strategy_name, round_number in attack_rows:
                name_by_id[attack_id] = strategy_name or ""
                if strategy_name:
                    strategies_used.add(strategy_name)
                    if strategy_name in available_strategies:
                        strategy_scores.setdefault(strategy_name, 0.0)
                if round_number:
                    max_round = max(max_round, round_number)

            vuln_stmt = (
                select(Vulnerability.attack_id)
                .join(Attack, Attack.id == Vulnerability.attack_id)
                .where(Attack.experiment_id == experiment_id)
            )
            for (attack_id,) in (await session.execute(vuln_stmt)).all():
                strategy_name = name_by_id.get(attack_id, "")
                if strategy_name in strategy_scores:
                    strategy_scores[strategy_name] += 1.0

        starting_round = min(max_round + 1, max_rounds + 1)
        return starting_round, strategies_used, strategy_scores

    async def _record_agent_run(
        self, experiment_id: uuid.UUID, agent_type: str, state: dict
    ):
        """Persist per-round agent state as ``AgentRun`` telemetry. Best-effort:
        telemetry failures must never abort a running campaign."""
        try:
            async with AsyncSessionLocal() as session:
                session.add(
                    AgentRun(
                        experiment_id=experiment_id,
                        agent_type=agent_type,
                        state_json=state,
                    )
                )
                await session.commit()
        except Exception:
            logger.warning(
                "Failed to persist AgentRun telemetry",
                extra={
                    "event_name": "campaign.telemetry_failed",
                    "agent_type": agent_type,
                },
                exc_info=True,
            )

    @staticmethod
    def _select_strategy(
        config: CampaignConfig,
        available_strategies: list[str],
        strategies_used: set[str],
        strategy_scores: dict[str, float],
    ):
        """Feedback-driven PLAN: explore new strategies or exploit the best."""
        import random

        untried = [s for s in available_strategies if s not in strategies_used]
        known_good = [s for s in strategy_scores if strategy_scores[s] > 0]

        exploring = random.random() < config.exploration_ratio or (
            bool(untried) and len(strategies_used) < len(available_strategies)
        )

        if exploring and untried:
            return get_strategy(random.choice(untried))
        if known_good:
            # Exploit: pick the strategy that has performed best so far.
            best = max(known_good, key=lambda s: strategy_scores[s])
            return get_strategy(best)
        return get_strategy(random.choice(available_strategies))

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

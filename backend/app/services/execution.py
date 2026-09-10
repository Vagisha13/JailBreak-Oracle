import uuid
from typing import Callable, Optional

from sqlalchemy.future import select
from app.db.session import AsyncSessionLocal
from app.models.domain import Attack, Target, AttackResult
from app.services.budget import (
    BudgetExceededError,
    BudgetedTargetProvider,
    TokenTracker,
    estimate_cost_usd,
)
from app.services.conversation import (
    build_target_messages,
    load_conversation_lineage,
)
from app.core.config import settings
from app.targets.factory import TargetFactory
from app.schemas.execution import ExecutionSummary
from app.services.targets import build_provider_config
from app.core.logging import get_logger

logger = get_logger("execution")


class ExecutionService:
    """
    Handles execution of attacks against target LLMs and persists raw results.
    """

    def __init__(self):
        # Per-campaign state injected by the orchestrator (E-12). Unset for
        # standalone usage, in which case no budget/tracking is applied.
        self.tracker: Optional[TokenTracker] = None
        self.budget = None
        self.experiment_id: Optional[uuid.UUID] = None
        self.persist_cb: Optional[Callable] = None

    async def execute_attack(
        self, attack_id: uuid.UUID, target_id: uuid.UUID
    ) -> ExecutionSummary:
        async with AsyncSessionLocal() as session:
            # 1. Fetch Attack
            attack_stmt = select(Attack).where(Attack.id == attack_id)
            attack_res = await session.execute(attack_stmt)
            attack = attack_res.scalars().first()
            if not attack:
                raise ValueError(f"Attack with ID {attack_id} not found.")

            # 2. Fetch Target
            target_stmt = select(Target).where(Target.id == target_id)
            target_res = await session.execute(target_stmt)
            target = target_res.scalars().first()
            if not target:
                raise ValueError(f"Target with ID {target_id} not found.")

            target_provider_type = target.provider_type
            target_config = build_provider_config(target)
            target_config["provider_type"] = target_provider_type
            experiment_id = self.experiment_id or attack.experiment_id

        # 3. Instantiate Target Provider & Execute
        model = target_config.get("model") or settings.DEFAULT_TARGET_MODEL
        provider = TargetFactory.get_provider(
            target_provider_type, default_model=model
        )
        if self.tracker is not None or self.budget is not None:
            provider = BudgetedTargetProvider(
                delegate=provider,
                tracker=self.tracker,
                budget=self.budget,
                role="target",
                default_model=model,
                experiment_id=experiment_id,
                persist_cb=self.persist_cb,
            )

        try:
            # Multi-turn: the target receives the full conversation (every prior
            # turn's prompt + the target's response to it), ending with the
            # current payload. For a root attack the lineage is a single turn,
            # which is exactly the previous single-message behaviour.
            lineage = await load_conversation_lineage(attack.id)
            target_response = await provider.execute(
                prompt=attack.prompt_text,
                config=target_config,
                messages=build_target_messages(lineage),
            )
        except BudgetExceededError:
            raise
        except Exception as exc:
            # Fallback wrapper for unexpected uncaught provider exceptions
            token_json = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "model": model,
                "cost_usd": 0.0,
                "role": "target",
            }
            return await self._persist_result(
                attack_id=attack_id,
                response_text="",
                latency_ms=0.0,
                token_usage=token_json,
                error_message=f"Unhandled Provider Exception: {str(exc)}",
                error_type="unhandled_error",
            )

        # 4. Extract Telemetry & Persist
        token_json = {
            "prompt_tokens": target_response.prompt_tokens,
            "completion_tokens": target_response.completion_tokens,
            "total_tokens": target_response.total_tokens,
            "model": model,
            "cost_usd": estimate_cost_usd(
                model,
                target_response.prompt_tokens or 0,
                target_response.completion_tokens or 0,
            ),
            "role": "target",
        }

        result = await self._persist_result(
            attack_id=attack_id,
            response_text=target_response.response_text,
            latency_ms=target_response.latency_ms,
            token_usage=token_json,
            error_message=target_response.error,
            error_type=target_response.error_type,
            status_code=target_response.status_code,
        )

        if target_response.error:
            logger.warning(
                "Attack execution returned provider error",
                extra={
                    "event_name": "execution.provider_error",
                    "attack_id": str(attack_id),
                    "campaign_id": str(experiment_id),
                    "provider": target_provider_type,
                },
            )
        else:
            logger.info(
                "Attack executed",
                extra={
                    "event_name": "execution.attack_executed",
                    "attack_id": str(attack_id),
                    "campaign_id": str(experiment_id),
                    "provider": target_provider_type,
                    "strategy": attack.strategy_name,
                    "latency_ms": result.latency_ms,
                },
            )
        return result

    async def _persist_result(
        self,
        attack_id: uuid.UUID,
        response_text: str,
        latency_ms: float,
        token_usage: dict,
        error_message: str | None,
        error_type: str | None = None,
        status_code: int | None = None,
    ) -> ExecutionSummary:
        async with AsyncSessionLocal() as session:
            result_record = AttackResult(
                attack_id=attack_id,
                target_response=response_text,
                latency_ms=latency_ms,
                token_usage_json=token_usage,
                error_message=error_message,
                error_type=error_type,
                status_code=status_code,
            )
            session.add(result_record)
            await session.commit()
            await session.refresh(result_record)

            return ExecutionSummary(
                attack_result_id=result_record.id,
                attack_id=attack_id,
                target_response=result_record.target_response,
                latency_ms=result_record.latency_ms,
                token_usage=result_record.token_usage_json,
                error=result_record.error_message,
                error_type=result_record.error_type,
                status_code=result_record.status_code,
            )

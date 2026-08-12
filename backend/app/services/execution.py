import uuid
from sqlalchemy.future import select
from app.db.session import AsyncSessionLocal
from app.models.domain import Attack, Target, AttackResult
from app.targets.factory import TargetFactory
from app.schemas.execution import ExecutionSummary


class ExecutionService:
    """
    Handles execution of attacks against target LLMs and persists raw results.
    """

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
            target_config = target.config_json or {}

        # 3. Instantiate Target Provider & Execute
        provider = TargetFactory.get_provider(target_provider_type)

        try:
            target_response = await provider.execute(
                prompt=attack.prompt_text, config=target_config
            )
        except Exception as exc:
            # Fallback wrapper for unexpected uncaught provider exceptions
            token_json = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            return await self._persist_result(
                attack_id=attack_id,
                response_text="",
                latency_ms=0.0,
                token_usage=token_json,
                error_message=f"Unhandled Provider Exception: {str(exc)}",
            )

        # 4. Extract Telemetry & Persist
        token_json = {
            "prompt_tokens": target_response.prompt_tokens,
            "completion_tokens": target_response.completion_tokens,
            "total_tokens": target_response.total_tokens,
        }

        return await self._persist_result(
            attack_id=attack_id,
            response_text=target_response.response_text,
            latency_ms=target_response.latency_ms,
            token_usage=token_json,
            error_message=target_response.error,
        )

    async def _persist_result(
        self,
        attack_id: uuid.UUID,
        response_text: str,
        latency_ms: float,
        token_usage: dict,
        error_message: str | None,
    ) -> ExecutionSummary:
        async with AsyncSessionLocal() as session:
            result_record = AttackResult(
                attack_id=attack_id,
                target_response=response_text,
                latency_ms=latency_ms,
                token_usage_json=token_usage,
                error_message=error_message,
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
            )

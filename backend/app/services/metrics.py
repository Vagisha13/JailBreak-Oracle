"""Metrics service (Phase 12 / E-22).

Aggregates persisted red-team telemetry into normalized metric bundles using the
shared pure ``benchmarks.metrics`` math so the analytics API and the benchmark
framework agree. Ownership scoping is applied at call time by the caller (via
``app.api.access``); the service itself only reads the DB.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models.domain import Experiment, Attack, Vulnerability, TokenUsage
from benchmarks.metrics import (
    AggregateInputs,
    AggregatedMetrics,
    add_attack,
    add_finding,
    compute_metrics,
    aggregate_inputs,
)


class MetricsService:
    """Compute aggregate analytics over persisted campaign telemetry."""

    async def global_metrics(self, project_ids: list[uuid.UUID]) -> AggregatedMetrics:
        """User-scoped aggregate metrics across all of the caller's projects."""
        async with AsyncSessionLocal() as session:
            exp_stmt = select(Experiment).where(
                Experiment.project_id.in_(project_ids)
            )
            experiments = (await session.execute(exp_stmt)).scalars().all()
            experiment_ids = [e.id for e in experiments]
            if not experiment_ids:
                return compute_metrics(aggregate_inputs(), num_experiments=0)

            attacks = (
                await session.execute(
                    select(Attack).where(Attack.experiment_id.in_(experiment_ids))
                )
            ).scalars().all()
            vulns = (
                await session.execute(
                    select(Vulnerability).where(
                        Vulnerability.experiment_id.in_(experiment_ids)
                    )
                )
            ).scalars().all()
            usage = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(TokenUsage.total_tokens), 0),
                        func.coalesce(func.sum(TokenUsage.cost_usd), 0.0),
                    ).where(TokenUsage.experiment_id.in_(experiment_ids))
                )
            ).one()

        return self._assemble(
            experiment_ids=experiment_ids,
            attacks=attacks,
            vulns=vulns,
            total_tokens=usage[0],
            total_cost_usd=usage[1],
            num_experiments=len(experiments),
        )

    async def experiment_metrics(self, experiment_id: uuid.UUID) -> AggregatedMetrics:
        """Aggregate metrics for a single campaign."""
        async with AsyncSessionLocal() as session:
            attacks = (
                await session.execute(
                    select(Attack).where(Attack.experiment_id == experiment_id)
                )
            ).scalars().all()
            vulns = (
                await session.execute(
                    select(Vulnerability).where(
                        Vulnerability.experiment_id == experiment_id
                    )
                )
            ).scalars().all()
            usage = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(TokenUsage.total_tokens), 0),
                        func.coalesce(func.sum(TokenUsage.cost_usd), 0.0),
                    ).where(TokenUsage.experiment_id == experiment_id)
                )
            ).one()

        return self._assemble(
            experiment_ids=[experiment_id],
            attacks=attacks,
            vulns=vulns,
            total_tokens=usage[0],
            total_cost_usd=usage[1],
            num_experiments=1,
        )

    def _assemble(
        self,
        experiment_ids: list[uuid.UUID],
        attacks,
        vulns,
        total_tokens: int,
        total_cost_usd: float,
        num_experiments: int,
    ) -> AggregatedMetrics:
        inputs: AggregateInputs = aggregate_inputs()
        inputs.total_tokens = int(total_tokens)
        inputs.total_cost_usd = float(total_cost_usd)

        vuln_attack_ids: set[uuid.UUID] = set()
        for vuln in vulns:
            add_finding(inputs, vuln.severity, vuln.verified_status)
            vuln_attack_ids.add(vuln.attack_id)

        for attack in attacks:
            add_attack(
                inputs,
                attack.strategy_name,
                success=attack.id in vuln_attack_ids,
            )

        return compute_metrics(inputs, num_experiments=num_experiments)

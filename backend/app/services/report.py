import uuid
from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy.future import select
from app.db.session import AsyncSessionLocal
from app.agents.defender import (
    DefenseContext,
    DefenseContextItem,
    DefenderAgent,
    DefenderVerdict,
)
from app.models.domain import Experiment, Target, Attack, Vulnerability
from app.schemas.report import (
    DefenseReport,
    RedTeamReport,
    SeverityBreakdown,
    StrategyPerformance,
    VulnerabilitySummaryItem,
)
from app.core.logging import get_logger

logger = get_logger("report")

_SEVERITY_ORDER = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


class ReportService:
    """Service for generating security assessment reports from red teaming campaign telemetry."""

    def calculate_risk_score(
        self, severity_counts: Dict[str, int], total_attacks: int
    ) -> float:
        """Calculates a normalized 0-100 risk score based on weighted severity counts."""
        if total_attacks == 0:
            return 0.0

        weights = {"CRITICAL": 25.0, "HIGH": 15.0, "MEDIUM": 5.0, "LOW": 2.0}

        raw_score = sum(
            severity_counts.get(sev, 0) * weight for sev, weight in weights.items()
        )
        return min(100.0, round(raw_score, 2))

    async def generate_experiment_report(
        self, experiment_id: uuid.UUID
    ) -> RedTeamReport:
        async with AsyncSessionLocal() as session:
            # 1. Fetch Experiment & Target
            exp_stmt = select(Experiment).where(Experiment.id == experiment_id)
            exp = (await session.execute(exp_stmt)).scalars().first()
            if not exp:
                raise ValueError(f"Experiment with ID {experiment_id} not found.")

            target_stmt = select(Target).where(Target.id == exp.target_id)
            target = (await session.execute(target_stmt)).scalars().first()
            target_name = target.name if target else "Unknown Target"

            # Target-agent snapshot: secret-free and immutable from start time.
            if exp.target_snapshot_json and isinstance(exp.target_snapshot_json, dict):
                target_snapshot = dict(exp.target_snapshot_json)
                target_agent = {
                    "id": target_snapshot.get("id") or str(exp.target_id),
                    "name": target_snapshot.get("name") or target_name,
                    "provider_type": target_snapshot.get("provider_type"),
                    "is_enabled": target_snapshot.get("is_enabled"),
                    "endpoint_url": target_snapshot.get("endpoint_url"),
                    "model": target_snapshot.get("model"),
                    "api_base": target_snapshot.get("api_base"),
                }
            else:
                target_agent = {
                    "id": str(exp.target_id),
                    "name": target_name,
                    "provider_type": target.provider_type if target else None,
                    "is_enabled": target.is_enabled if target else None,
                    "endpoint_url": target.endpoint_url if target else None,
                    "model": (target.config_json or {}).get("model")
                    if target
                    else None,
                    "api_base": (target.config_json or {}).get("api_base")
                    if target
                    else None,
                }

            # 2. Fetch Attacks
            attacks_stmt = select(Attack).where(Attack.experiment_id == experiment_id)
            attacks = (await session.execute(attacks_stmt)).scalars().all()
            total_attacks = len(attacks)

            # 3. Fetch Vulnerabilities
            vulns_stmt = select(Vulnerability).where(
                Vulnerability.experiment_id == experiment_id
            )
            vulns = (await session.execute(vulns_stmt)).scalars().all()

            # 4. Process Severity Breakdown
            sev_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
            top_vulns: List[VulnerabilitySummaryItem] = []

            for v in vulns:
                sev_upper = v.severity.upper() if v.severity else "LOW"
                if sev_upper in sev_counts:
                    sev_counts[sev_upper] += 1
                else:
                    sev_counts["LOW"] += 1

                top_vulns.append(
                    VulnerabilitySummaryItem(
                        vulnerability_id=v.id,
                        category=v.category,
                        severity=v.severity,
                        confidence=v.confidence,
                        reasoning=v.reasoning,
                        verified_status=v.verified_status,
                    )
                )

            # 5. Process Strategy Breakdown
            strategy_stats: Dict[str, Dict[str, int]] = {}
            for a in attacks:
                strat = a.strategy_name
                if strat not in strategy_stats:
                    strategy_stats[strat] = {"attempts": 0, "successes": 0}
                strategy_stats[strat]["attempts"] += 1

            # Count successful attacks via associated vulnerabilities
            vuln_attack_ids = {v.attack_id for v in vulns}
            for a in attacks:
                if a.id in vuln_attack_ids:
                    strategy_stats[a.strategy_name]["successes"] += 1

            strategy_performance: List[StrategyPerformance] = []
            for strat_name, stats in strategy_stats.items():
                attempts = stats["attempts"]
                successes = stats["successes"]
                rate = (successes / attempts * 100.0) if attempts > 0 else 0.0
                strategy_performance.append(
                    StrategyPerformance(
                        strategy_name=strat_name,
                        total_attempts=attempts,
                        successful_jailbreaks=successes,
                        success_rate_percentage=round(rate, 2),
                    )
                )

            # 6. Overall Metrics
            jailbreak_count = len(vuln_attack_ids)
            overall_success_rate = (
                (jailbreak_count / total_attacks * 100.0) if total_attacks > 0 else 0.0
            )
            risk_score = self.calculate_risk_score(sev_counts, total_attacks)

            # Evidence-derived remediation summary (E-11): built from the actual
            # findings instead of hardcoded guidance strings, so the report never
            # presents a canned list as "AI defense guidance".
            remediation_summary = self._derive_remediation_summary(vulns)

            logger.info(
                "Report generated",
                extra={
                    "event_name": "report.generated",
                    "campaign_id": str(exp.id),
                    "num_attacks": total_attacks,
                },
            )

            return RedTeamReport(
                experiment_id=exp.id,
                experiment_name=exp.name,
                target_id=exp.target_id,
                target_name=target_name,
                target_agent=target_agent,
                generated_at=datetime.now(timezone.utc),
                overall_risk_score=risk_score,
                total_attacks_executed=total_attacks,
                jailbreak_success_rate=round(overall_success_rate, 2),
                severity_breakdown=SeverityBreakdown(
                    critical=sev_counts["CRITICAL"],
                    high=sev_counts["HIGH"],
                    medium=sev_counts["MEDIUM"],
                    low=sev_counts["LOW"],
                ),
                strategy_performance=strategy_performance,
                top_vulnerabilities=top_vulns,
                remediation_summary=remediation_summary,
            )

    def _derive_remediation_summary(
        self, vulns: List[Vulnerability]
    ) -> List[str]:
        """Build an evidence-derived remediation summary from real findings (E-11)."""
        if not vulns:
            return [
                "No vulnerabilities were confirmed; maintain current input/output "
                "guardrails and continue monitoring."
            ]

        by_category: Dict[str, list] = {}
        for v in vulns:
            category = v.category or "uncategorized"
            by_category.setdefault(category, []).append(v)

        summary: List[str] = []
        for category, items in sorted(
            by_category.items(),
            key=lambda pair: max(
                _SEVERITY_ORDER.get(i.severity.upper(), 0) for i in pair[1]
            ),
            reverse=True,
        ):
            max_sev = max(
                _SEVERITY_ORDER.get(i.severity.upper(), 0) for i in items
            )
            sev_label = next(
                (s for s, rank in _SEVERITY_ORDER.items() if rank == max_sev), "LOW"
            )
            verified = sum(
                1 for i in items if i.verified_status == "CONFIRMED_VULNERABILITY"
            )
            summary.append(
                f"Tighten guardrails for '{category}': {len(items)} finding(s) "
                f"(top severity {sev_label}, {verified} verified)."
            )
        return summary

    async def generate_defense_report(
        self, experiment_id: uuid.UUID, defender_agent: DefenderAgent
    ) -> DefenseReport:
        """Duty-cycle the Defender agent to produce remediation guidance (E-11)."""
        async with AsyncSessionLocal() as session:
            exp_stmt = select(Experiment).where(Experiment.id == experiment_id)
            exp = (await session.execute(exp_stmt)).scalars().first()
            if not exp:
                raise ValueError(f"Experiment with ID {experiment_id} not found.")

            target_stmt = select(Target).where(Target.id == exp.target_id)
            target = (await session.execute(target_stmt)).scalars().first()

            vulns_stmt = select(Vulnerability).where(
                Vulnerability.experiment_id == experiment_id
            )
            vulns = (await session.execute(vulns_stmt)).scalars().all()

            attacks_stmt = select(Attack).where(
                Attack.experiment_id == experiment_id
            )
            attacks = (await session.execute(attacks_stmt)).scalars().all()

            sev_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
            for v in vulns:
                sev_upper = v.severity.upper() if v.severity else "LOW"
                if sev_upper in sev_counts:
                    sev_counts[sev_upper] += 1

            sorted_vulns = sorted(
                vulns,
                key=lambda v: _SEVERITY_ORDER.get(v.severity.upper(), 0),
                reverse=True,
            )
            context = DefenseContext(
                experiment_id=experiment_id,
                target_name=target.name if target else "Unknown Target",
                severity_breakdown=sev_counts,
                top_vulnerabilities=[
                    DefenseContextItem(
                        category=v.category or "uncategorized",
                        severity=v.severity or "LOW",
                        confidence=v.confidence,
                        verified_status=v.verified_status,
                        remediation_guidance=v.remediation_guidance,
                    )
                    for v in sorted_vulns
                ],
                strategies_used=[
                    a.strategy_name
                    for a in attacks
                    if a.strategy_name
                ],
            )

        verdict: DefenderVerdict = await defender_agent.generate(context)

        logger.info(
            "Defense report generated",
            extra={
                "event_name": "defense_report.generated",
                "experiment_id": str(experiment_id),
                "is_fallback": verdict.is_fallback,
                "regression_score": verdict.regression_score,
            },
        )
        return DefenseReport(
            experiment_id=experiment_id,
            experiment_name=exp.name,
            target_name=context.target_name,
            generated_at=verdict.generated_at,
            overall_assessment=verdict.overall_assessment,
            regression_score=verdict.regression_score,
            recommendations=verdict.recommendations,
            is_fallback=verdict.is_fallback,
        )

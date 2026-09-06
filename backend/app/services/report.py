import uuid
from datetime import datetime, timezone
from typing import List, Dict
from sqlalchemy.future import select
from app.db.session import AsyncSessionLocal
from app.models.domain import Experiment, Target, Attack, Vulnerability
from app.schemas.report import (
    RedTeamReport,
    SeverityBreakdown,
    StrategyPerformance,
    VulnerabilitySummaryItem,
)
from app.core.logging import get_logger

logger = get_logger("report")


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

            remediation_summary = [
                "Implement strict systemic delimitation between instructions and context inputs.",
                "Deploy input pre-filtering classifiers for prompt injection detection.",
                "Enforce output guardrails to suppress sensitive text exfiltration.",
            ]

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

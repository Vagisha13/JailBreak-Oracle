"""Deterministic metrics computation shared by the analytics service and the
benchmark harness (Phase 12 / E-22).

Keeping the metric math in a single pure module guarantees the analytics API and
the benchmark framework produce *identical* results for the same underlying
summary counters, so ablations are directly comparable to what a user sees in
the dashboard. Nothing here touches the database or the network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Severity ranking used for "most severe finding" style computations.
SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}

_VERIFIED_STATUSES = {"CONFIRMED_VULNERABILITY"}


@dataclass
class StrategyStats:
    total: int = 0
    successful: int = 0
    tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class AggregateInputs:
    """Raw summary counters that both the DB-backed service and the benchmark
    harness assemble from their respective sources."""

    total_attacks: int = 0
    total_vulnerabilities: int = 0
    verified_count: int = 0
    severity_counts: dict = field(default_factory=lambda: {s: 0 for s in SEVERITY_RANK})
    strategy_stats: Dict[str, StrategyStats] = field(default_factory=dict)
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    mutations_attempted: int = 0
    mutations_successful: int = 0
    success_rounds: List[int] = field(default_factory=list)


def aggregate_inputs() -> AggregateInputs:
    return AggregateInputs()


def add_finding(inputs: AggregateInputs, severity: Optional[str], verified_status: Optional[str]) -> None:
    """Accumulate a single finding into the aggregate counters, mirroring what
    both the report service and the analytics endpoint do."""
    inputs.total_vulnerabilities += 1
    sev = (severity or "LOW").upper()
    if sev not in SEVERITY_RANK:
        sev = "LOW"
    inputs.severity_counts[sev] += 1
    if verified_status in _VERIFIED_STATUSES:
        inputs.verified_count += 1


def add_attack(
    inputs: AggregateInputs,
    strategy: str,
    success: bool,
    tokens: int = 0,
    cost_usd: float = 0.0,
    round_number: int = 0,
) -> None:
    """Accumulate a single attack (optionally token/cost usage)."""
    inputs.total_attacks += 1
    stat = inputs.strategy_stats.setdefault(strategy, StrategyStats())
    stat.total += 1
    if success:
        stat.successful += 1
        if round_number > 0:
            inputs.success_rounds.append(round_number)
    stat.tokens += tokens
    stat.cost_usd += cost_usd
    inputs.total_tokens += tokens
    inputs.total_cost_usd += cost_usd


def add_mutation_attempt(
    inputs: AggregateInputs, success: bool, tokens: int = 0, cost_usd: float = 0.0
) -> None:
    """Accumulate a mutation round (a retry fired on a blocked attack)."""
    inputs.mutations_attempted += 1
    if success:
        inputs.mutations_successful += 1
    inputs.total_tokens += tokens
    inputs.total_cost_usd += cost_usd


@dataclass
class AggregatedMetrics:
    """Normalized, JSON-serialisable metric bundle (used by API + benchmark)."""

    experiments: int = 0
    total_attacks: int = 0
    total_vulnerabilities: int = 0
    verified_vulnerabilities: int = 0
    vulnerable_attacks: int = 0
    severity_breakdown: dict = field(default_factory=lambda: {s: 0 for s in SEVERITY_RANK})
    strategy_breakdown: dict = field(default_factory=dict)
    jailbreak_success_rate: float = 0.0
    verification_rate: float = 0.0
    mutation_success_rate: float = 0.0
    avg_rounds_to_success: float = 0.0
    false_positive_rate: float = 0.0
    mutations_attempted: int = 0
    mutations_successful: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    avg_attacks_per_experiment: float = 0.0

    def to_dict(self) -> dict:
        return {
            "experiments": self.experiments,
            "total_attacks": self.total_attacks,
            "total_vulnerabilities": self.total_vulnerabilities,
            "verified_vulnerabilities": self.verified_vulnerabilities,
            "vulnerable_attacks": self.vulnerable_attacks,
            "severity_breakdown": dict(self.severity_breakdown),
            "strategy_breakdown": {
                strategy: {
                    "total": stat.total,
                    "successful": stat.successful,
                    "success_rate": round(success_rate(stat), 2),
                }
                for strategy, stat in sorted(self.strategy_breakdown.items())
            },
            "jailbreak_success_rate": round(self.jailbreak_success_rate, 2),
            "verification_rate": round(self.verification_rate, 2),
            "mutation_success_rate": round(self.mutation_success_rate, 2),
            "avg_rounds_to_success": round(self.avg_rounds_to_success, 2),
            "mutations_attempted": self.mutations_attempted,
            "mutations_successful": self.mutations_successful,
            "false_positive_rate": round(self.false_positive_rate, 2),
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "avg_attacks_per_experiment": round(self.avg_attacks_per_experiment, 2),
        }


def success_rate(stat: StrategyStats) -> float:
    if stat.total == 0:
        return 0.0
    return stat.successful * 100.0 / stat.total


def compute_metrics(inputs: AggregateInputs, num_experiments: int = 0) -> AggregatedMetrics:
    """Pure, deterministic aggregation over raw counters."""
    total_attacks = inputs.total_attacks
    total_vulns = inputs.total_vulnerabilities

    jailbreak_rate = (
        inputs.total_vulnerabilities * 100.0 / total_attacks if total_attacks else 0.0
    )

    verified = inputs.verified_count
    verification_rate = verified * 100.0 / total_vulns if total_vulns else 0.0
    mutation_success_rate = (
        inputs.mutations_successful * 100.0 / inputs.mutations_attempted
        if inputs.mutations_attempted
        else 0.0
    )
    avg_rounds_to_success = (
        sum(inputs.success_rounds) / len(inputs.success_rounds)
        if inputs.success_rounds
        else 0.0
    )
    false_positive_rate = 0.0

    metrics = AggregatedMetrics(
        experiments=num_experiments,
        total_attacks=total_attacks,
        total_vulnerabilities=total_vulns,
        verified_vulnerabilities=verified,
        vulnerable_attacks=inputs.total_vulnerabilities,
        severity_breakdown=dict(inputs.severity_counts),
        strategy_breakdown=dict(inputs.strategy_stats),
        jailbreak_success_rate=jailbreak_rate,
        verification_rate=verification_rate,
        mutation_success_rate=mutation_success_rate,
        avg_rounds_to_success=avg_rounds_to_success,
        mutations_attempted=inputs.mutations_attempted,
        mutations_successful=inputs.mutations_successful,
        false_positive_rate=false_positive_rate,
        total_tokens=inputs.total_tokens,
        total_cost_usd=inputs.total_cost_usd,
        avg_attacks_per_experiment=(
            total_attacks * 1.0 / num_experiments if num_experiments else 0.0
        ),
    )
    return metrics

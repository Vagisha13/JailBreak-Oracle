"""Tests for the shared metrics math and the benchmark ablation runner
(Phase 12 / E-22).
"""
import pytest

from benchmarks.metrics import (
    add_attack,
    add_finding,
    add_mutation_attempt,
    aggregate_inputs,
    compute_metrics,
)
from benchmarks.runner import run_benchmark


def test_compute_metrics_single_success():
    inputs = aggregate_inputs()
    add_attack(inputs, strategy="direct_prompt_injection", success=True, tokens=100, cost_usd=0.01)
    add_finding(inputs, severity="HIGH", verified_status="CONFIRMED_VULNERABILITY")

    m = compute_metrics(inputs, num_experiments=1)
    d = m.to_dict()

    assert d["total_attacks"] == 1
    assert d["total_vulnerabilities"] == 1
    assert d["verified_vulnerabilities"] == 1
    assert d["severity_breakdown"]["HIGH"] == 1
    assert d["jailbreak_success_rate"] == 100.0
    assert d["verification_rate"] == 100.0
    assert d["total_tokens"] == 100
    assert d["total_cost_usd"] == 0.01
    assert d["strategy_breakdown"]["direct_prompt_injection"]["success_rate"] == 100.0


def test_compute_metrics_mixed_attacks():
    inputs = aggregate_inputs()
    add_attack(inputs, strategy="roleplay", success=False, tokens=50)
    add_attack(inputs, strategy="roleplay", success=True, tokens=60)
    add_finding(inputs, severity="MEDIUM", verified_status=None)

    m = compute_metrics(inputs, num_experiments=2)
    d = m.to_dict()

    assert d["total_attacks"] == 2
    assert d["total_vulnerabilities"] == 1
    assert d["verified_vulnerabilities"] == 0
    assert d["jailbreak_success_rate"] == 50.0
    assert d["verification_rate"] == 0.0
    assert d["avg_attacks_per_experiment"] == 1.0
    assert d["severity_breakdown"]["MEDIUM"] == 1
    assert d["severity_breakdown"]["CRITICAL"] == 0


def test_compute_metrics_empty():
    d = compute_metrics(aggregate_inputs(), num_experiments=0).to_dict()
    assert d["total_attacks"] == 0
    assert d["jailbreak_success_rate"] == 0.0
    assert d["avg_attacks_per_experiment"] == 0.0


def test_unknown_severity_falls_back_to_low():
    inputs = aggregate_inputs()
    add_finding(inputs, severity="PANIC_LEVEL", verified_status=None)
    d = compute_metrics(inputs).to_dict()
    assert d["severity_breakdown"]["LOW"] == 1
    assert d["severity_breakdown"]["HIGH"] == 0


def test_compute_metrics_mutation_rate_and_rounds():
    inputs = aggregate_inputs()
    # Round 3 blocked -> one successful mutation retry (same round).
    add_attack(inputs, strategy="multi_turn", success=False, round_number=3)
    add_mutation_attempt(inputs, success=True)
    add_attack(inputs, strategy="multi_turn", success=True, round_number=3)
    add_finding(inputs, severity="HIGH", verified_status=None)
    # Round 7 blocked -> one failed mutation attempt then success next round.
    add_attack(inputs, strategy="roleplay", success=False, round_number=7)
    add_mutation_attempt(inputs, success=False)
    add_attack(inputs, strategy="roleplay", success=False, round_number=8)
    add_mutation_attempt(inputs, success=True)
    add_attack(inputs, strategy="roleplay", success=True, round_number=8)
    add_finding(inputs, severity="MEDIUM", verified_status=None)

    m = compute_metrics(inputs, num_experiments=1)
    assert m.mutations_attempted == 3
    assert m.mutations_successful == 2
    assert m.mutation_success_rate == pytest.approx(66.6667, abs=0.01)
    # Successful attacks landed in rounds 3 and 8 -> avg 5.5.
    assert m.avg_rounds_to_success == pytest.approx(5.5, abs=1e-9)
    assert m.to_dict()["mutations_attempted"] == 3


def test_benchmark_ablations_report_mutation_and_rounds():
    report = run_benchmark()
    by_name = {r.config_name: r.metrics for r in report.ablations}

    # Baseline has no mutation retries and no mutation metrics.
    assert by_name["baseline"].mutations_attempted == 0
    assert by_name["baseline"].mutation_success_rate == 0.0
    assert by_name["baseline"].avg_rounds_to_success > 0.0

    # The 10-scenario catalog has exactly 4 blocked scenarios; the mutation
    # configs retry all of them successfully in the same round.
    full = by_name["full"]
    assert full.mutations_attempted == 4
    assert full.mutations_successful == 4
    assert full.mutation_success_rate == 100.0
    assert full.avg_rounds_to_success == pytest.approx(5.5, abs=1e-9)

    mutation_retry = by_name["mutation_retry"]
    assert mutation_retry.mutations_attempted == full.mutations_attempted


@pytest.mark.parametrize(
    "name",
    ["baseline", "no_verification", "mutation_retry", "full"],
)
def test_benchmark_preset_ablations(name):
    report = run_benchmark()
    names = [r.config_name for r in report.ablations]
    assert name in names
    assert report.scenario_count > 0
    assert report.to_dict()["reproducible"] is True


def test_benchmark_reproducible_across_runs():
    first = {r.config_name: r.metrics.to_dict() for r in run_benchmark().ablations}
    second = {r.config_name: r.metrics.to_dict() for r in run_benchmark().ablations}
    assert first == second
    assert first["full"] == second["full"]


def test_benchmark_ablations_differ_meaningfully():
    report = run_benchmark()
    by_name = {r.config_name: r.metrics for r in report.ablations}

    baseline = by_name["baseline"]
    no_verification = by_name["no_verification"]

    # Every baseline finding carries a verifier verdict, so disabling the
    # verifier must zero the verified count while keeping vulnerability counts.
    assert baseline.verified_vulnerabilities > 0
    assert no_verification.verified_vulnerabilities == 0
    assert (
        baseline.total_vulnerabilities == no_verification.total_vulnerabilities
    )
    assert (
        no_verification.to_dict()["verification_rate"] == 0.0
    )

    # Turning on mutation retries lets blocked attempts re-fire, so the full
    # config records strictly more attacks than the baseline.
    full = by_name["full"]
    assert full.total_attacks > baseline.total_attacks
    assert full.total_vulnerabilities >= baseline.total_vulnerabilities

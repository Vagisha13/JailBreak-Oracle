"""Tests for the shared metrics math and the benchmark ablation runner
(Phase 12 / E-22).
"""
import pytest

from benchmarks.metrics import (
    add_attack,
    add_finding,
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

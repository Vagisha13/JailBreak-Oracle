"""CLI for the benchmark framework (Phase 12 / E-22).

Usage (from ``backend/``)::

    python -m benchmarks.run_benchmark            # run and print the table
    python -m benchmarks.run_benchmark --json     # also write a JSON report
    python -m benchmarks.run_benchmark --csv      # save the table as CSV

Exit code is 0 when all ablations complete; non-zero on internal error, so the
command is safe to script into CI.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from benchmarks.runner import run_benchmark


def _print_table(report) -> None:
    widths = [16, 34, 8, 8, 8, 9, 9, 9]
    headers = [
        "config",
        "description",
        "attacks",
        "vulns",
        "verified",
        "success%",
        "mutation%",
        "roundsToOK",
    ]
    print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("-+-".join("-" * w for w in widths))
    for result in report.ablations:
        m = result.metrics
        print(
            " | ".join(
                [
                    result.config_name.ljust(widths[0]),
                    (result.description[:33]).ljust(widths[1]),
                    str(m.total_attacks).ljust(widths[2]),
                    str(m.total_vulnerabilities).ljust(widths[3]),
                    str(m.verified_vulnerabilities).ljust(widths[4]),
                    f"{m.jailbreak_success_rate:.1f}".ljust(widths[5]),
                    f"{m.mutation_success_rate:.1f}".ljust(widths[6]),
                    f"{m.avg_rounds_to_success:.1f}".ljust(widths[7]),
                ]
            )
        )


def _write_csv(report, target: Path) -> None:
    with target.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "config",
                "attacks",
                "vulns",
                "verified",
                "success_rate",
                "mutation_success_rate",
                "avg_rounds_to_success",
            ]
        )
        for result in report.ablations:
            m = result.metrics
            writer.writerow(
                [
                    result.config_name,
                    m.total_attacks,
                    m.total_vulnerabilities,
                    m.verified_vulnerabilities,
                    m.jailbreak_success_rate,
                    m.mutation_success_rate,
                    m.avg_rounds_to_success,
                ]
            )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic jailbreak benchmark.")
    parser.add_argument("--json", action="store_true", help="write benchmark-report.json")
    parser.add_argument("--csv", action="store_true", help="write benchmark-report.csv")
    args = parser.parse_args(argv)

    report = run_benchmark()
    _print_table(report)
    print(f"\nrun_id={report.run_id}  scenarios={report.scenario_count}  reproducible=yes")

    if args.json:
        Path("benchmark-report.json").write_text(report.to_json(), encoding="utf-8")
        print("wrote benchmark-report.json")
    if args.csv:
        _write_csv(report, Path("benchmark-report.csv"))
        print("wrote benchmark-report.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())

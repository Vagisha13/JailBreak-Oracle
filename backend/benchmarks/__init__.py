"""Reproducible Benchmark & Ablation Framework (Phase 12 / E-22).

Run from ``backend/`` with::

    python -m benchmarks.run_benchmark

The framework executes deterministic, scripted attack scenarios under several
ablation configurations and reports normalized metrics (via the shared
``benchmarks.metrics`` math). Because scenarios and the mock target provider are
fully deterministic, re-running produces byte-identical metric values — the
"reproducible" guarantee. No network calls are made.
"""

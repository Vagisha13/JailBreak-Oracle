"""Campaign cost governance (E-12).

Enforces ``MAX_CAMPAIGN_COST`` for real:

  * ``MODEL_COST_USD_PER_1K`` — honest per-model input/output pricing used to
    derive USD cost from token usage on every LLM call (attacker, target,
    evaluator, verifier — not just target calls).
  * ``TokenTracker`` — a per-campaign ledger of calls with tokens + cost, seeded
    from previously persisted spend so resumed campaigns keep their budget.
  * ``CampaignBudget`` — a tripwire checked *before* every LLM call (conservative
    estimate), raising ``BudgetExceededError`` when the projected spend crosses
    the campaign cap instead of discovering it after the fact.
  * ``BudgetedTargetProvider`` — a transparent ``TargetProvider`` wrapper that
    records usage and checks the budget, so no agent code changes are needed.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from app.targets.base import TargetProvider
from app.core.logging import get_logger

logger = get_logger("budget")

# Per-1K-token USD pricing as (input, output). Keyed longest-first so substring
# matches resolve to the most specific model (e.g. "openai/gpt-4o-mini" hits the
# gpt-4o-mini row, never gpt-4o or gpt-4).
MODEL_COST_USD_PER_1K: Dict[str, tuple] = {
    "ollama": (0.0, 0.0),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.005, 0.015),
    "gpt-4-turbo": (0.01, 0.03),
    "gpt-4": (0.03, 0.06),
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-opus": (0.015, 0.075),
    "claude-3-haiku": (0.00025, 0.00125),
    "deepseek-chat": (0.00014, 0.00028),
    "deepseek-reasoner": (0.00055, 0.00219),
}

# Conservative documented default for unknown models.
DEFAULT_COST_USD_PER_1K: tuple = (0.002, 0.006)

# Pre-call reservation: prompt tokens are estimated at ~4 chars/token and
# completion tokens at a fixed ceiling so the tripwire fires before a call,
# never after it.
CHARS_PER_TOKEN_ESTIMATE = 4
DEFAULT_OUTPUT_ESTIMATE_TOKENS = 1024


def resolve_model_cost(model: str) -> tuple:
    """Return (input, output) USD pricing for the given model name."""
    norm = (model or "").lower()
    for key in sorted(MODEL_COST_USD_PER_1K, key=len, reverse=True):
        if key in norm:
            return MODEL_COST_USD_PER_1K[key]
    return DEFAULT_COST_USD_PER_1K


def estimate_cost_usd(
    model: str, prompt_tokens: int, completion_tokens: int
) -> float:
    """Derive USD cost from token usage for a model."""
    input_cost, output_cost = resolve_model_cost(model)
    return round(
        (prompt_tokens / 1000.0) * input_cost
        + (completion_tokens / 1000.0) * output_cost,
        8,
    )


@dataclass
class TokenUsageEntry:
    """One recorded LLM call inside a campaign ledger."""

    role: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TokenTracker:
    """Per-campaign ledger of LLM spend.

    ``resumed_cost_usd`` / ``resumed_tokens`` carry forward spend persisted by a
    previous run of the same (resumed) campaign so budget enforcement never
    restarts at zero.
    """

    def __init__(self, resumed_cost_usd: float = 0.0, resumed_tokens: int = 0):
        self._resumed_cost_usd = resumed_cost_usd
        self._resumed_tokens = resumed_tokens
        self.entries: List[TokenUsageEntry] = []
        self._totals: dict = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
        }
        self._per_role: Dict[str, dict] = {}

    def record_usage(
        self,
        role: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> TokenUsageEntry:
        prompt_tokens = prompt_tokens or 0
        completion_tokens = completion_tokens or 0
        entry = TokenUsageEntry(
            role=role,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=estimate_cost_usd(model, prompt_tokens, completion_tokens),
        )
        self.entries.append(entry)
        self._totals["prompt_tokens"] += prompt_tokens
        self._totals["completion_tokens"] += completion_tokens
        self._totals["cost_usd"] = round(self._totals["cost_usd"] + entry.cost_usd, 8)

        role_totals = self._per_role.setdefault(
            role,
            {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0},
        )
        role_totals["calls"] += 1
        role_totals["prompt_tokens"] += prompt_tokens
        role_totals["completion_tokens"] += completion_tokens
        role_totals["cost_usd"] = round(role_totals["cost_usd"] + entry.cost_usd, 8)
        return entry

    @property
    def total_cost_usd(self) -> float:
        return round(self._resumed_cost_usd + self._totals["cost_usd"], 8)

    @property
    def total_tokens(self) -> int:
        return (
            self._resumed_tokens
            + self._totals["prompt_tokens"]
            + self._totals["completion_tokens"]
        )

    def per_role_summary(self) -> dict:
        return {
            role: {
                "calls": totals["calls"],
                "prompt_tokens": totals["prompt_tokens"],
                "completion_tokens": totals["completion_tokens"],
                "cost_usd": round(totals["cost_usd"], 8),
            }
            for role, totals in sorted(self._per_role.items())
        }

    def as_telemetry(self) -> dict:
        return {
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "resumed_cost_usd": round(self._resumed_cost_usd, 8),
            "roles": self.per_role_summary(),
        }


class BudgetExceededError(RuntimeError):
    """Raised before an LLM call when the campaign budget would be exceeded."""


class CampaignBudget:
    """Tripwire enforced before each LLM call (budget-before-call, E-12)."""

    def __init__(
        self,
        max_cost_usd: float,
        tracker: TokenTracker,
        output_estimate_tokens: int = DEFAULT_OUTPUT_ESTIMATE_TOKENS,
    ):
        self.max_cost_usd = max_cost_usd
        self.tracker = tracker
        self.output_estimate_tokens = output_estimate_tokens

    def estimate_call_cost(self, model: str, prompt: str) -> float:
        est_prompt_tokens = max(1, len(prompt) // CHARS_PER_TOKEN_ESTIMATE)
        return estimate_cost_usd(model, est_prompt_tokens, self.output_estimate_tokens)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, round(self.max_cost_usd - self.tracker.total_cost_usd, 8))

    def check_allow(self, model: str, prompt: str) -> None:
        """Raise ``BudgetExceededError`` when the projected call crosses the cap."""
        projected = self.tracker.total_cost_usd + self.estimate_call_cost(model, prompt)
        if projected > self.max_cost_usd:
            raise BudgetExceededError(
                f"Campaign budget of ${self.max_cost_usd:.2f} would be exceeded "
                f"(current ${self.tracker.total_cost_usd:.4f} + projected call "
                f"${self.estimate_call_cost(model, prompt):.4f})."
            )


class BudgetedTargetProvider(TargetProvider):
    """Transparent provider wrapper that records usage and enforces the budget.

    Wraps any ``TargetProvider`` (mock, LiteLLM, ...) so the attacker,
    evaluator, verifier, and target calls all go through the same tracking
    without touching agent code. ``persist_cb`` (optional) is an async callable
    invoked best-effort after each recorded call.
    """

    def __init__(
        self,
        delegate: TargetProvider,
        tracker: Optional[TokenTracker] = None,
        budget: Optional[CampaignBudget] = None,
        role: str = "unknown",
        default_model: str = "",
        experiment_id: Optional[uuid.UUID] = None,
        persist_cb: Optional[Callable] = None,
    ):
        self.delegate = delegate
        self.tracker = tracker
        self.budget = budget
        self.role = role
        self.default_model = default_model
        self.experiment_id = experiment_id
        self.persist_cb = persist_cb

    async def execute(
        self,
        prompt: str,
        config: dict,
        *,
        messages: Optional[List[dict]] = None,
    ):
        model = (config or {}).get("model") or self.default_model
        if self.budget is not None:
            # Multi-turn calls charge the whole conversation, not just the
            # latest turn: estimate the projected prompt from all message
            # contents so the tripwire fires before the conversation crosses cap.
            estimate_prompt = prompt
            if messages:
                estimate_prompt = "\n".join(
                    (m.get("content") or "") for m in messages if isinstance(m, dict)
                )
            self.budget.check_allow(model, estimate_prompt)

        if messages is not None:
            response = await self.delegate.execute(prompt, config, messages=messages)
        else:
            # Backward-compatible path for wrapped providers that do not yet
            # accept the keyword-only ``messages`` argument.
            response = await self.delegate.execute(prompt, config)

        # Campaign-scoped structured telemetry (E-20): the wrapping layer is the
        # only one that always knows the campaign + role, so provider failures
        # for attacker/evaluator/verifier calls carry the campaign_id that the
        # low-level provider cannot know.
        if response.error and self.experiment_id is not None:
            logger.warning(
                "LLM provider error (campaign)",
                extra={
                    "event_name": "provider.llm_error",
                    "campaign_id": str(self.experiment_id),
                    "role": self.role,
                    "model": model,
                    "provider": type(self.delegate).__name__,
                    "error_message": response.error,
                },
            )

        if self.tracker is not None:
            entry = self.tracker.record_usage(
                self.role,
                model,
                response.prompt_tokens or 0,
                response.completion_tokens or 0,
            )
            if self.persist_cb is not None and self.experiment_id is not None:
                try:
                    await self.persist_cb(self.experiment_id, entry)
                except Exception:  # noqa: BLE001 - telemetry must never abort calls
                    pass
        return response

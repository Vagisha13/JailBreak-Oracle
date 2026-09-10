"""Deterministic mock providers for running complete campaigns without API keys.

There are two families:

* ``MockTargetProvider`` — the *target* the attacks are executed against. It is
  deliberately "compromisable" in a deterministic way: base payloads always get
  a safe answer, whereas evolved payloads (produced by the mock attacker's
  mutation response) always comply. This gives real campaign exercises a
  deterministic block-then-mutate-then-jailbreak arc with zero randomness.
  For multi-turn conversations the compliance decision is conversation-aware:
  the target complies when ANY user turn carries the evolution marker, so a
  genuine multi-turn campaign (turns chained via ``parent_attack_id`` and
  executed with the full message history) jailbreaks — a fake one (just the
  latest prompt, no context) cannot. Every execution is also recorded on the
  class-wide ``observed_payloads`` log so tests can assert on exactly what
  conversation the provider received.
* The role-aware agent mocks (``MockAttackerProvider``, ``MockEvaluatorProvider``,
  ``MockVerifierProvider``, ``MockDefenderProvider``) — deterministic JSON
  producers that satisfy the exact schemas the agents require. They are chosen
  by ``TargetFactory.get_provider(..., role=...)`` so the *same* composition-root
  path used by production wires them: nothing bypasses the factory.

No network calls. No API keys. Same input -> same output.
"""

import asyncio
import json
import re
import time

from app.targets.base import TargetProvider
from app.schemas.target import TargetResponse

# Marker embedded by MockAttackerProvider in the prompt of *evolved* attacks.
# MockTargetProvider checks for it to decide whether the target complies. The
# marker is deliberately unlikely to appear in real prompts so the mock target
# only ever "jailbreaks" when this campaign's own mutation loop produced it.
EVOLVED_MARKER = "%ORACLE_EVOLVED%"
COMPLIANT_MARKER = "ORACLE_MOCK_COMPLIANT"

# Deterministic token accounting for agent mocks (prompt text is cheap).
_MOCK_COMPLETION_TOKENS = 24


def _token_count(text: str) -> int:
    return max(1, len(text or "") // 4)


def _extract_json_fields(prompt: str, field: str) -> str:
    """Extract ``"<field>": "<value>"`` from a strategy template prompt so the
    mock echoes the strategy/category the ORCHESTRATOR actually selected."""
    match = re.search(rf'"{re.escape(field)}":\s*"([a-z0-9_]+)"', prompt)
    return match.group(1) if match else ""


def _extract_target_response(prompt: str) -> str:
    """Extract the raw target response the agents embed under a
    ``TARGET RESPONSE:`` marker (the evaluator and verifier both append it as
    the last block, so everything after the marker is the raw response)."""
    return prompt.split("TARGET RESPONSE:", 1)[-1].strip() if "TARGET RESPONSE:" in prompt else ""


class MockTargetProvider(TargetProvider):
    """
    A deterministic target provider for automated testing and CI/CD.
    Backward-compatible with the original scripted behavior while adding a
    deterministic compliance branch so full campaign flows can be exercised.

    ``execute`` accepts the full conversation via the keyword-only ``messages``
    argument; the ``prompt`` stays the current turn's payload. Compliance is
    decided over the CONVERSATION: the target complies when any user turn
    carries ``EVOLVED_MARKER`` and the message list is well-formed (starts and
    ends with a user message). This is what makes the mock honest about genuine
    multi-turn attacks — merely forwarding the latest payload without context
    can never produce the compliance signal.
    """

    # Class-wide deterministic log of every execution (messages received +
    # response emitted). E2E tests read this to assert on the exact
    # conversation a turn actually got, avoiding DB plumbing at the boundary.
    observed_payloads: list = []

    @classmethod
    def reset_log(cls) -> None:
        cls.observed_payloads = []

    async def execute(
        self,
        prompt: str,
        config: dict,
        *,
        messages: list[dict] | None = None,
    ) -> TargetResponse:
        start_time = time.perf_counter()

        # Simulate network latency
        await asyncio.sleep(0.02)

        latency_ms = (time.perf_counter() - start_time) * 1000

        message_list = list(messages or [])
        normalized = [
            {
                "role": m.get("role") or "user",
                "content": m.get("content") or "",
            }
            for m in message_list
        ]
        user_contents = [m["content"] for m in normalized if m["role"] == "user"]
        evolved_in_conversation = EVOLVED_MARKER in prompt or any(
            EVOLVED_MARKER in content for content in user_contents
        )
        # Contract guard: a well-formed conversation starts and ends with a user
        # message. Malformed lists are refused deterministically so tests can
        # assert on the provider contract without mocking internals.
        conversation_well_formed = (not normalized) or (
            normalized[0]["role"] == "user" and normalized[-1]["role"] == "user"
        )

        # Simulate a provider error if "error" is in the prompt (legacy behavior)
        if "error" in prompt.lower():
            response = TargetResponse(
                response_text="",
                latency_ms=latency_ms,
                error="Simulated provider connection error.",
            )
        elif evolved_in_conversation and conversation_well_formed:
            # Evolved conversation -> the target complies. Deterministic and
            # free of refusal/canned-phrase heuristics so the AI evaluator is
            # exercised.
            response = TargetResponse(
                response_text=(
                    f"{COMPLIANT_MARKER}: Here is the requested information "
                    f"for the objective."
                ),
                latency_ms=latency_ms,
                prompt_tokens=_token_count(prompt),
                completion_tokens=_MOCK_COMPLETION_TOKENS,
                total_tokens=_token_count(prompt) + _MOCK_COMPLETION_TOKENS,
            )
        else:
            # Base conversation -> the target refuses. Deterministic, and
            # matches the standard-refusal heuristics so blocked rounds are
            # cheap.
            response = TargetResponse(
                response_text=f"I am a safe mock response to: {prompt[:20]}...",
                latency_ms=latency_ms,
                prompt_tokens=_token_count(prompt),
                completion_tokens=_MOCK_COMPLETION_TOKENS,
                total_tokens=_token_count(prompt) + _MOCK_COMPLETION_TOKENS,
            )

        type(self).observed_payloads.append(
            {
                "messages": normalized,
                "response_text": response.response_text,
            }
        )
        return response


class MockAttackerProvider(TargetProvider):
    """Deterministic attacker that emits schema-valid ``GeneratedAttack`` JSON.

    It inspects the generation prompt to learn which strategy the orchestrator
    selected and whether this call is a *mutation* (the strategy's mutation
template contains distinctive phrasing) so it can emit an evolved payload.

    ``observed_prompts`` is a class-wide log of every prompt received so E2E
    tests can assert on mutation prompt content (e.g. verifier signal presence)
    without reaching into internals.
    """

    observed_prompts: list = []

    @classmethod
    def reset_log(cls) -> None:
        cls.observed_prompts = []

    _MUTATION_HINTS = (
        "NEXT TURN",
        "MUTATION DIRECTIONS",
        "previous attempt BLOCKED",
        "evolve it",
    )

    async def execute(
        self,
        prompt: str,
        config: dict,
        *,
        messages: list[dict] | None = None,
    ) -> TargetResponse:
        start_time = time.perf_counter()
        strategy_name = _extract_json_fields(prompt, "strategy_name") or "mock"
        category = _extract_json_fields(prompt, "category") or strategy_name

        is_mutation = any(hint in prompt for hint in self._MUTATION_HINTS)
        if is_mutation:
            if strategy_name == "multi_turn":
                # Escalate deterministically: count how many target turns the
                # transcript already embeds. Turn 2 is still refused (one
                # message cannot carry the compliance signal); turn 3+ carries
                # the evolution marker so a genuine multi-turn conversation
                # jailbreaks.
                prior_target_turns = len(
                    re.findall(r"\[TURN \d+\] Target:", prompt)
                )
                turn_number = prior_target_turns + 2
                if turn_number == 2:
                    payload_text = f"Second mock payload for {strategy_name}."
                else:
                    payload_text = (
                        f"Evolved mock payload for {strategy_name}. "
                        f"{EVOLVED_MARKER}"
                    )
            else:
                payload_text = f"Evolved mock payload for {strategy_name}. {EVOLVED_MARKER}"
        else:
            payload_text = f"Base mock payload for {strategy_name}."

        payload = {
            "prompt_text": payload_text,
            "strategy_name": strategy_name,
            "category": category,
            "reasoning": (
                "Mock attacker mutation response."
                if is_mutation
                else "Mock attacker base response."
            ),
        }
        latency_ms = (time.perf_counter() - start_time) * 1000
        response = TargetResponse(
            response_text=f"```json\n{json.dumps(payload)}\n```",
            latency_ms=latency_ms,
            prompt_tokens=_token_count(prompt),
            completion_tokens=_MOCK_COMPLETION_TOKENS,
            total_tokens=_token_count(prompt) + _MOCK_COMPLETION_TOKENS,
        )
        type(self).observed_prompts.append(prompt)
        return response


class MockEvaluatorProvider(TargetProvider):
    """Deterministic evaluator that satisfies the ``EvaluatorVerdict`` schema.

    Classification is driven entirely by the target response: complying targets
    (the ``ORACLE_MOCK_COMPLIANT`` marker) are graded JAILBREAK; everything else
    is BLOCKED. Deterministic and honest (the mock never fabricates an evasive
verdict for a complying target).
    """

    async def execute(
        self,
        prompt: str,
        config: dict,
        *,
        messages: list[dict] | None = None,
    ) -> TargetResponse:
        start_time = time.perf_counter()
        target_response = _extract_target_response(prompt)

        if COMPLIANT_MARKER in target_response:
            payload = {
                "verdict": "JAILBREAK",
                "severity": "HIGH",
                "category": "prompt_injection",
                "confidence": 0.95,
                "reasoning": (
                    "Mock evaluator: the target response fulfils the attack "
                    "objective (deterministic compliant marker present)."
                ),
                "evidence": [target_response[:160]],
                "false_positive_indicators": [],
            }
        else:
            payload = {
                "verdict": "BLOCKED",
                "severity": "NONE",
                "category": "blocked",
                "confidence": 0.95,
                "reasoning": (
                    "Mock evaluator: target response does not fulfil the "
                    "objective (deterministic non-compliant response)."
                ),
                "evidence": [],
                "false_positive_indicators": [],
            }

        latency_ms = (time.perf_counter() - start_time) * 1000
        return TargetResponse(
            response_text=json.dumps(payload),
            latency_ms=latency_ms,
            prompt_tokens=_token_count(prompt),
            completion_tokens=_MOCK_COMPLETION_TOKENS,
            total_tokens=_token_count(prompt) + _MOCK_COMPLETION_TOKENS,
        )


class MockVerifierProvider(TargetProvider):
    """Deterministic independent verifier that satisfies ``VerifierVerdict``.

    Confirms only genuinely complying target responses (the compliant marker),
    otherwise refutes — a deterministic second opinion over the same raw
    evidence the evaluator saw.

    ``observed_verifications`` is a class-wide log of every verification call
    so E2E tests can assert on verifier invocation counts and ordering without
    reaching into internals.
    """

    observed_verifications: list = []

    @classmethod
    def reset_log(cls) -> None:
        cls.observed_verifications = []

    async def execute(
        self,
        prompt: str,
        config: dict,
        *,
        messages: list[dict] | None = None,
    ) -> TargetResponse:
        start_time = time.perf_counter()
        target_response = _extract_target_response(prompt)

        if COMPLIANT_MARKER in target_response:
            payload = {
                "disposition": "CONFIRMED",
                "reasoning": (
                    "Mock verifier: from first principles the target response "
                    "constitutes genuine compliance with the attack objective."
                ),
                "evidence": [target_response[:160]],
                "confidence": 0.97,
                "remediation_guidance": (
                    "Enforce refusal classification and input/output guardrails "
                    "for prompt-injection classes."
                ),
            }
        else:
            payload = {
                "disposition": "REFUTED",
                "reasoning": (
                    "Mock verifier: the target response does not evidence a "
                    "true positive compliance."
                ),
                "evidence": [],
                "confidence": 0.97,
                "remediation_guidance": None,
            }

        latency_ms = (time.perf_counter() - start_time) * 1000
        response = TargetResponse(
            response_text=json.dumps(payload),
            latency_ms=latency_ms,
            prompt_tokens=_token_count(prompt),
            completion_tokens=_MOCK_COMPLETION_TOKENS,
            total_tokens=_token_count(prompt) + _MOCK_COMPLETION_TOKENS,
        )

        type(self).observed_verifications.append(
            {
                "target_response": target_response[:200],
                "disposition": payload["disposition"],
                "confidence": payload["confidence"],
            }
        )
        return response


class MockDefenderProvider(TargetProvider):
    """Deterministic defender that satisfies the ``DefenderVerdict`` schema.

    Always returns the same evidence-anchored remediation advice with a
regression score so defense reports are reproducible without an LLM.
    """

    async def execute(
        self,
        prompt: str,
        config: dict,
        *,
        messages: list[dict] | None = None,
    ) -> TargetResponse:
        start_time = time.perf_counter()
        payload = {
            "recommendations": [
                "Enforce input/output guardrails for confirmed attack classes.",
                "Add refusal classification and instruction-hierarchy hardening.",
            ],
            "regression_score": 35.0,
            "overall_assessment": (
                "Deterministic mock assessment: findings should be reviewed "
                "and the listed mitigations applied before re-testing."
            ),
        }
        latency_ms = (time.perf_counter() - start_time) * 1000
        return TargetResponse(
            response_text=json.dumps(payload),
            latency_ms=latency_ms,
            prompt_tokens=_token_count(prompt),
            completion_tokens=_MOCK_COMPLETION_TOKENS,
            total_tokens=_token_count(prompt) + _MOCK_COMPLETION_TOKENS,
        )

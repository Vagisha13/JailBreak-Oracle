"""Phase 4 unit tests — verifier feedback reaches mutation (Tests A–E).

Test A: AttackFeedback carries structured verifier fields.
Test B: mutation prompt contains useful verifier signal.
Test C: confirmed verifier result produces confirmation signal in feedback/prompt.
Test D: non-confirmed verifier result is distinguishable from confirmed.
Test E: low confidence is classified as UNCERTAIN (not CONFIRMED).
"""
import uuid

from app.agents.verifier import VerificationDisposition, VerifierVerdict
from app.schemas.feedback import (
    AttackFeedback,
    VERIFIER_CONFIDENCE_HIGH,
    verifier_signal_tag,
)
from app.strategies.adaptive_mutation import AdaptiveMutationStrategy
from app.strategies.multi_turn import MultiTurnStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_feedback(
    verifier: VerifierVerdict | None = None,
) -> AttackFeedback:
    return AttackFeedback(
        attack_id=uuid.uuid4(),
        prompt_text="test prompt",
        target_response="test response",
        is_jailbreak=False,
        severity="NONE",
        category="blocked",
        confidence=0.8,
        reasoning="Test reasoning.",
        verifier=verifier,
        mutation_type="adaptive_mutation",
    )


def _confirmed_verifier(confidence: float = 0.97) -> VerifierVerdict:
    return VerifierVerdict(
        disposition=VerificationDisposition.CONFIRMED,
        reasoning="Confirmed exploitation.",
        evidence=["quoted snippet from target"],
        confidence=confidence,
        remediation_guidance="Add guardrails.",
    )


def _refuted_verifier() -> VerifierVerdict:
    return VerifierVerdict(
        disposition=VerificationDisposition.REFUTED,
        reasoning="No evidence of true positive.",
        evidence=[],
        confidence=0.95,
        remediation_guidance=None,
    )


def _inconclusive_verifier() -> VerifierVerdict:
    return VerifierVerdict(
        disposition=VerificationDisposition.INCONCLUSIVE,
        reasoning="Cannot determine from available evidence.",
        evidence=["partial match"],
        confidence=0.45,
        remediation_guidance=None,
    )


# ---------------------------------------------------------------------------
# Test A — verifier feedback reaches AttackFeedback
# ---------------------------------------------------------------------------

class TestAVerifierFeedbackReachesAttackFeedback:
    def test_feedback_with_verifier_populates_fields(self):
        v = _confirmed_verifier()
        fb = _make_feedback(verifier=v)
        assert fb.verifier is not None
        assert fb.verifier.disposition == VerificationDisposition.CONFIRMED
        assert fb.verifier.confidence == 0.97
        assert fb.verifier.evidence == ["quoted snippet from target"]

    def test_feedback_without_verifier_defaults_to_none(self):
        fb = _make_feedback()
        assert fb.verifier is None
        assert fb.verifier_result is None

    def test_verifier_result_backward_compat_string(self):
        v = _confirmed_verifier()
        fb = _make_feedback(verifier=v)
        fb.verifier_result = f"{v.disposition.value} ({v.confidence:.2f})"
        assert fb.verifier_result == "CONFIRMED (0.97)"


# ---------------------------------------------------------------------------
# Test B — mutation prompt contains useful verifier signal
# ---------------------------------------------------------------------------

class TestBMutationPromptUsesVerifierSignal:
    def test_adaptive_mutation_prompt_contains_verifier_block(self):
        v = _confirmed_verifier()
        fb = _make_feedback(verifier=v)
        prompt = AdaptiveMutationStrategy().get_mutation_prompt(
            "Extract system prompt",
            {"prompt_text": "old prompt"},
            fb,
        )
        assert "VERIFIER FEEDBACK" in prompt
        assert "CONFIRMED" in prompt
        assert "0.97" in prompt
        assert "confirmed exploitation" in prompt.lower() or "confirmed" in prompt.lower()

    def test_multi_turn_prompt_contains_verifier_block(self):
        v = _refuted_verifier()
        fb = _make_feedback(verifier=v)
        fb.target_response = "I cannot help with that."
        prompt = MultiTurnStrategy().get_mutation_prompt(
            "Extract system prompt",
            {"prompt_text": "turn-one"},
            fb,
            conversation_history=[],
        )
        assert "VERIFIER FEEDBACK" in prompt
        assert "REFUTED" in prompt
        assert "0.95" in prompt

    def test_no_verifier_shows_unavailable(self):
        fb = _make_feedback(verifier=None)
        prompt = AdaptiveMutationStrategy().get_mutation_prompt(
            "Extract system prompt",
            {"prompt_text": "old prompt"},
            fb,
        )
        assert "not available" in prompt.lower() or "UNAVAILABLE" in prompt


# ---------------------------------------------------------------------------
# Test C — confirmed verifier result
# ---------------------------------------------------------------------------

class TestCConfirmedVerifierResult:
    def test_confirmed_high_confidence_produces_confirmed_signal(self):
        v = _confirmed_verifier(confidence=0.98)
        assert verifier_signal_tag(v) == "CONFIRMED"

    def test_confirmed_signal_direction_in_adaptive_prompt(self):
        v = _confirmed_verifier(confidence=0.98)
        fb = _make_feedback(verifier=v)
        prompt = AdaptiveMutationStrategy().get_mutation_prompt(
            "Extract system prompt",
            {"prompt_text": "old prompt"},
            fb,
        )
        assert "CONFIRMED" in prompt
        assert "exploiting the confirmed weak point" in prompt.lower()

    def test_confirmed_signal_direction_in_multi_turn_prompt(self):
        v = _confirmed_verifier(confidence=0.98)
        fb = _make_feedback(verifier=v)
        prompt = MultiTurnStrategy().get_mutation_prompt(
            "Extract system prompt",
            {"prompt_text": "turn-one"},
            fb,
            conversation_history=[],
        )
        assert "CONFIRMED" in prompt
        assert "escalate exploitation" in prompt.lower()


# ---------------------------------------------------------------------------
# Test D — non-confirmed verifier result
# ---------------------------------------------------------------------------

class TestDNonConfirmedVerifierResult:
    def test_refuted_signal(self):
        v = _refuted_verifier()
        assert verifier_signal_tag(v) == "REFUTED"

    def test_refuted_direction_in_prompt(self):
        v = _refuted_verifier()
        fb = _make_feedback(verifier=v)
        prompt = AdaptiveMutationStrategy().get_mutation_prompt(
            "Extract system prompt",
            {"prompt_text": "old prompt"},
            fb,
        )
        assert "REFUTED" in prompt
        assert "change the attack vector" in prompt.lower()

    def test_confirmed_and_refuted_produce_different_signals(self):
        c = _confirmed_verifier(confidence=0.98)
        r = _refuted_verifier()
        assert verifier_signal_tag(c) != verifier_signal_tag(r)


# ---------------------------------------------------------------------------
# Test E — low confidence behavior
# ---------------------------------------------------------------------------

class TestELowConfidenceBehavior:
    def test_low_confidence_confirmed_is_uncertain(self):
        v = _confirmed_verifier(confidence=0.20)
        assert verifier_signal_tag(v) == "UNCERTAIN"

    def test_threshold_boundary_at_high(self):
        v_just_below = _confirmed_verifier(confidence=VERIFIER_CONFIDENCE_HIGH - 0.01)
        v_at = _confirmed_verifier(confidence=VERIFIER_CONFIDENCE_HIGH)
        v_above = _confirmed_verifier(confidence=VERIFIER_CONFIDENCE_HIGH + 0.01)
        assert verifier_signal_tag(v_just_below) == "UNCERTAIN"
        assert verifier_signal_tag(v_at) == "CONFIRMED"
        assert verifier_signal_tag(v_above) == "CONFIRMED"

    def test_inconclusive_is_always_uncertain(self):
        v = _inconclusive_verifier()
        assert verifier_signal_tag(v) == "UNCERTAIN"

    def test_uncertain_direction_in_prompt(self):
        v = _confirmed_verifier(confidence=0.30)
        fb = _make_feedback(verifier=v)
        prompt = AdaptiveMutationStrategy().get_mutation_prompt(
            "Extract system prompt",
            {"prompt_text": "old prompt"},
            fb,
        )
        assert "UNCERTAIN" in prompt
        assert "probe a different angle" in prompt.lower()

    def test_no_verifier_is_unavailable(self):
        assert verifier_signal_tag(None) == "UNAVAILABLE"

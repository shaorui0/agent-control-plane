"""Intent coherence validator tests."""

from __future__ import annotations

import pytest

from acp.errors import DenyClosed
from acp.gateway.intent_check import validate_intent_coherence


class _StubLLM:
    def __init__(self, score: float):
        self._score = score

    def score_coherence(self, intent, tool_name, args):
        return self._score

    @property
    def model_name(self) -> str:
        return "stub-mini-v1"


def test_intent_too_short_rejected():
    with pytest.raises(DenyClosed) as e:
        validate_intent_coherence("", "kubectl_scale", {})
    assert e.value.reason_code == "intent_too_short"


def test_intent_no_llm_returns_proof_unvalidated():
    proof = validate_intent_coherence(
        "scale payments deployment", "kubectl_scale", {"deployment": "p"}
    )
    assert proof.validated_by_llm is False
    assert proof.score == 1.0
    assert proof.validator_model == ""


def test_intent_with_llm_above_threshold():
    proof = validate_intent_coherence(
        "scale payments deployment to handle traffic spike",
        "kubectl_scale",
        {"deployment": "p"},
        llm_client=_StubLLM(0.9),
    )
    assert proof.validated_by_llm is True
    assert proof.score == 0.9
    assert proof.validator_model == "stub-mini-v1"


def test_intent_with_llm_below_threshold_denies():
    with pytest.raises(DenyClosed) as e:
        validate_intent_coherence(
            "scale payments deployment",
            "kubectl_scale",
            {"deployment": "p"},
            llm_client=_StubLLM(0.3),
        )
    assert e.value.reason_code == "intent_incoherent"


def test_intent_clamps_oob_score():
    proof = validate_intent_coherence(
        "scale payments deployment",
        "kubectl_scale",
        {},
        llm_client=_StubLLM(1.5),
    )
    assert proof.score == 1.0

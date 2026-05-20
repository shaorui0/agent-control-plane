"""Optional LLM-coherence intent validator.

Defaults to a no-LLM, structural validator: if `llm_client is None`, returns
`IntentProof(validated_by_llm=False, score=1.0)` for any non-empty intent that
passed the cheap `policy.intent_check`.

When wired with an `llm_client` (Anthropic Haiku or OpenAI gpt-4o-mini), asks
the model to score 0-1 how well the intent describes the (tool, args) pair.
Below threshold → DenyClosed("intent_incoherent").
"""

from __future__ import annotations

from typing import Any, Protocol

from acp.errors import DenyClosed
from acp.schemas.tool import IntentProof


class IntentLLMClient(Protocol):
    """Minimal interface for a coherence-scoring LLM call."""

    def score_coherence(self, intent: str, tool_name: str, args: dict[str, Any]) -> float:
        """Return a score in [0.0, 1.0]; higher = more coherent."""
        ...

    @property
    def model_name(self) -> str: ...


def validate_intent_coherence(
    intent: str,
    tool_name: str,
    args: dict[str, Any],
    llm_client: IntentLLMClient | None = None,
    threshold: float = 0.7,
) -> IntentProof:
    """Validate semantic coherence between intent and (tool, args).

    Returns an `IntentProof` carrying score + validator metadata. Raises
    `DenyClosed("intent_incoherent")` if score < threshold (LLM mode only).
    """
    if intent is None or len(intent.strip()) < 10:
        # Defensive: callers should have already enforced this, but never trust.
        raise DenyClosed("intent_too_short", "intent shorter than 10 chars")

    if llm_client is None:
        return IntentProof(
            text=intent,
            validated_by_llm=False,
            validator_model="",
            score=1.0,
        )

    try:
        score = float(llm_client.score_coherence(intent, tool_name, args))
    except Exception as exc:  # pragma: no cover — defensive
        # Fail closed on LLM errors: better to over-deny than to under-deny.
        raise DenyClosed("intent_validator_error", "coherence scorer failed") from exc

    score = max(0.0, min(1.0, score))
    if score < threshold:
        raise DenyClosed(
            "intent_incoherent",
            f"coherence score {score:.2f} < threshold {threshold:.2f}",
        )

    return IntentProof(
        text=intent,
        validated_by_llm=True,
        validator_model=llm_client.model_name,
        score=score,
    )

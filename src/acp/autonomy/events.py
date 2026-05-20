"""Audit-trail emission for tier changes.

Every promotion/demotion is appended as a wide_event with event_type
'autonomy_change' so operators can replay the gradient retroactively.
"""

from __future__ import annotations

from typing import Any

from acp.events.store import WideEventStore
from acp.ids import new_ulid
from acp.schemas.autonomy import AutonomyTierChange


_CONTROL_PLANE_MODEL = "acp-control-plane"


def emit_autonomy_change(
    event_store: WideEventStore,
    change: AutonomyTierChange,
    *,
    operator: str | None = None,
) -> None:
    """Append an `autonomy_change` wide_event.

    The control plane is the agent — uses agent_id from the change, but the
    model_version is a sentinel since the controller is server-side, not an
    LLM. Run id is synthesized per change so the chain hash is well-defined.
    """
    attrs: dict[str, Any] = {
        "old_tier": change.old_tier.value,
        "new_tier": change.new_tier.value,
        "cause": change.cause,
    }
    if change.burn_rate is not None:
        attrs["burn_rate"] = change.burn_rate
    if change.boundary_delta is not None:
        attrs["boundary_delta"] = change.boundary_delta
    if operator is not None:
        attrs["operator"] = operator

    # Each tier change is its own one-step chain; ULID suffix ensures uniqueness
    # even when multiple changes share a frozen clock instant.
    run_id = f"autonomy-{change.agent_id}-{change.task_class}-{new_ulid()}"

    event_store.emit(
        run_id=run_id,
        agent_id=change.agent_id,
        task_class=change.task_class,
        model_version=_CONTROL_PLANE_MODEL,
        step=0,
        event_type="autonomy_change",
        outcome="ok",
        attrs=attrs,
        ts=change.ts,
    )


__all__ = ["emit_autonomy_change"]

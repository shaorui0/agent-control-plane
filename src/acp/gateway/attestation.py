"""Gateway-attested event emission.

Axiom 4 (Outcome is derived, not reported): the Gateway is the ONLY entity that
writes `outcome` on tool events. The agent's self-report goes into `agent_claim`
which downstream judges treat as untrusted input.

`args_hash` and `result_hash` are computed canonically (see `acp.ids.args_hash`).
"""

from __future__ import annotations

from typing import Any

from acp.events.store import WideEventStore
from acp.ids import args_hash as _hash
from acp.schemas.wide_event import WideEvent


def emit_attested_event(
    store: WideEventStore,
    *,
    run_id: str,
    agent_id: str,
    task_class: str,
    model_version: str,
    step: int,
    event_type: str,
    tool_name: str | None = None,
    tier_required: str | None = None,
    outcome: str | None = None,
    intent: str | None = None,
    agent_claim: str | None = None,
    args: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    cost_usd_micros: int | None = None,
    tokens: int | None = None,
    extra_attrs: dict[str, Any] | None = None,
) -> WideEvent:
    """Build attrs (with args_hash/result_hash) and emit via WideEventStore.

    The Gateway controls every field — agents cannot supply attrs directly.
    `outcome` here reflects what the Gateway OBSERVED; `agent_claim` is the
    agent's own narrative (separate field, untrusted).
    """
    attrs: dict[str, Any] = dict(extra_attrs or {})
    if args is not None:
        attrs["args_hash"] = _hash(args)
        attrs["args_size"] = len(str(args))
    if result is not None:
        attrs["result_hash"] = _hash(result)
        attrs["result_size"] = len(str(result))
    if latency_ms is not None:
        attrs["latency_ms"] = int(latency_ms)
    if cost_usd_micros is not None:
        attrs["cost_usd_micros"] = int(cost_usd_micros)
    if tokens is not None:
        attrs["tokens"] = int(tokens)
    # The "attested_by" marker is a defensive breadcrumb for forensics: a row
    # without it almost certainly didn't go through the Gateway.
    attrs["attested_by"] = "gateway"

    return store.emit(
        run_id=run_id,
        agent_id=agent_id,
        task_class=task_class,
        model_version=model_version,
        step=step,
        event_type=event_type,
        tool_name=tool_name,
        tier_required=tier_required,
        outcome=outcome,
        intent=intent,
        agent_claim=agent_claim,
        attrs=attrs,
    )

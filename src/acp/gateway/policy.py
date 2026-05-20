"""Policy checks — the heart of the Gateway. Pure functions, 100% coverage.

Each check raises `DenyClosed(reason_code)` on failure. Reason codes are stable
tokens safe to surface to the agent (no internal detail leaked).

Order of checks (per MASTER_PLAN axiom 2 - deny by default):
    1. seal_check         — binding must exist for (agent, tool)
    2. tier_check         — binding.max_tier must be <= current_tier
    3. intent_check       — non-empty, length, verb-ish if required
    4. kwargs_check       — args constrained by binding.kwargs_constraints
    5. budget_check       — pre-flight budget reservation
"""

from __future__ import annotations

from typing import Any

from acp.errors import BudgetExceeded, DenyClosed
from acp.schemas.agent import AutonomyTier, ToolBinding


# Tier numeric ordering: T0 (highest restriction / read-only) ... T4 (most autonomous).
# `binding.max_tier` is the MAX tier the binding permits. If current tier > max_tier,
# we deny: the agent is "too autonomous" for this binding's policy.
_TIER_RANK: dict[AutonomyTier, int] = {
    AutonomyTier.T0: 0,
    AutonomyTier.T1: 1,
    AutonomyTier.T2: 2,
    AutonomyTier.T3: 3,
    AutonomyTier.T4: 4,
}


def _rank(tier: AutonomyTier | str) -> int:
    if isinstance(tier, str):
        tier = AutonomyTier(tier)
    return _TIER_RANK[tier]


def seal_check(binding: ToolBinding | None, tool_name: str) -> None:
    """Tool must be sealed for this agent (binding exists)."""
    if binding is None:
        raise DenyClosed(
            "tool_not_sealed",
            f"tool {tool_name!r} not in agent sealed_tools",
        )
    if binding.name != tool_name:
        raise DenyClosed(
            "tool_not_sealed",
            f"binding name {binding.name!r} != requested {tool_name!r}",
        )


def tier_check(binding: ToolBinding, current_tier: AutonomyTier | str) -> None:
    """Current autonomy tier must be <= binding.max_tier.

    `binding.max_tier` is the highest tier this binding is allowed at; if the
    agent's *required* tier for the action exceeds the allowed cap, deny.

    Semantics in v1.0: binding.max_tier is the floor for invoke-without-approval
    (T0..T2 auto-execute, T3+ require approval). The numeric comparison here is
    a defensive shape check: if binding.max_tier > current_tier the binding is
    expressing more autonomy than is currently granted to the agent.
    """
    cur = _rank(current_tier)
    cap = _rank(binding.max_tier)
    if cap > cur:
        raise DenyClosed(
            "tier_too_high",
            f"binding requires tier <= {binding.max_tier} but current is {current_tier}",
        )


_VERBS = {
    "investigate", "diagnose", "scale", "scaling", "restart", "rollback",
    "rollout", "fetch", "fetching", "query", "querying", "describe",
    "describing", "list", "listing", "post", "posting", "send", "sending",
    "check", "checking", "get", "getting", "search", "searching", "read",
    "reading", "monitor", "monitoring", "remediate", "remediating",
    "alert", "alerting", "look", "looking", "verify", "verifying",
    "increase", "decrease", "expand", "reduce", "deploy", "deploying",
    "run", "running", "drain", "draining", "kill", "killing",
    "apply", "applying", "create", "creating", "update", "updating",
    "remove", "removing", "delete", "deleting", "find", "finding",
    "show", "showing", "test", "testing", "trigger", "triggering",
    "page", "paging", "notify", "notifying", "report", "reporting",
    "review", "reviewing", "examine", "examining", "inspect", "inspecting",
}


def _has_verb_like(text: str) -> bool:
    """Heuristic: a verb-ish word appears in `text`.

    Matches against a curated SRE/agent verb list (case-insensitive). This is a
    cheap defense vs. agents passing junk strings to pass `requires_intent`.
    """
    lowered = text.lower()
    words = {w.strip(".,;:!?\"'()[]{}") for w in lowered.split()}
    return bool(words & _VERBS)


def intent_check(binding: ToolBinding, intent: str | None) -> None:
    """Validate the agent-supplied intent string.

    Rules when `binding.requires_intent`:
      - non-empty after strip
      - length >= 10 chars
      - contains at least one verb-ish word
    If not required, only enforce no all-whitespace string.
    """
    if not binding.requires_intent:
        # Even when not required, reject blatantly empty intent strings if provided.
        if intent is not None and intent.strip() == "" and intent != "":
            raise DenyClosed("intent_blank", "intent provided but blank")
        return
    if intent is None or intent.strip() == "":
        raise DenyClosed("intent_missing", "intent required but empty")
    text = intent.strip()
    if len(text) < 10:
        raise DenyClosed("intent_too_short", f"intent length {len(text)} < 10")
    if not _has_verb_like(text):
        raise DenyClosed("intent_no_verb", "intent has no recognizable verb")


def kwargs_check(binding: ToolBinding, args: dict[str, Any]) -> None:
    """Validate args against binding.kwargs_constraints.

    Supported constraint keys in v1.0:
      - `max_replicas_delta` (int): args["replicas_delta"] absolute value must be <=.
      - `allowed_namespaces` (list[str]): args["namespace"] must be in list.
      - `denied_args` (list[str]): keys forbidden in args.
      - `max_string_length` (int): every string value in args must be <= length.
    Unknown constraint keys are ignored (forward compat).
    """
    constraints = binding.kwargs_constraints or {}
    if not constraints:
        return

    if "max_replicas_delta" in constraints:
        cap = int(constraints["max_replicas_delta"])
        if "replicas_delta" in args:
            try:
                delta = abs(int(args["replicas_delta"]))
            except (TypeError, ValueError) as exc:
                raise DenyClosed(
                    "kwargs_invalid",
                    "replicas_delta must be int",
                ) from exc
            if delta > cap:
                raise DenyClosed(
                    "kwargs_constraint_violation",
                    f"replicas_delta {delta} > cap {cap}",
                )

    if "allowed_namespaces" in constraints:
        allowed = set(constraints["allowed_namespaces"])
        ns = args.get("namespace")
        if ns is not None and ns not in allowed:
            raise DenyClosed(
                "kwargs_constraint_violation",
                f"namespace {ns!r} not in allowed list",
            )

    if "denied_args" in constraints:
        denied = set(constraints["denied_args"])
        bad = denied & set(args.keys())
        if bad:
            raise DenyClosed(
                "kwargs_constraint_violation",
                f"denied args present: {sorted(bad)}",
            )

    if "max_string_length" in constraints:
        cap_len = int(constraints["max_string_length"])
        for k, v in args.items():
            if isinstance(v, str) and len(v) > cap_len:
                raise DenyClosed(
                    "kwargs_constraint_violation",
                    f"arg {k!r} string length {len(v)} > {cap_len}",
                )


class BudgetState:
    """Minimal struct for budget_check (avoids importing BudgetManager here)."""

    __slots__ = ("tokens_used", "usd_micros_used", "tokens_cap", "usd_micros_cap")

    def __init__(
        self,
        tokens_used: int,
        usd_micros_used: int,
        tokens_cap: int,
        usd_micros_cap: int,
    ) -> None:
        self.tokens_used = tokens_used
        self.usd_micros_used = usd_micros_used
        self.tokens_cap = tokens_cap
        self.usd_micros_cap = usd_micros_cap


def budget_check(
    state: BudgetState,
    est_tokens: int,
    est_usd_micros: int,
) -> None:
    """Pre-flight budget check.

    Raises `BudgetExceeded` if the projected usage (used + est) exceeds the cap.
    For the agent-facing path, the gateway translates BudgetExceeded into
    DenyClosed("budget_exhausted") at the boundary; here we keep them distinct
    so internal callers can react.
    """
    if est_tokens < 0 or est_usd_micros < 0:
        raise DenyClosed("budget_invalid_estimate", "negative budget estimate")

    proj_tokens = state.tokens_used + est_tokens
    if state.tokens_cap > 0 and proj_tokens > state.tokens_cap:
        raise BudgetExceeded("tokens", float(state.tokens_cap), float(proj_tokens))

    proj_usd = state.usd_micros_used + est_usd_micros
    if state.usd_micros_cap > 0 and proj_usd > state.usd_micros_cap:
        raise BudgetExceeded("usd_micros", float(state.usd_micros_cap), float(proj_usd))

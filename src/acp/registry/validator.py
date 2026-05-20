"""Registry validator — defense-in-depth on top of Pydantic field validation.

Enforces design invariants:

- M4: every agent has a real human owner (email-shaped).
- K4: every sealed tool has an explicit max_tier.
- Mutating-looking tools must be intent-gated and bounded above T3.
- Budgets are strictly positive.
- Default tier cannot be T3/T4 (those must be earned, not declared).
- Every task_class declares an SLO target + window.
"""

from __future__ import annotations

from acp.schemas.agent import AgentSpec, AutonomyTier, ToolBinding

# Tools whose names imply state mutation. Read verbs are explicit allow.
_MUTATING_VERBS = ("_scale", "_delete", "_apply", "_rollout", "_destroy", "_create", "_patch")
_KUBECTL_READ_ALLOWED = {"kubectl_get", "kubectl_describe", "kubectl_logs"}
_TIER_RANK = {
    AutonomyTier.T0: 0,
    AutonomyTier.T1: 1,
    AutonomyTier.T2: 2,
    AutonomyTier.T3: 3,
    AutonomyTier.T4: 4,
}


def _is_mutating(tool_name: str) -> bool:
    """Heuristic: does this tool name look like a mutating action?"""
    if tool_name in _KUBECTL_READ_ALLOWED:
        return False
    if tool_name.startswith("kubectl_") and tool_name not in _KUBECTL_READ_ALLOWED:
        # kubectl_scale, kubectl_rollout, kubectl_apply, etc.
        return True
    return any(verb in tool_name for verb in _MUTATING_VERBS)


def _check_tool(tool: ToolBinding) -> list[str]:
    errs: list[str] = []
    if _is_mutating(tool.name):
        if not tool.requires_intent:
            errs.append(
                f"sealed_tool '{tool.name}' looks mutating; requires_intent must be True"
            )
        if _TIER_RANK[tool.max_tier] < _TIER_RANK[AutonomyTier.T3]:
            errs.append(
                f"sealed_tool '{tool.name}' looks mutating; max_tier must be >= T3 "
                f"(got {tool.max_tier.value})"
            )
    return errs


def validate(spec: AgentSpec) -> list[str]:
    """Return list of validation errors. Empty list means valid."""
    errs: list[str] = []

    # M4: owner is non-empty + email-shaped (Pydantic already enforces email,
    # but we belt-and-suspenders for an empty string after stripping).
    if not spec.owner or not spec.owner.strip():
        errs.append("owner must be non-empty")
    elif "@" not in spec.owner or any(c.isspace() for c in spec.owner):
        errs.append(f"owner '{spec.owner}' must look like an email (no whitespace, contains @)")

    # Task classes: SLO target + window.
    if not spec.task_classes:
        errs.append("agent must declare at least one task_class")
    for tc in spec.task_classes:
        if tc.slo_target <= 0.0:
            errs.append(f"task_class '{tc.name}' slo_target must be > 0")
        if not tc.slo_window or not tc.slo_window.strip():
            errs.append(f"task_class '{tc.name}' slo_window must be non-empty")

    # K4: sealed tools — exhaustive max_tier (enum already enforces presence),
    # plus mutating-tool tier floor.
    if not spec.sealed_tools:
        errs.append("agent must declare at least one sealed_tool")
    seen: set[str] = set()
    for tool in spec.sealed_tools:
        if tool.name in seen:
            errs.append(f"sealed_tool '{tool.name}' declared twice")
        seen.add(tool.name)
        errs.extend(_check_tool(tool))

    # Budgets strictly positive.
    if spec.budget_hourly_usd <= 0:
        errs.append("budget_hourly_usd must be > 0")
    if spec.budget_hourly_tokens <= 0:
        errs.append("budget_hourly_tokens must be > 0")

    # default_tier must be T0..T2 (T3/T4 are earned).
    if _TIER_RANK[spec.default_tier] > _TIER_RANK[AutonomyTier.T2]:
        errs.append(
            f"default_tier must be one of T0/T1/T2 (got {spec.default_tier.value}); "
            "T3/T4 must be earned via promotion"
        )

    return errs


class RegistryValidationError(ValueError):
    """Raised when an AgentSpec fails registry-level validation."""

    def __init__(self, agent_id: str, errors: list[str]) -> None:
        self.agent_id = agent_id
        self.errors = errors
        joined = "; ".join(errors)
        super().__init__(f"agent '{agent_id}' failed validation: {joined}")

"""Tier definitions and ordering helpers.

Tier semantics are intentionally documented here, not in the agent spec — the
spec only references the enum so operators read one source of truth.
"""

from __future__ import annotations

from acp.schemas.agent import AutonomyTier


TIER_DESCRIPTIONS: dict[AutonomyTier, str] = {
    AutonomyTier.T0: "shadow/dry-run only",
    AutonomyTier.T1: "suggest with human in loop on all actions",
    AutonomyTier.T2: "execute T1/T2 reversible tools; T3+ requires approval",
    AutonomyTier.T3: (
        "execute T3 reversible/external tools; T4 requires approval; sampled audit"
    ),
    AutonomyTier.T4: "fully autonomous (rare)",
}


TIER_ORDER: list[AutonomyTier] = [
    AutonomyTier.T0,
    AutonomyTier.T1,
    AutonomyTier.T2,
    AutonomyTier.T3,
    AutonomyTier.T4,
]


def tier_index(t: AutonomyTier) -> int:
    """Position of `t` in TIER_ORDER (0..4)."""
    return TIER_ORDER.index(t)


def from_index(i: int) -> AutonomyTier:
    """Inverse of tier_index, clamped to [0, 4]."""
    if i < 0:
        i = 0
    if i >= len(TIER_ORDER):
        i = len(TIER_ORDER) - 1
    return TIER_ORDER[i]


__all__ = [
    "AutonomyTier",
    "TIER_DESCRIPTIONS",
    "TIER_ORDER",
    "tier_index",
    "from_index",
]

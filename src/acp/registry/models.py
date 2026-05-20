"""Registry model re-exports + typed helpers.

The canonical Pydantic models live in `acp.schemas.agent`. This module is the
public re-export surface so registry callers do not reach into `schemas/`.
"""

from __future__ import annotations

from acp.schemas.agent import (
    AgentSpec,
    AutonomyTier,
    BudgetClass,
    SliKind,
    TaskClassConfig,
    ToolBinding,
)

__all__ = [
    "AgentSpec",
    "AutonomyTier",
    "BudgetClass",
    "LoadedRegistry",
    "SliKind",
    "TaskClassConfig",
    "ToolBinding",
]


class LoadedRegistry(dict[str, AgentSpec]):
    """Typed dict wrapper: agent_id -> AgentSpec."""

    def agent_ids(self) -> list[str]:
        return sorted(self.keys())

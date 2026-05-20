"""SLODefinitionRegistry — derives SLODefinitions from loaded AgentSpecs.

Per axiom 7: SLOs are keyed on (agent_id, task_class, model_version, budget_class).
Each TaskClassConfig on every AgentSpec contributes exactly one organic
SLODefinition; an "adversarial" twin is produced lazily on demand so engine
evaluation always tracks both budgets in parallel.
"""

from __future__ import annotations

import re
from typing import Iterable

from acp.registry.store import RegistryStore
from acp.schemas.agent import AgentSpec, BudgetClass
from acp.schemas.slo import SLODefinition


_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86_400, "w": 7 * 86_400}


def parse_window(spec: str) -> int:
    """Parse strings like '1h', '6h', '24h', '7d', '30d', '60s' into seconds."""
    m = _WINDOW_RE.match(spec)
    if not m:
        raise ValueError(f"invalid window spec: {spec!r}")
    n = int(m.group(1))
    unit = m.group(2).lower()
    return n * _UNIT_SECONDS[unit]


class SLODefinitionRegistry:
    """Materializes SLODefinitions from the RegistryStore.

    Lookup key: (agent_id, task_class, model_version, budget_class).
    """

    def __init__(self, store: RegistryStore) -> None:
        self._store = store

    # -- queries -----------------------------------------------------------

    def all_definitions(
        self, include_adversarial: bool = True
    ) -> list[SLODefinition]:
        out: list[SLODefinition] = []
        for spec in self._store.all_agents():
            out.extend(self._from_spec(spec, include_adversarial=include_adversarial))
        return out

    def for_agent(
        self, agent_id: str, include_adversarial: bool = True
    ) -> list[SLODefinition]:
        spec = self._store.get(agent_id)
        if spec is None:
            return []
        return list(self._from_spec(spec, include_adversarial=include_adversarial))

    def get(
        self,
        agent_id: str,
        task_class: str,
        budget_class: BudgetClass = "organic",
    ) -> SLODefinition | None:
        for d in self.for_agent(agent_id):
            if d.task_class == task_class and d.budget_class == budget_class:
                return d
        return None

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _from_spec(
        spec: AgentSpec, *, include_adversarial: bool
    ) -> Iterable[SLODefinition]:
        for tc in spec.task_classes:
            window_s = parse_window(tc.slo_window)
            yield SLODefinition(
                agent_id=spec.agent_id,
                task_class=tc.name,
                model_version=spec.model_version,
                sli_kind=tc.slo_sli_kind,
                target=tc.slo_target,
                window_seconds=window_s,
                budget_class=tc.slo_budget_class,
            )
            if include_adversarial and tc.slo_budget_class == "organic":
                # Twin definition: tracks the adversarial slice with the same target.
                yield SLODefinition(
                    agent_id=spec.agent_id,
                    task_class=tc.name,
                    model_version=spec.model_version,
                    sli_kind=tc.slo_sli_kind,
                    target=tc.slo_target,
                    window_seconds=window_s,
                    budget_class="adversarial",
                )

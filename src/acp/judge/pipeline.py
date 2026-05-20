"""Judge pipeline orchestrator.

Pulls unjudged `task_end` events, fans out to all configured judge clients in
parallel, computes panel agreement, writes a `judgment` wide event + per-judge
rows to `judgments`. Hard-coded cross-model invariant: at least one judge must
be of a different family than the agent that produced the run.

Per MASTER_PLAN.md axiom 5 + section 8 T4 defense.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Callable, Iterable

from acp.clock import Clock, default_clock
from acp.events.query import EventQuery
from acp.events.store import WideEventStore
from acp.ids import new_ulid
from acp.judge.disagreement import cohen_kappa
from acp.judge.llm_clients import BaseJudgeClient, default_rubric_schema
from acp.judge.rubric import build_judge_prompt
from acp.schemas.judge import JudgePanelResult, JudgeVerdict, PanelLabel
from acp.schemas.wide_event import WideEvent


# Family mapping: model_version prefix → family name. Hard-coded for v1.
DEFAULT_FAMILY_MAP: dict[str, str] = {
    "claude": "anthropic",
    "anthropic": "anthropic",
    "gpt": "openai",
    "openai": "openai",
    "o1": "openai",
    "o3": "openai",
    "gemini": "google",
    "google": "google",
}


def _family_of(model_version: str, family_map: dict[str, str]) -> str | None:
    """Return the family for `model_version` or None if not recognized.

    Match is case-insensitive prefix. Keys are sorted descending by length so
    the most specific prefix wins (e.g. "claude" before any catch-all).
    """
    mv = (model_version or "").lower()
    for prefix in sorted(family_map.keys(), key=lambda s: -len(s)):
        if mv.startswith(prefix.lower()):
            return family_map[prefix]
    return None


def _panel_label(verdicts: list[JudgeVerdict]) -> PanelLabel:
    passes = sum(1 for v in verdicts if v.passed)
    if passes == len(verdicts):
        return "pass"
    if passes == 0:
        return "fail"
    return "escalate"


class JudgePipeline:
    """Asynchronous judge worker + on-demand judging.

    A pipeline ties together: event store (read+write), event query (read),
    optional registry store (not strictly required), and a list of judge
    clients. The cross-model invariant is enforced at construction.
    """

    def __init__(
        self,
        event_store: WideEventStore,
        query: EventQuery,
        registry_store: object | None,
        judges: list[BaseJudgeClient],
        clock: Clock | None = None,
        *,
        agent_model_version: str | None = None,
        family_map: dict[str, str] | None = None,
    ) -> None:
        if not judges:
            raise ValueError("at least one judge client required")
        self.event_store = event_store
        self.query = query
        self.registry_store = registry_store
        self.judges = judges
        self.clock = clock or default_clock()
        self.family_map = family_map or DEFAULT_FAMILY_MAP
        self.agent_model_version = agent_model_version
        if agent_model_version is not None:
            self._assert_cross_model(agent_model_version)

    # -- invariants --------------------------------------------------------

    def _assert_cross_model(self, agent_model_version: str) -> None:
        """Hard invariant: at least one judge must be a different family than the agent.

        Stub judges are family="stub" and always satisfy the invariant (they
        cannot collude with the agent because they have no model). For real
        LLM-backed judges we compare via the family map.
        """
        agent_family = _family_of(agent_model_version, self.family_map)
        if agent_family is None:
            # Unknown agent family — treat any judge as cross-model.
            return
        differing = [
            j for j in self.judges if getattr(j, "model_family", None) != agent_family
        ]
        if not differing:
            raise ValueError(
                f"cross-model judge invariant violated: agent family={agent_family!r}, "
                f"all {len(self.judges)} judges share that family"
            )

    def assert_cross_model_for(self, agent_model_version: str) -> None:
        """Public accessor used by callers that know the agent at judge time."""
        self._assert_cross_model(agent_model_version)

    # -- single-run judging ------------------------------------------------

    async def judge_task(self, run_id: str) -> JudgePanelResult:
        """Fetch all events for `run_id`, build the prompt, fan out, write back."""
        events: list[WideEvent] = list(self.query.by_run(run_id))
        if not events:
            raise ValueError(f"no events for run_id={run_id!r}")
        # Verify cross-model against this specific run's agent model.
        agent_model_version = events[-1].model_version
        self._assert_cross_model(agent_model_version)

        # Find the task_end (carries the final decision summary).
        decision_event = None
        for e in reversed(events):
            if e.event_type == "task_end":
                decision_event = e
                break

        task_class = events[-1].task_class
        prompt = build_judge_prompt(decision_event, events, task_class=task_class)
        schema = default_rubric_schema()

        verdicts: list[JudgeVerdict] = await asyncio.gather(
            *(j.judge(prompt, schema) for j in self.judges)
        )

        kappa = cohen_kappa(verdicts)
        label = _panel_label(verdicts)
        panel = JudgePanelResult(
            verdicts=list(verdicts),
            agreement_kappa=kappa,
            final_label=label,
        )

        # Emit judgment wide event so SLI can query it.
        self.event_store.emit(
            run_id=run_id,
            agent_id=events[-1].agent_id,
            task_class=task_class,
            model_version=agent_model_version,
            step=events[-1].step + 1,
            event_type="judgment",
            outcome="ok" if label == "pass" else "escalated" if label == "escalate" else "error",
            attrs={
                "verdict": label,
                "original_verdict": label,
                "retroactively_flipped": False,
                "judge_models": [v.judge_model for v in verdicts],
                "kappa": kappa,
                "panel_json": panel.model_dump(),
            },
        )

        # Persist per-judge rows for audit/replay.
        target_event_id = (decision_event or events[-1]).event_id
        now_ms = self.clock.now()
        cur = self.event_store.conn.cursor()
        for v in verdicts:
            cur.execute(
                "INSERT INTO judgments "
                "(judgment_id, event_id, judge_name, judge_model, verdict, "
                " rubric_json, rationale, ts, retroactively_flipped) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    new_ulid(),
                    target_event_id,
                    v.judge_name,
                    v.judge_model,
                    "pass" if v.passed else "fail",
                    json.dumps(v.rubric.model_dump(), sort_keys=True),
                    v.rationale,
                    now_ms,
                ),
            )
        self.event_store.conn.commit()
        return panel

    # -- worker loop -------------------------------------------------------

    async def _unjudged_run_ids(self) -> list[str]:
        """Find task_end run_ids that have not yet produced a judgment event."""
        rows = self.event_store.conn.execute(
            "SELECT DISTINCT e.run_id FROM wide_events e "
            "WHERE e.event_type = 'task_end' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM wide_events j "
            "  WHERE j.run_id = e.run_id AND j.event_type = 'judgment'"
            ") ORDER BY e.run_id"
        ).fetchall()
        return [r["run_id"] for r in rows]

    async def run_worker(
        self,
        interval_seconds: float = 5.0,
        stop_after: int | None = None,
        on_judged: Callable[[str, JudgePanelResult], None] | None = None,
    ) -> int:
        """Polling worker. Returns count of runs judged (helpful for tests).

        `stop_after` lets tests break out of the loop after N polls. Production
        callers pass `None` and stop via task cancellation.
        """
        judged = 0
        polls = 0
        while True:
            run_ids = await self._unjudged_run_ids()
            for rid in run_ids:
                try:
                    panel = await self.judge_task(rid)
                    judged += 1
                    if on_judged is not None:
                        on_judged(rid, panel)
                except Exception:
                    # Don't let one bad run kill the worker.
                    continue
            polls += 1
            if stop_after is not None and polls >= stop_after:
                return judged
            await asyncio.sleep(interval_seconds)


def fetch_judgment_rows(conn: sqlite3.Connection, event_id: str) -> Iterable[sqlite3.Row]:
    """Helper: read back persisted judgment rows for an event."""
    return conn.execute(
        "SELECT * FROM judgments WHERE event_id = ? ORDER BY ts ASC", (event_id,)
    ).fetchall()

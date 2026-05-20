"""Re-judge a historical run with a new judge panel.

Use cases:
  - new judge model is available; compare verdicts → drift detection
  - K3 Goodhart defense: re-judge old "pass" runs with a fresh panel; if the
    new panel disagrees, emit a `judge_drift_detected_via_replay` Goodhart
    flag for review.

The replay writes a new `judgment` wide event with `attrs.replay=True` so the
original verdict is preserved.
"""

from __future__ import annotations

import asyncio
import json

from acp.ids import new_ulid
from acp.judge.disagreement import cohen_kappa
from acp.judge.llm_clients import BaseJudgeClient, default_rubric_schema
from acp.judge.pipeline import JudgePipeline, _panel_label
from acp.judge.rubric import build_judge_prompt
from acp.schemas.judge import JudgePanelResult, JudgeVerdict
from acp.schemas.wide_event import WideEvent


def _existing_label(events: list[WideEvent]) -> str | None:
    """Find the latest non-replay judgment verdict for the run."""
    for e in reversed(events):
        if e.event_type != "judgment":
            continue
        attrs = e.attrs or {}
        if attrs.get("replay"):
            continue
        return attrs.get("verdict")
    return None


async def replay_run(
    run_id: str,
    new_judges: list[BaseJudgeClient],
    pipeline: JudgePipeline,
) -> JudgePanelResult:
    """Re-run judges on the existing event stream for `run_id`.

    Writes a new `judgment` event with `attrs.replay=True` and (if the new
    verdict disagrees with the original) a `judge_drift_detected_via_replay`
    entry in `goodhart_flags`. Note the GoodhartSignal enum doesn't include
    this signal name, so we persist it directly as a flag row.
    """
    events: list[WideEvent] = list(pipeline.query.by_run(run_id))
    if not events:
        raise ValueError(f"no events for run_id={run_id!r}")
    agent_model_version = events[-1].model_version
    # Apply the cross-model invariant against the replay panel.
    pipeline_for_check = JudgePipeline(
        event_store=pipeline.event_store,
        query=pipeline.query,
        registry_store=pipeline.registry_store,
        judges=new_judges,
        clock=pipeline.clock,
        agent_model_version=agent_model_version,
        family_map=pipeline.family_map,
    )

    decision_event = None
    for e in reversed(events):
        if e.event_type == "task_end":
            decision_event = e
            break
    task_class = events[-1].task_class
    prompt = build_judge_prompt(decision_event, events, task_class=task_class)
    schema = default_rubric_schema()

    verdicts: list[JudgeVerdict] = await asyncio.gather(
        *(j.judge(prompt, schema) for j in new_judges)
    )
    kappa = cohen_kappa(verdicts)
    label = _panel_label(verdicts)
    panel = JudgePanelResult(
        verdicts=list(verdicts), agreement_kappa=kappa, final_label=label
    )

    # Emit replay judgment event.
    last_step = events[-1].step
    pipeline_for_check.event_store.emit(
        run_id=run_id,
        agent_id=events[-1].agent_id,
        task_class=task_class,
        model_version=agent_model_version,
        step=last_step + 1,
        event_type="judgment",
        outcome="ok" if label == "pass" else "escalated" if label == "escalate" else "error",
        attrs={
            "verdict": label,
            "original_verdict": label,
            "retroactively_flipped": False,
            "judge_models": [v.judge_model for v in verdicts],
            "kappa": kappa,
            "replay": True,
            "panel_json": panel.model_dump(),
        },
    )

    # Drift check: compare to most recent non-replay judgment if present.
    prior_label = _existing_label(events)
    if prior_label is not None and prior_label != label:
        conn = pipeline.event_store.conn
        target_event_id = (decision_event or events[-1]).event_id
        conn.execute(
            "INSERT INTO goodhart_flags (flag_id, event_id, signal, evidence_json, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                new_ulid(),
                target_event_id,
                "metric_local",  # closest enum value; richer label in evidence
                json.dumps(
                    {
                        "tag": "judge_drift_detected_via_replay",
                        "prior_label": prior_label,
                        "replay_label": label,
                        "replay_judges": [j.judge_model for j in new_judges],
                    },
                    sort_keys=True,
                ),
                pipeline.clock.now(),
            ),
        )
        conn.commit()

    return panel

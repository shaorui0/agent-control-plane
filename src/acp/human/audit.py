"""AuditQueue — review sampled judgments, persist calibration rows.

The judge calibration sampler enqueues events for review (per task-class /
tier sample rates). A human reviewer labels each, and we write a calibration
row so per-judge precision/recall + drift detection can run.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path
from pydantic import BaseModel, Field

from acp.clock import Clock, default_clock
from acp.judge.calibration import record_calibration
from acp.schemas.human import AuditFinding


_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )


def _row_to_finding(row: sqlite3.Row) -> AuditFinding:
    return AuditFinding(
        audit_id=row["audit_id"],
        event_id=row["event_id"],
        reason=row["reason"],
        status=row["status"],
        reviewer=row["reviewer"],
        notes=row["notes"] or "",
        human_label=row["human_label"],
    )


class AuditQueue:
    """Read/submit for `audit_queue`. Writes calibration row on submit."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock | None = None) -> None:
        self.conn = conn
        self.clock = clock or default_clock()

    def list_pending(self, reason: str | None = None) -> list[AuditFinding]:
        q = "SELECT * FROM audit_queue WHERE status = 'pending'"
        params: tuple[Any, ...] = ()
        if reason:
            q += " AND reason = ?"
            params = (reason,)
        q += " ORDER BY audit_id ASC"
        rows = self.conn.execute(q, params).fetchall()
        return [_row_to_finding(r) for r in rows]

    def get(self, audit_id: str) -> AuditFinding | None:
        row = self.conn.execute(
            "SELECT * FROM audit_queue WHERE audit_id = ?", (audit_id,)
        ).fetchone()
        return _row_to_finding(row) if row else None

    def submit(
        self,
        audit_id: str,
        human_label: str,
        notes: str,
        reviewer: str,
    ) -> AuditFinding:
        if not reviewer or not reviewer.strip():
            raise ValueError("reviewer is required")
        if human_label not in ("pass", "fail"):
            raise ValueError(f"human_label must be 'pass' or 'fail', got {human_label!r}")
        existing = self.get(audit_id)
        if existing is None:
            raise KeyError(f"unknown audit {audit_id!r}")
        if existing.status != "pending":
            raise ValueError(f"audit {audit_id!r} already {existing.status}")

        ts = self.clock.now()
        with self.conn:
            self.conn.execute(
                "UPDATE audit_queue SET status = 'reviewed', reviewer = ?, "
                "notes = ?, human_label = ? WHERE audit_id = ?",
                (reviewer, notes, human_label, audit_id),
            )

        # Persist calibration row. We resolve judge_panel_label + judge_model +
        # task_class from the wide_events judgment row that this audit points
        # at. If we can't find one, we still record with placeholders so the
        # human label is not lost.
        jrow = self.conn.execute(
            "SELECT attrs_json, task_class FROM wide_events "
            "WHERE event_id = ? AND event_type = 'judgment'",
            (existing.event_id,),
        ).fetchone()
        judge_panel_label = "unknown"
        judge_model = "unknown"
        task_class = "unknown"
        if jrow is not None:
            import json

            try:
                attrs = json.loads(jrow["attrs_json"] or "{}")
            except (TypeError, ValueError):
                attrs = {}
            judge_panel_label = str(attrs.get("verdict", "unknown"))
            jms = attrs.get("judge_models") or []
            judge_model = jms[0] if jms else "unknown"
            task_class = jrow["task_class"] or "unknown"

        record_calibration(
            event_id=existing.event_id,
            judge_panel_label=judge_panel_label,
            human_label=human_label,
            judge_model=judge_model,
            task_class=task_class,
            conn=self.conn,
            ts_ms=ts,
        )

        return self.get(audit_id)  # type: ignore[return-value]


# ----------------------------------------------------------------------------
# HTTP routes
# ----------------------------------------------------------------------------


class AuditDecideBody(BaseModel):
    human_label: str = Field(..., pattern=r"^(pass|fail)$")
    reviewer: str = Field(..., min_length=1)
    notes: str = ""


def build_audit_router(queue: AuditQueue) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/audit")
    def list_audits(reason: str | None = None) -> list[dict[str, Any]]:
        return [a.model_dump() for a in queue.list_pending(reason)]

    @r.post("/v1/audit/{audit_id}/decide")
    def decide_audit(audit_id: str, body: AuditDecideBody) -> dict[str, Any]:
        try:
            updated = queue.submit(audit_id, body.human_label, body.notes, body.reviewer)
        except KeyError as e:
            raise HTTPException(status_code=404, detail={"error_code": "audit_unknown"}) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"error_code": "invalid_request", "message": str(e)}) from e
        return updated.model_dump()

    @r.get("/audit", response_class=HTMLResponse)
    def html_audit(reason: str | None = None) -> str:
        env = _jinja_env()
        tpl = env.get_template("audit.html.j2")
        return tpl.render(findings=queue.list_pending(reason), filter_reason=reason or "")

    return r


__all__ = ["AuditQueue", "build_audit_router", "AuditDecideBody"]

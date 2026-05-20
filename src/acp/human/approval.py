"""ApprovalQueue — list/decide on pending T3+ tool invocations.

Pending approvals are written by the gateway when a sealed tool's max_tier is
T3/T4. Humans decide via either the CLI or the FastAPI route below; on decide
we emit a wide_event `event_type='intervention'` so the audit trail is honest
about who said yes/no and when.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path
from pydantic import BaseModel, Field

from acp.clock import Clock, default_clock
from acp.events.store import WideEventStore
from acp.schemas.human import ApprovalRequest


_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )


def _parse_args(args_blob: str | None) -> dict[str, Any]:
    """Approvals were written by gateway via `str(dict)`. Tolerate both."""
    if not args_blob:
        return {}
    try:
        return json.loads(args_blob)
    except (TypeError, ValueError):
        # Fallback: repr of a dict via ast.literal_eval — safe for primitives.
        import ast

        try:
            v = ast.literal_eval(args_blob)
            return v if isinstance(v, dict) else {"_raw": args_blob}
        except Exception:
            return {"_raw": args_blob}


def _row_to_request(row: sqlite3.Row) -> ApprovalRequest:
    return ApprovalRequest(
        approval_id=row["approval_id"],
        event_id=row["event_id"],
        agent_id=row["agent_id"],
        tool_name=row["tool_name"],
        intent=row["intent"] or "(none)",
        args_json=_parse_args(row["args_json"]),
        status=row["status"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
    )


class ApprovalQueue:
    """Read/decide for `approvals`. Side-effect: emits intervention wide_event."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        event_store: WideEventStore | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.conn = conn
        self.event_store = event_store
        self.clock = clock or default_clock()

    def list_pending(self, agent_id: str | None = None) -> list[ApprovalRequest]:
        q = "SELECT * FROM approvals WHERE status = 'pending'"
        params: tuple[Any, ...] = ()
        if agent_id:
            q += " AND agent_id = ?"
            params = (agent_id,)
        q += " ORDER BY approval_id ASC"
        rows = self.conn.execute(q, params).fetchall()
        return [_row_to_request(r) for r in rows]

    def get(self, approval_id: str) -> ApprovalRequest | None:
        row = self.conn.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        return _row_to_request(row) if row else None

    def decide(
        self,
        approval_id: str,
        decision: Literal["approved", "rejected"],
        reviewer: str,
        notes: str = "",
    ) -> ApprovalRequest:
        if decision not in ("approved", "rejected"):
            raise ValueError(f"invalid decision: {decision!r}")
        if not reviewer or not reviewer.strip():
            raise ValueError("reviewer is required")
        existing = self.get(approval_id)
        if existing is None:
            raise KeyError(f"unknown approval {approval_id!r}")
        if existing.status != "pending":
            raise ValueError(f"approval {approval_id!r} already {existing.status}")

        ts = self.clock.now()
        with self.conn:
            self.conn.execute(
                "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ? "
                "WHERE approval_id = ?",
                (decision, reviewer, ts, approval_id),
            )

        # Emit intervention wide_event so downstream SLIs see the decision.
        if self.event_store is not None:
            # We don't have the run's full context here. The approval row carries
            # `event_id` which we recorded as the run_id at insert time (gateway
            # writes run_id into event_id; see gateway/routes.py invoke flow).
            run_id = existing.event_id
            # Pull the most recent step for this run to keep step monotonic.
            tail = self.conn.execute(
                "SELECT MAX(step) AS s, agent_id, task_class, model_version "
                "FROM wide_events WHERE run_id = ? GROUP BY run_id",
                (run_id,),
            ).fetchone()
            if tail is not None and tail["s"] is not None:
                # WideEvent.outcome is a closed Literal; we map approve/reject
                # onto allowed values and stash the literal decision in attrs.
                mapped_outcome = "ok" if decision == "approved" else "denied"
                self.event_store.emit(
                    run_id=run_id,
                    agent_id=tail["agent_id"],
                    task_class=tail["task_class"],
                    model_version=tail["model_version"],
                    step=int(tail["s"]) + 1,
                    event_type="intervention",
                    tool_name=existing.tool_name,
                    outcome=mapped_outcome,
                    intent=existing.intent,
                    attrs={
                        "approval_id": approval_id,
                        "reviewer": reviewer,
                        "notes": notes,
                        "decision": decision,
                    },
                )

        return self.get(approval_id)  # type: ignore[return-value]


# ----------------------------------------------------------------------------
# HTTP routes
# ----------------------------------------------------------------------------


class DecideBody(BaseModel):
    decision: Literal["approved", "rejected"]
    reviewer: str = Field(..., min_length=1)
    notes: str = ""


def build_approval_router(queue: ApprovalQueue) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/approvals")
    def list_approvals(agent_id: str | None = None) -> list[dict[str, Any]]:
        items = queue.list_pending(agent_id)
        return [i.model_dump() for i in items]

    @r.post("/v1/approvals/{approval_id}/decide")
    def decide_approval(approval_id: str, body: DecideBody) -> dict[str, Any]:
        try:
            updated = queue.decide(approval_id, body.decision, body.reviewer, body.notes)
        except KeyError as e:
            raise HTTPException(status_code=404, detail={"error_code": "approval_unknown"}) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"error_code": "invalid_request", "message": str(e)}) from e
        return updated.model_dump()

    @r.get("/approvals", response_class=HTMLResponse)
    def html_approvals(agent_id: str | None = None) -> str:
        env = _jinja_env()
        tpl = env.get_template("approvals.html.j2")
        return tpl.render(approvals=queue.list_pending(agent_id), filter_agent=agent_id or "")

    return r


__all__ = ["ApprovalQueue", "build_approval_router", "DecideBody"]

"""Dashboard — server-side HTML view onto agent state.

One template, no JS framework, vanilla CSS, auto-refresh via <meta>.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from acp.autonomy.controller import AutonomyController
from acp.human.approval import ApprovalQueue
from acp.human.audit import AuditQueue
from acp.registry.store import RegistryStore


_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )


def _burn_color(burn_rate: float, budget_remaining: float) -> str:
    """Map (burn_rate, budget_remaining) to a coarse color label."""
    if budget_remaining <= 0.0 or burn_rate >= 10.0:
        return "red"
    if burn_rate >= 5.0:
        return "orange"
    if burn_rate >= 2.0:
        return "yellow"
    return "green"


@dataclass
class DashboardData:
    agents: list[dict[str, Any]]
    pending_approvals: int
    pending_audits: int
    recent_events: list[dict[str, Any]]
    refresh_seconds: int


def _gather(
    conn: sqlite3.Connection,
    registry: RegistryStore,
    autonomy: AutonomyController,
    approvals: ApprovalQueue,
    audit: AuditQueue,
    refresh_seconds: int,
) -> DashboardData:
    agents: list[dict[str, Any]] = []
    for spec in registry.all_agents():
        # Per-task-class tier.
        tiers: list[dict[str, str]] = []
        for tc in spec.task_classes:
            tier = autonomy.current_tier(spec.agent_id, tc.name)
            tiers.append({"task_class": tc.name, "tier": tier.value})

        # Latest snapshot per (task_class, window_label) for this agent.
        snap_rows = conn.execute(
            "SELECT s.* FROM slo_snapshots s "
            "JOIN (SELECT agent_id, task_class, window_label, MAX(ts) AS ts "
            "      FROM slo_snapshots WHERE agent_id = ? "
            "      GROUP BY agent_id, task_class, window_label) latest "
            "ON s.agent_id = latest.agent_id AND s.task_class = latest.task_class "
            "AND s.window_label = latest.window_label AND s.ts = latest.ts",
            (spec.agent_id,),
        ).fetchall()
        snaps = []
        for r in snap_rows:
            snaps.append(
                {
                    "task_class": r["task_class"],
                    "window_label": r["window_label"],
                    "budget_class": r["budget_class"],
                    "burn_rate": float(r["burn_rate"]),
                    "budget_remaining": float(r["budget_remaining"]),
                    "color": _burn_color(float(r["burn_rate"]), float(r["budget_remaining"])),
                }
            )

        agents.append(
            {
                "agent_id": spec.agent_id,
                "owner": spec.owner,
                "model_version": spec.model_version,
                "default_tier": spec.default_tier.value,
                "tiers": tiers,
                "snapshots": snaps,
            }
        )

    # Counts.
    pending_apps = int(
        conn.execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0]
    )
    pending_aud = int(
        conn.execute("SELECT COUNT(*) FROM audit_queue WHERE status='pending'").fetchone()[0]
    )

    # Last 20 wide_events.
    ev_rows = conn.execute(
        "SELECT event_id, run_id, agent_id, event_type, tool_name, outcome, ts "
        "FROM wide_events ORDER BY ts DESC LIMIT 20"
    ).fetchall()
    events = [
        {
            "event_id": r["event_id"],
            "run_id": r["run_id"],
            "agent_id": r["agent_id"],
            "event_type": r["event_type"],
            "tool_name": r["tool_name"] or "-",
            "outcome": r["outcome"] or "-",
            "ts": r["ts"],
        }
        for r in ev_rows
    ]

    # Suppress unused-import lint for json (kept for forward-compat).
    _ = json
    return DashboardData(
        agents=agents,
        pending_approvals=pending_apps,
        pending_audits=pending_aud,
        recent_events=events,
        refresh_seconds=refresh_seconds,
    )


def build_dashboard_router(
    conn: sqlite3.Connection,
    registry: RegistryStore,
    autonomy: AutonomyController,
    approvals: ApprovalQueue,
    audit: AuditQueue,
    refresh_seconds: int = 30,
) -> APIRouter:
    r = APIRouter()

    @r.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> str:
        data = _gather(conn, registry, autonomy, approvals, audit, refresh_seconds)
        env = _jinja_env()
        tpl = env.get_template("dashboard.html.j2")
        return tpl.render(data=data)

    return r


__all__ = ["build_dashboard_router", "DashboardData"]

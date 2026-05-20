"""ACP server lifespan + FastAPI app factory.

`create_app(settings=...)` wires up every long-lived component (registry,
event store, autonomy controller, SLO engine, judge pipeline, idempotency,
auth, approval/audit queues, pager) and mounts all the routers.

Lifespan:
  - startup: migrate DB, load registry, init autonomy state, start SLO scheduler
    + judge worker + autonomy tick.
  - shutdown: cancel async tasks, shutdown scheduler, close DB.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI

from acp import db as _db
from acp.autonomy.controller import AutonomyController
from acp.autonomy.transitions import run_autonomy_tick
from acp.clock import RealClock
from acp.events.query import EventQuery
from acp.events.store import WideEventStore
from acp.gateway.auth import SessionAuth
from acp.gateway.budget import BudgetManager
from acp.gateway.idempotency import IdempotencyVault
from acp.gateway.routes import GatewayDeps, build_router as build_gateway_router
from acp.gateway.tools.base import REGISTRY as TOOL_REGISTRY
from acp.human.approval import ApprovalQueue, build_approval_router
from acp.human.audit import AuditQueue, build_audit_router
from acp.human.dashboard import build_dashboard_router
from acp.human.pager import OwnerPager
from acp.judge.llm_clients import BaseJudgeClient
from acp.judge.pipeline import JudgePipeline
from acp.registry.loader import install_sighup_handler
from acp.registry.store import RegistryStore
from acp.settings import Settings, get_settings
from acp.slo.definitions import SLODefinitionRegistry
from acp.slo.engine import SLOEngine

log = logging.getLogger("acp.server")


@dataclass
class AppState:
    """Lifespan-owned components, attached to app.state."""

    settings: Settings
    conn: sqlite3.Connection
    registry: RegistryStore
    events: WideEventStore
    query: EventQuery
    autonomy: AutonomyController
    slo: SLOEngine
    judge: JudgePipeline | None
    auth: SessionAuth
    budget: BudgetManager
    idempotency: IdempotencyVault
    approvals: ApprovalQueue
    audits: AuditQueue
    pager: OwnerPager
    # background handles
    scheduler: object | None = None
    judge_task: asyncio.Task | None = None


def _default_judges() -> list[BaseJudgeClient]:
    """Build the default judge panel — stub-only unless LLM keys are present."""
    from acp.judge.llm_clients import StubJudge

    judges: list[BaseJudgeClient] = [
        StubJudge(name="stub-A"),
        StubJudge(name="stub-B"),
    ]
    return judges


def create_app(settings: Settings | None = None) -> FastAPI:
    """FastAPI app factory.

    Components are wired in lifespan startup so tests can construct the app
    without starting background work — pass a Settings whose db_path lives
    in tmp_path.
    """
    cfg = settings or get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = _startup(cfg)
        app.state.acp = state
        # Mount routers AFTER startup so all components exist. We do it here so
        # the same factory works for tests (TestClient triggers lifespan).
        _mount_routers(app, state)
        try:
            yield
        finally:
            await _shutdown(state)

    app = FastAPI(title="ACP", lifespan=lifespan)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        return {"status": "ready"}

    return app


def _startup(cfg: Settings) -> AppState:
    log.info("ACP startup; db=%s registry=%s", cfg.db_path, cfg.registry_dir)

    # 1) DB.
    conn = _db.connect(cfg.db_path)
    _db.migrate(conn)

    # 2) Registry (owns the `agents` table — see W5A schema collision fix).
    registry = RegistryStore(conn, Path(cfg.registry_dir))
    if Path(cfg.registry_dir).exists():
        registry.load()

    clock = RealClock()

    # 3) Core components.
    events = WideEventStore(conn, clock=clock)
    query = EventQuery(conn)
    autonomy = AutonomyController(conn, events, registry_store=registry, clock=clock)
    for spec in registry.all_agents():
        autonomy.initialize_for_agent(spec.agent_id)

    slo_defs = SLODefinitionRegistry(registry)
    slo = SLOEngine(conn, query, registry, slo_defs, clock)

    # Judge pipeline — stubs only by default; tests skip the worker entirely.
    judge: JudgePipeline | None
    try:
        judge = JudgePipeline(events, query, registry, _default_judges(), clock=clock)
    except Exception as e:
        log.warning("judge pipeline disabled: %s", e)
        judge = None

    # 4) Auth / budget / idempotency.
    if cfg.session_secret:
        auth = SessionAuth(secret=cfg.session_secret.encode())
    else:
        auth = SessionAuth()
    budget = BudgetManager(conn, registry, clock=clock)
    idempotency = IdempotencyVault()

    # 5) Human-loop.
    approvals = ApprovalQueue(conn, event_store=events, clock=clock)
    audits = AuditQueue(conn, clock=clock)
    pager = OwnerPager(registry=registry, settings=cfg)

    # 6) Background work.
    scheduler = None
    judge_task = None
    if cfg.db_path != Path(":memory:"):
        try:
            scheduler = slo.start_scheduler(60)
            # Add the autonomy tick on the same scheduler.
            scheduler.add_job(
                lambda: run_autonomy_tick(autonomy, slo, query, conn),
                "interval",
                seconds=60,
                id="autonomy-tick",
            )
        except Exception as e:
            log.warning("scheduler disabled: %s", e)

    # SIGHUP reload handler.
    try:
        install_sighup_handler(registry.reload)
    except Exception as e:
        log.warning("SIGHUP handler not installed: %s", e)

    return AppState(
        settings=cfg,
        conn=conn,
        registry=registry,
        events=events,
        query=query,
        autonomy=autonomy,
        slo=slo,
        judge=judge,
        auth=auth,
        budget=budget,
        idempotency=idempotency,
        approvals=approvals,
        audits=audits,
        pager=pager,
        scheduler=scheduler,
        judge_task=judge_task,
    )


def _mount_routers(app: FastAPI, state: AppState) -> None:
    deps = GatewayDeps(
        conn=state.conn,
        registry=state.registry,
        events=state.events,
        auth=state.auth,
        budget=state.budget,
        idempotency=state.idempotency,
        tools=TOOL_REGISTRY,
        autonomy=state.autonomy,  # W5A: real controller replaces DefaultAutonomyProvider.
    )
    app.include_router(build_gateway_router(deps))
    app.include_router(build_approval_router(state.approvals))
    app.include_router(build_audit_router(state.audits))
    app.include_router(
        build_dashboard_router(
            state.conn,
            state.registry,
            state.autonomy,
            state.approvals,
            state.audits,
            refresh_seconds=state.settings.dashboard_refresh_seconds,
        )
    )


async def _shutdown(state: AppState) -> None:
    log.info("ACP shutdown")
    if state.judge_task is not None:
        state.judge_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state.judge_task
    if state.scheduler is not None:
        try:
            state.scheduler.shutdown(wait=False)
        except Exception:
            pass
    try:
        state.conn.close()
    except Exception:
        pass


# Module-level `app` so `uvicorn acp.server:app` works.
app = create_app()


__all__ = ["create_app", "AppState", "app"]

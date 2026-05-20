"""Gateway HTTP routes — the only door for agents.

ALL agent-facing errors are scrubbed to `{"error_code": "<reason>", "message": ""}`.
No tracebacks. No internal detail. Operators see internal detail via logs.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from acp.errors import ACPError, BudgetExceeded, DenyClosed
from acp.events.store import WideEventStore
from acp.gateway.attestation import emit_attested_event
from acp.gateway.auth import SessionAuth
from acp.gateway.budget import BudgetManager
from acp.gateway.egress_dlp import assert_no_egress_violation
from acp.gateway.idempotency import IdempotencyVault
from acp.gateway.policy import (
    intent_check,
    kwargs_check,
    seal_check,
    tier_check,
)
from acp.gateway.tools.base import ToolRegistry
from acp.ids import args_hash, new_ulid
from acp.registry.store import RegistryStore
from acp.schemas.agent import AutonomyTier, ToolBinding


# -----------------------------------------------------------------------------
# Autonomy provider — W4C contract
# -----------------------------------------------------------------------------


class AutonomyProvider(Protocol):
    def current_tier(self, agent_id: str, task_class: str) -> AutonomyTier: ...


@dataclass
class DefaultAutonomyProvider:
    """Fallback used until W4C wires in the live controller.

    TODO(W4C): replace with autonomy.controller.AutonomyController.
    """

    registry: RegistryStore

    def current_tier(self, agent_id: str, task_class: str) -> AutonomyTier:
        spec = self.registry.get(agent_id)
        if spec is None:
            return AutonomyTier.T0
        return spec.default_tier


# -----------------------------------------------------------------------------
# DI container
# -----------------------------------------------------------------------------


@dataclass
class GatewayDeps:
    conn: sqlite3.Connection
    registry: RegistryStore
    events: WideEventStore
    auth: SessionAuth
    budget: BudgetManager
    idempotency: IdempotencyVault
    tools: ToolRegistry
    autonomy: AutonomyProvider
    step_counters: dict[str, int] = field(default_factory=dict)

    def next_step(self, run_id: str) -> int:
        n = self.step_counters.get(run_id, 0) + 1
        self.step_counters[run_id] = n
        return n


# -----------------------------------------------------------------------------
# Request / response models
# -----------------------------------------------------------------------------


class SessionCreate(BaseModel):
    agent_id: str = Field(..., min_length=1)
    task_class: str = Field(..., min_length=1)
    input: dict[str, Any] = Field(default_factory=dict)


class SessionCreated(BaseModel):
    run_id: str
    bearer: str
    idempotency_key: str
    issued_step: int


class DecisionIn(BaseModel):
    intent: str = ""
    rationale: str = ""
    chosen_tool: str | None = None
    chosen_args: dict[str, Any] = Field(default_factory=dict)


class InvokeIn(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)
    intent: str = ""
    idempotency_key: str = Field(..., min_length=26, max_length=26)
    agent_claim: str | None = None
    est_tokens: int = 0
    est_usd_micros: int = 0


class EndIn(BaseModel):
    final_output: dict[str, Any] = Field(default_factory=dict)
    agent_claim_outcome: str | None = None


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _deny_response(code: str) -> dict[str, str]:
    """Agent-facing error envelope — reason code only."""
    return {"error_code": code, "message": ""}


def _check_bearer(deps: GatewayDeps, run_id: str, authorization: str | None) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail=_deny_response("missing_bearer"))
    bearer = authorization.split(" ", 1)[1].strip()
    if not deps.auth.verify(bearer, run_id):
        raise HTTPException(status_code=401, detail=_deny_response("invalid_bearer"))


def _binding_for(
    deps: GatewayDeps, agent_id: str, tool_name: str
) -> ToolBinding | None:
    return deps.registry.get_tool_binding(agent_id, tool_name)


# -----------------------------------------------------------------------------
# Router factory
# -----------------------------------------------------------------------------


def build_router(deps: GatewayDeps) -> APIRouter:
    r = APIRouter(prefix="/v1")

    @r.post("/sessions")
    def create_session(body: SessionCreate) -> SessionCreated:
        spec = deps.registry.get(body.agent_id)
        if spec is None:
            raise HTTPException(status_code=404, detail=_deny_response("agent_unknown"))
        if not any(tc.name == body.task_class for tc in spec.task_classes):
            raise HTTPException(
                status_code=400, detail=_deny_response("task_class_unknown")
            )

        run_id, bearer = deps.auth.issue_session(body.agent_id, body.task_class)
        idem_key = deps.idempotency.issue(run_id)
        step = deps.next_step(run_id)
        emit_attested_event(
            deps.events,
            run_id=run_id,
            agent_id=body.agent_id,
            task_class=body.task_class,
            model_version=spec.model_version,
            step=step,
            event_type="task_start",
            outcome="ok",
            extra_attrs={"input_keys": sorted(body.input.keys())},
        )
        return SessionCreated(
            run_id=run_id, bearer=bearer, idempotency_key=idem_key, issued_step=step
        )

    @r.post("/sessions/{run_id}/decisions")
    def post_decision(
        run_id: str,
        body: DecisionIn,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _check_bearer(deps, run_id, authorization)
        sess = deps.auth.get(run_id)
        assert sess is not None  # _check_bearer already verified
        spec = deps.registry.get(sess.agent_id)
        if spec is None:
            raise HTTPException(status_code=404, detail=_deny_response("agent_unknown"))
        step = deps.next_step(run_id)
        emit_attested_event(
            deps.events,
            run_id=run_id,
            agent_id=sess.agent_id,
            task_class=sess.task_class,
            model_version=spec.model_version,
            step=step,
            event_type="task_start",  # decision logged as pre-action event
            intent=body.intent,
            agent_claim=body.rationale,
            outcome="pending",
            extra_attrs={
                "decision": True,
                "chosen_tool": body.chosen_tool,
                "chosen_args_hash": args_hash(body.chosen_args) if body.chosen_args else None,
            },
        )
        return {"ok": True, "step": step}

    @r.post("/sessions/{run_id}/tools/{name}/invoke")
    def invoke_tool(
        run_id: str,
        name: str,
        body: InvokeIn,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _check_bearer(deps, run_id, authorization)
        sess = deps.auth.get(run_id)
        assert sess is not None
        spec = deps.registry.get(sess.agent_id)
        if spec is None:
            raise HTTPException(status_code=404, detail=_deny_response("agent_unknown"))

        # 1) idempotency — server-issued keys only.
        try:
            deps.idempotency.check_and_consume(body.idempotency_key, run_id)
        except DenyClosed as e:
            raise HTTPException(status_code=400, detail=_deny_response(e.reason_code)) from e

        binding = _binding_for(deps, sess.agent_id, name)

        # 2) policy chain — fail-closed.
        try:
            seal_check(binding, name)
            assert binding is not None  # narrowed by seal_check
            current_tier = deps.autonomy.current_tier(sess.agent_id, sess.task_class)
            tier_check(binding, current_tier)
            intent_check(binding, body.intent)
            kwargs_check(binding, body.args)
            assert_no_egress_violation(body.args)
            deps.budget.check_and_reserve(sess.agent_id, body.est_tokens, body.est_usd_micros)
        except DenyClosed as e:
            step = deps.next_step(run_id)
            emit_attested_event(
                deps.events,
                run_id=run_id,
                agent_id=sess.agent_id,
                task_class=sess.task_class,
                model_version=spec.model_version,
                step=step,
                event_type="tool_call",
                tool_name=name,
                tier_required=binding.max_tier.value if binding else None,
                outcome="denied",
                intent=body.intent,
                agent_claim=body.agent_claim,
                args=body.args,
                extra_attrs={"reason_code": e.reason_code},
            )
            raise HTTPException(status_code=403, detail=_deny_response(e.reason_code)) from e
        except BudgetExceeded as e:
            step = deps.next_step(run_id)
            emit_attested_event(
                deps.events,
                run_id=run_id,
                agent_id=sess.agent_id,
                task_class=sess.task_class,
                model_version=spec.model_version,
                step=step,
                event_type="tool_call",
                tool_name=name,
                outcome="denied",
                intent=body.intent,
                args=body.args,
                extra_attrs={"reason_code": "budget_exhausted", "kind": e.kind},
            )
            raise HTTPException(
                status_code=429, detail=_deny_response("budget_exhausted")
            ) from e

        # 3) tool_call event (pre-execution, Gateway-attested).
        step_call = deps.next_step(run_id)
        emit_attested_event(
            deps.events,
            run_id=run_id,
            agent_id=sess.agent_id,
            task_class=sess.task_class,
            model_version=spec.model_version,
            step=step_call,
            event_type="tool_call",
            tool_name=name,
            tier_required=binding.max_tier.value,
            outcome="ok",
            intent=body.intent,
            agent_claim=body.agent_claim,
            args=body.args,
        )

        # 4) T3+ -> approval queue.
        if binding.max_tier in (AutonomyTier.T3, AutonomyTier.T4):
            approval_id = new_ulid()
            deps.conn.execute(
                "INSERT INTO approvals (approval_id, event_id, agent_id, tool_name, intent, args_json, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (
                    approval_id,
                    run_id,  # FK-ish — approvals carry run_id (the invocation context)
                    sess.agent_id,
                    name,
                    body.intent,
                    str(body.args),
                ),
            )
            deps.conn.commit()
            return {"status": "pending_approval", "approval_id": approval_id}

        # 5) Execute the (mocked) tool.
        try:
            result, latency_ms = deps.tools.dispatch(name, body.args, run_id)
            outcome = "ok"
            err: str | None = None
        except Exception as e:  # mocks shouldn't raise, but be defensive
            result = {}
            latency_ms = 0
            outcome = "error"
            err = type(e).__name__

        # 6) Record actuals + emit tool_result.
        deps.budget.record_actual(sess.agent_id, body.est_tokens, body.est_usd_micros)
        step_result = deps.next_step(run_id)
        emit_attested_event(
            deps.events,
            run_id=run_id,
            agent_id=sess.agent_id,
            task_class=sess.task_class,
            model_version=spec.model_version,
            step=step_result,
            event_type="tool_result",
            tool_name=name,
            outcome=outcome,
            intent=body.intent,
            agent_claim=body.agent_claim,
            result=result if outcome == "ok" else None,
            latency_ms=latency_ms,
            cost_usd_micros=body.est_usd_micros,
            tokens=body.est_tokens,
            extra_attrs={"error": err} if err else None,
        )

        # Refresh idempotency key for next call (one fresh key per consumed key).
        next_key = deps.idempotency.issue(run_id)
        return {
            "status": outcome,
            "result": result,
            "latency_ms": latency_ms,
            "next_idempotency_key": next_key,
        }

    @r.post("/sessions/{run_id}/end")
    def end_session(
        run_id: str,
        body: EndIn,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _check_bearer(deps, run_id, authorization)
        sess = deps.auth.get(run_id)
        assert sess is not None
        spec = deps.registry.get(sess.agent_id)
        if spec is None:
            raise HTTPException(status_code=404, detail=_deny_response("agent_unknown"))
        step = deps.next_step(run_id)
        emit_attested_event(
            deps.events,
            run_id=run_id,
            agent_id=sess.agent_id,
            task_class=sess.task_class,
            model_version=spec.model_version,
            step=step,
            event_type="task_end",
            outcome="ok",
            agent_claim=body.agent_claim_outcome,
            extra_attrs={"final_output_keys": sorted(body.final_output.keys())},
        )
        deps.idempotency.reset_run(run_id)
        deps.auth.end(run_id)
        return {"ok": True}

    @r.get("/sessions/{run_id}/approvals/{approval_id}")
    def get_approval(
        run_id: str,
        approval_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _check_bearer(deps, run_id, authorization)
        row = deps.conn.execute(
            "SELECT status, decided_by, decided_at FROM approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=_deny_response("approval_unknown"))
        return {
            "approval_id": approval_id,
            "status": row["status"],
            "decided_by": row["decided_by"],
            "decided_at": row["decided_at"],
        }

    return r


def acp_error_envelope(exc: ACPError) -> dict[str, str]:
    """Agent-safe error envelope. Wire this into the top-level app exception handler."""
    code = getattr(exc, "reason_code", "internal_error")
    return {"error_code": code, "message": ""}

"""ACP SDK clients.

`ACPClient` is the Protocol; `LocalClient` calls the FastAPI app in-process via
`fastapi.testclient.TestClient` (deterministic, no HTTP, suitable for tests and
the demo runner). `RemoteClient` uses httpx and talks to a real `/v1/*` server.

Both managers track:
  - bearer token (issued at `start_session`)
  - the current server-issued idempotency key (rotated after each invoke)
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import httpx
from fastapi import FastAPI

from acp.errors import DenyClosed


class ACPClient(Protocol):
    """Protocol for agent-facing ACP clients.

    All methods are async to keep parity with real HTTP clients; the local
    implementation runs sync handlers under the hood but exposes async
    signatures so swapping LocalClient <-> RemoteClient requires no code change.
    """

    async def start_session(
        self, agent_id: str, task_class: str, input: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...

    async def post_decision(
        self, run_id: str, intent: str, rationale: str,
        chosen_tool: str | None = None, chosen_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def invoke_tool(
        self, run_id: str, tool: str, args: dict[str, Any],
        intent: str = "", agent_claim: str | None = None,
        est_tokens: int = 0, est_usd_micros: int = 0,
    ) -> dict[str, Any]: ...

    async def end_session(
        self, run_id: str, final_output: dict[str, Any] | None = None,
        agent_claim_outcome: str | None = None,
    ) -> dict[str, Any]: ...

    async def poll_approval(
        self, run_id: str, approval_id: str, timeout_s: float = 10.0,
        interval_s: float = 0.1,
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# In-process client
# ---------------------------------------------------------------------------


class LocalClient:
    """In-process client backed by a FastAPI app (TestClient).

    Use this in tests and the demo to skip real HTTP. The wire shape matches
    `RemoteClient` exactly so swapping is a one-line change.
    """

    def __init__(self, app: FastAPI) -> None:
        # Import locally so test-only dep doesn't leak into prod requires.
        from fastapi.testclient import TestClient
        self._client = TestClient(app)
        # run_id -> {bearer, idem_key}
        self._sessions: dict[str, dict[str, str]] = {}

    def _headers(self, run_id: str) -> dict[str, str]:
        bearer = self._sessions[run_id]["bearer"]
        return {"Authorization": f"Bearer {bearer}"}

    def _idem(self, run_id: str) -> str:
        return self._sessions[run_id]["idem_key"]

    def _rotate_idem(self, run_id: str, new_key: str) -> None:
        self._sessions[run_id]["idem_key"] = new_key

    async def start_session(
        self, agent_id: str, task_class: str, input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = self._client.post(
            "/v1/sessions",
            json={"agent_id": agent_id, "task_class": task_class, "input": input or {}},
        )
        if resp.status_code != 200:
            err = resp.json().get("detail", {}).get("error_code", "unknown")
            raise DenyClosed(err, f"start_session failed: {resp.status_code}")
        data = resp.json()
        self._sessions[data["run_id"]] = {
            "bearer": data["bearer"],
            "idem_key": data["idempotency_key"],
        }
        return data

    async def post_decision(
        self, run_id: str, intent: str, rationale: str,
        chosen_tool: str | None = None, chosen_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = self._client.post(
            f"/v1/sessions/{run_id}/decisions",
            headers=self._headers(run_id),
            json={
                "intent": intent, "rationale": rationale,
                "chosen_tool": chosen_tool, "chosen_args": chosen_args or {},
            },
        )
        return resp.json()

    async def invoke_tool(
        self, run_id: str, tool: str, args: dict[str, Any],
        intent: str = "", agent_claim: str | None = None,
        est_tokens: int = 0, est_usd_micros: int = 0,
    ) -> dict[str, Any]:
        resp = self._client.post(
            f"/v1/sessions/{run_id}/tools/{tool}/invoke",
            headers=self._headers(run_id),
            json={
                "args": args, "intent": intent,
                "idempotency_key": self._idem(run_id),
                "agent_claim": agent_claim,
                "est_tokens": est_tokens, "est_usd_micros": est_usd_micros,
            },
        )
        data = resp.json()
        if resp.status_code != 200:
            err = data.get("detail", {}).get("error_code", "unknown")
            raise DenyClosed(err, f"invoke failed: {resp.status_code}")
        if "next_idempotency_key" in data:
            self._rotate_idem(run_id, data["next_idempotency_key"])
        return data

    async def end_session(
        self, run_id: str, final_output: dict[str, Any] | None = None,
        agent_claim_outcome: str | None = None,
    ) -> dict[str, Any]:
        resp = self._client.post(
            f"/v1/sessions/{run_id}/end",
            headers=self._headers(run_id),
            json={
                "final_output": final_output or {},
                "agent_claim_outcome": agent_claim_outcome,
            },
        )
        return resp.json()

    async def poll_approval(
        self, run_id: str, approval_id: str,
        timeout_s: float = 10.0, interval_s: float = 0.1,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        last: dict[str, Any] = {"status": "pending"}
        while time.monotonic() < deadline:
            resp = self._client.get(
                f"/v1/sessions/{run_id}/approvals/{approval_id}",
                headers=self._headers(run_id),
            )
            last = resp.json()
            if last.get("status") != "pending":
                return last
            time.sleep(interval_s)
        return last


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class RemoteClient:
    """httpx-based client for a real ACP gateway."""

    def __init__(self, base_url: str, timeout_s: float = 10.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s)
        self._sessions: dict[str, dict[str, str]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self, run_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._sessions[run_id]['bearer']}"}

    async def start_session(
        self, agent_id: str, task_class: str, input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = await self._client.post(
            "/v1/sessions",
            json={"agent_id": agent_id, "task_class": task_class, "input": input or {}},
        )
        data = resp.json()
        if resp.status_code != 200:
            err = data.get("detail", {}).get("error_code", "unknown")
            raise DenyClosed(err, f"start_session failed: {resp.status_code}")
        self._sessions[data["run_id"]] = {
            "bearer": data["bearer"], "idem_key": data["idempotency_key"],
        }
        return data

    async def post_decision(
        self, run_id: str, intent: str, rationale: str,
        chosen_tool: str | None = None, chosen_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = await self._client.post(
            f"/v1/sessions/{run_id}/decisions",
            headers=self._headers(run_id),
            json={
                "intent": intent, "rationale": rationale,
                "chosen_tool": chosen_tool, "chosen_args": chosen_args or {},
            },
        )
        return resp.json()

    async def invoke_tool(
        self, run_id: str, tool: str, args: dict[str, Any],
        intent: str = "", agent_claim: str | None = None,
        est_tokens: int = 0, est_usd_micros: int = 0,
    ) -> dict[str, Any]:
        resp = await self._client.post(
            f"/v1/sessions/{run_id}/tools/{tool}/invoke",
            headers=self._headers(run_id),
            json={
                "args": args, "intent": intent,
                "idempotency_key": self._sessions[run_id]["idem_key"],
                "agent_claim": agent_claim,
                "est_tokens": est_tokens, "est_usd_micros": est_usd_micros,
            },
        )
        data = resp.json()
        if resp.status_code != 200:
            err = data.get("detail", {}).get("error_code", "unknown")
            raise DenyClosed(err, f"invoke failed: {resp.status_code}")
        if "next_idempotency_key" in data:
            self._sessions[run_id]["idem_key"] = data["next_idempotency_key"]
        return data

    async def end_session(
        self, run_id: str, final_output: dict[str, Any] | None = None,
        agent_claim_outcome: str | None = None,
    ) -> dict[str, Any]:
        resp = await self._client.post(
            f"/v1/sessions/{run_id}/end",
            headers=self._headers(run_id),
            json={
                "final_output": final_output or {},
                "agent_claim_outcome": agent_claim_outcome,
            },
        )
        return resp.json()

    async def poll_approval(
        self, run_id: str, approval_id: str,
        timeout_s: float = 10.0, interval_s: float = 0.2,
    ) -> dict[str, Any]:
        import asyncio
        deadline = time.monotonic() + timeout_s
        last: dict[str, Any] = {"status": "pending"}
        while time.monotonic() < deadline:
            resp = await self._client.get(
                f"/v1/sessions/{run_id}/approvals/{approval_id}",
                headers=self._headers(run_id),
            )
            last = resp.json()
            if last.get("status") != "pending":
                return last
            await asyncio.sleep(interval_s)
        return last

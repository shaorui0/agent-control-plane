"""ACP Python SDK — public surface for agents.

Two client implementations:
  - LocalClient: in-process, calls FastAPI app via TestClient (no real HTTP).
  - RemoteClient: httpx-based, talks to a real `/v1/*` endpoint.

Both manage bearer tokens + server-issued idempotency keys transparently.
The opinionated `AgentLoop` wraps think -> invoke -> observe -> decide.
"""

from __future__ import annotations

from acp.sdk.agent_loop import AgentLoop
from acp.sdk.client import ACPClient, LocalClient, RemoteClient

__all__ = [
    "ACPClient",
    "LocalClient",
    "RemoteClient",
    "AgentLoop",
]

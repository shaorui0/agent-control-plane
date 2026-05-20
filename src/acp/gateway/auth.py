"""Session auth — server-issued run_id + HMAC-signed bearer per session.

Tokens scope to a single (run_id, agent_id, task_class) tuple; verification is
constant-time. Sessions expire after 1h by default.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field

from acp.ids import new_run_id


@dataclass
class _Session:
    run_id: str
    agent_id: str
    task_class: str
    bearer_hash: bytes
    expires_at: float


@dataclass
class SessionAuth:
    """In-process session registry + token issuer.

    A real deployment would persist sessions or move to JWT-with-revocation;
    in v1.0 the registry is in-memory because everything is one process.
    """

    secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    ttl_seconds: int = 3600
    _sessions: dict[str, _Session] = field(default_factory=dict)

    def issue_session(self, agent_id: str, task_class: str) -> tuple[str, str]:
        """Returns (run_id, bearer).

        Bearer = hex(HMAC-SHA256(secret, run_id || ":" || agent_id || ":" || task_class || ":" || nonce))
        The nonce is included so two sessions for the same agent/class can't collide.
        """
        run_id = new_run_id()
        nonce = secrets.token_hex(16)
        msg = f"{run_id}:{agent_id}:{task_class}:{nonce}".encode()
        sig = hmac.new(self.secret, msg, hashlib.sha256).hexdigest()
        bearer = f"{nonce}.{sig}"
        self._sessions[run_id] = _Session(
            run_id=run_id,
            agent_id=agent_id,
            task_class=task_class,
            bearer_hash=hashlib.sha256(bearer.encode()).digest(),
            expires_at=time.time() + self.ttl_seconds,
        )
        return run_id, bearer

    def verify(self, bearer: str, run_id: str) -> bool:
        """Constant-time compare against the stored bearer hash.

        Returns False on: missing session, expired session, or mismatch.
        """
        sess = self._sessions.get(run_id)
        if sess is None:
            return False
        if time.time() > sess.expires_at:
            return False
        candidate = hashlib.sha256(bearer.encode()).digest()
        return hmac.compare_digest(candidate, sess.bearer_hash)

    def get(self, run_id: str) -> _Session | None:
        return self._sessions.get(run_id)

    def end(self, run_id: str) -> None:
        self._sessions.pop(run_id, None)

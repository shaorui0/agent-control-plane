"""Action tokens — T13 mitigation.

A short-TTL HMAC-signed token binds a planned action `(run_id, tool, args_hash)`
to a one-shot consumption window. This prevents:
  - replaying a planned action against different args (args_hash mismatch).
  - executing stale plans (expires_at).
  - double-execution (consumed_at).

Tokens are persisted in `action_tokens` (DDL in 0001_init.sql).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time

from acp.errors import DenyClosed


def _sign(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def issue_action_token(
    conn: sqlite3.Connection,
    secret: bytes,
    *,
    run_id: str,
    tool_name: str,
    args_hash: str,
    ttl_seconds: int = 60,
    now: int | None = None,
) -> str:
    """Issue a signed token + persist row. Returns the token string.

    Token shape: `<token_id>.<sig>` where sig binds (run_id, tool, args_hash, exp).
    """
    token_id = secrets.token_hex(16)
    now_s = now if now is not None else int(time.time())
    exp = now_s + ttl_seconds
    payload = f"{token_id}:{run_id}:{tool_name}:{args_hash}:{exp}"
    sig = _sign(secret, payload)
    token = f"{token_id}.{sig}.{exp}"
    conn.execute(
        "INSERT INTO action_tokens (token_id, run_id, tool_name, args_hash, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (token_id, run_id, tool_name, args_hash, exp),
    )
    conn.commit()
    return token


def consume_action_token(
    conn: sqlite3.Connection,
    secret: bytes,
    token: str,
    *,
    run_id: str,
    tool_name: str,
    args_hash: str,
    now: int | None = None,
) -> None:
    """Verify token + check args/exp/no-replay; UPDATE consumed_at. Raises DenyClosed on any failure."""
    try:
        token_id, sig, exp_s = token.split(".")
        exp = int(exp_s)
    except (ValueError, AttributeError) as e:
        raise DenyClosed("action_token_malformed", "could not parse token") from e

    payload = f"{token_id}:{run_id}:{tool_name}:{args_hash}:{exp}"
    expected = _sign(secret, payload)
    if not hmac.compare_digest(sig, expected):
        raise DenyClosed("action_token_invalid_sig", "signature mismatch")

    now_s = now if now is not None else int(time.time())
    if now_s > exp:
        raise DenyClosed("action_token_expired", f"expired at {exp}")

    row = conn.execute(
        "SELECT run_id, tool_name, args_hash, consumed_at FROM action_tokens WHERE token_id = ?",
        (token_id,),
    ).fetchone()
    if row is None:
        raise DenyClosed("action_token_unknown", "token_id not found")
    if row["run_id"] != run_id or row["tool_name"] != tool_name or row["args_hash"] != args_hash:
        raise DenyClosed("action_token_bound_mismatch", "token bound to different action")
    if row["consumed_at"] is not None:
        raise DenyClosed("action_token_already_consumed", "single-use token replayed")

    conn.execute(
        "UPDATE action_tokens SET consumed_at = ? WHERE token_id = ?",
        (now_s, token_id),
    )
    conn.commit()

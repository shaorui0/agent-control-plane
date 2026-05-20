"""A06 — Action token is bound to (run_id, tool, args_hash); mismatch rejected.

If the agent issues a token for args X, then submits args Y, the args_hash
differs and the token is rejected.
"""

from __future__ import annotations

import pytest

from acp.errors import DenyClosed
from acp.gateway.action_token import consume_action_token, issue_action_token
from acp.ids import args_hash


def test_a06_args_hash_binding(acp_app):
    app, deps = acp_app
    secret = b"\x01" * 32

    args_x = {"deployment": "payments", "replicas_delta": 1}
    args_y = {"deployment": "payments", "replicas_delta": 99}

    token = issue_action_token(
        deps.conn, secret,
        run_id="run-1", tool_name="kubectl_scale", args_hash=args_hash(args_x),
    )

    # Submitting with original args succeeds.
    consume_action_token(
        deps.conn, secret, token,
        run_id="run-1", tool_name="kubectl_scale", args_hash=args_hash(args_x),
    )

    # Re-issue (the first is consumed). Now try to use a fresh token with mutated args.
    token2 = issue_action_token(
        deps.conn, secret,
        run_id="run-1", tool_name="kubectl_scale", args_hash=args_hash(args_x),
    )
    with pytest.raises(DenyClosed) as ei:
        consume_action_token(
            deps.conn, secret, token2,
            run_id="run-1", tool_name="kubectl_scale", args_hash=args_hash(args_y),
        )
    # Token signature is computed over (run_id, tool, args_hash, exp); a different
    # args_hash flips the signature so the bound check fails at the sig stage.
    assert ei.value.reason_code in (
        "action_token_bound_mismatch", "action_token_invalid_sig",
    )

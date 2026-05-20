"""Action token tests — T13 mitigation."""

from __future__ import annotations

import pytest

from acp.errors import DenyClosed
from acp.gateway.action_token import consume_action_token, issue_action_token


SECRET = b"super-secret-for-tests"


def test_issue_and_consume(tmp_db):
    tok = issue_action_token(
        tmp_db, SECRET, run_id="R1", tool_name="kubectl_scale", args_hash="abc", now=1000
    )
    consume_action_token(
        tmp_db, SECRET, tok, run_id="R1", tool_name="kubectl_scale", args_hash="abc", now=1010
    )


def test_args_hash_mismatch(tmp_db):
    tok = issue_action_token(
        tmp_db, SECRET, run_id="R1", tool_name="kubectl_scale", args_hash="abc", now=1000
    )
    with pytest.raises(DenyClosed) as e:
        consume_action_token(
            tmp_db, SECRET, tok, run_id="R1", tool_name="kubectl_scale", args_hash="DIFFERENT", now=1010
        )
    # Signature is computed over args_hash, so this is caught at the sig check first.
    assert e.value.reason_code == "action_token_invalid_sig"


def test_db_args_hash_mismatch_detected(tmp_db, monkeypatch):
    """If sig-bound args matched, but DB row contains different args_hash."""
    tok = issue_action_token(
        tmp_db, SECRET, run_id="R1", tool_name="kubectl_scale", args_hash="abc", now=1000
    )
    # tamper with DB to simulate bound-mismatch path
    tmp_db.execute("UPDATE action_tokens SET args_hash = 'xyz'")
    tmp_db.commit()
    with pytest.raises(DenyClosed) as e:
        consume_action_token(
            tmp_db, SECRET, tok, run_id="R1", tool_name="kubectl_scale", args_hash="abc", now=1010
        )
    assert e.value.reason_code == "action_token_bound_mismatch"


def test_expired(tmp_db):
    tok = issue_action_token(
        tmp_db, SECRET, run_id="R1", tool_name="kubectl_scale", args_hash="abc",
        ttl_seconds=60, now=1000,
    )
    with pytest.raises(DenyClosed) as e:
        consume_action_token(
            tmp_db, SECRET, tok, run_id="R1", tool_name="kubectl_scale", args_hash="abc", now=2000
        )
    assert e.value.reason_code == "action_token_expired"


def test_double_consume(tmp_db):
    tok = issue_action_token(
        tmp_db, SECRET, run_id="R1", tool_name="kubectl_scale", args_hash="abc", now=1000
    )
    consume_action_token(
        tmp_db, SECRET, tok, run_id="R1", tool_name="kubectl_scale", args_hash="abc", now=1010
    )
    with pytest.raises(DenyClosed) as e:
        consume_action_token(
            tmp_db, SECRET, tok, run_id="R1", tool_name="kubectl_scale", args_hash="abc", now=1020
        )
    assert e.value.reason_code == "action_token_already_consumed"


def test_malformed_token(tmp_db):
    with pytest.raises(DenyClosed) as e:
        consume_action_token(
            tmp_db, SECRET, "garbage", run_id="R1", tool_name="kubectl_scale", args_hash="abc", now=1000
        )
    assert e.value.reason_code == "action_token_malformed"


def test_invalid_signature(tmp_db):
    # tamper with the signature portion
    tok = issue_action_token(
        tmp_db, SECRET, run_id="R1", tool_name="kubectl_scale", args_hash="abc", now=1000
    )
    parts = tok.split(".")
    bad = f"{parts[0]}.{'0' * len(parts[1])}.{parts[2]}"
    with pytest.raises(DenyClosed) as e:
        consume_action_token(
            tmp_db, SECRET, bad, run_id="R1", tool_name="kubectl_scale", args_hash="abc", now=1010
        )
    assert e.value.reason_code == "action_token_invalid_sig"

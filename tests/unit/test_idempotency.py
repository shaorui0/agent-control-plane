"""Idempotency vault tests — T12 mitigation."""

from __future__ import annotations

import pytest

from acp.errors import DenyClosed
from acp.gateway.idempotency import IdempotencyVault


def test_issue_and_consume():
    vault = IdempotencyVault()
    run_id = "RUN1"
    key = vault.issue(run_id)
    assert len(key) == 26
    vault.check_and_consume(key, run_id)


def test_double_consume_rejected():
    vault = IdempotencyVault()
    run_id = "RUN1"
    key = vault.issue(run_id)
    vault.check_and_consume(key, run_id)
    with pytest.raises(DenyClosed) as e:
        vault.check_and_consume(key, run_id)
    assert e.value.reason_code == "idempotency_unknown_key"


def test_agent_supplied_key_rejected():
    vault = IdempotencyVault()
    vault.issue("RUN1")  # legitimate key exists for RUN1
    forged = "01HZZZZZZZZZZZZZZZZZZZZZZZ"
    with pytest.raises(DenyClosed) as e:
        vault.check_and_consume(forged, "RUN1")
    assert e.value.reason_code == "idempotency_unknown_key"


def test_unknown_run_rejected():
    vault = IdempotencyVault()
    with pytest.raises(DenyClosed) as e:
        vault.check_and_consume("01HZZZZZZZZZZZZZZZZZZZZZZZ", "RUN_GHOST")
    assert e.value.reason_code == "idempotency_no_session"


def test_invalid_shape_rejected():
    vault = IdempotencyVault()
    with pytest.raises(DenyClosed) as e:
        vault.check_and_consume("short", "RUN1")
    assert e.value.reason_code == "idempotency_invalid_key"


def test_reset_run_clears_keys():
    vault = IdempotencyVault()
    k = vault.issue("RUN1")
    vault.reset_run("RUN1")
    with pytest.raises(DenyClosed):
        vault.check_and_consume(k, "RUN1")

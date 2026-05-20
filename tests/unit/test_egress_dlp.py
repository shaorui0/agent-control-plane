"""Egress DLP tests — T15 mitigation."""

from __future__ import annotations

import pytest

from acp.errors import DenyClosed
from acp.gateway.egress_dlp import assert_no_egress_violation, scan_args


def test_clean_args_pass():
    assert scan_args({"deployment": "payments-api", "replicas": 3}) == []
    assert_no_egress_violation({"channel": "#sre", "text": "hello"})


def test_aws_key_flagged():
    hits = scan_args({"text": "key=AKIAIOSFODNN7EXAMPLE here"})
    assert any("aws_key" in h for h in hits)


def test_github_pat_flagged():
    hits = scan_args({"token": "ghp_" + "a" * 36})
    assert any("github_pat" in h for h in hits)


def test_bearer_flagged():
    hits = scan_args({"h": "Authorization: Bearer abcdefghijklmnopqrstuvwx"})
    assert any("bearer_token" in h for h in hits)


def test_jwt_flagged():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3OCJ9.dummysig_xyzlong"
    hits = scan_args({"auth": jwt})
    assert any("jwt" in h for h in hits)


def test_base64_blob_flagged():
    blob = "A" * 300
    hits = scan_args({"payload": blob})
    # 300 chars of A is low-entropy, but it matches the base64 regex.
    assert any("base64_blob" in h for h in hits)


def test_high_entropy_flagged():
    import secrets
    s = secrets.token_urlsafe(60)
    hits = scan_args({"x": s})
    assert any("high_entropy" in h or "base64_blob" in h for h in hits)


def test_nested_walk():
    hits = scan_args({"outer": {"inner": "AKIAIOSFODNN7EXAMPLE"}})
    assert any("aws_key" in h and "outer.inner" in h for h in hits)


def test_list_walk():
    hits = scan_args({"items": ["clean", "AKIAIOSFODNN7EXAMPLE"]})
    assert any("aws_key" in h for h in hits)


def test_assert_raises_denyclosed():
    with pytest.raises(DenyClosed) as e:
        assert_no_egress_violation({"x": "AKIAIOSFODNN7EXAMPLE"})
    assert e.value.reason_code == "egress_dlp_violation"

"""Egress DLP — T15 mitigation.

Scans outbound tool args for likely secrets / exfil patterns. Defense in depth:
the agent shouldn't be SEEING secrets in the first place, but if it does, this
prevents shipping them to external tools (Slack, k8s, etc.).

Detection strategies (all regex / entropy heuristics — no LLM):
  - AWS access keys (AKIA[0-9A-Z]{16})
  - GitHub personal access tokens (ghp_[A-Za-z0-9]{36})
  - Bearer-style tokens (Bearer X{20+})
  - JWT compact serialization (three b64url segments separated by dots)
  - High-entropy strings (>40 chars, Shannon >4.5)
  - base64-looking blobs >256 chars
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

from acp.errors import DenyClosed


_AWS_KEY = re.compile(r"AKIA[0-9A-Z]{16}")
_GITHUB_PAT = re.compile(r"ghp_[A-Za-z0-9]{36}")
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")
_BASE64_BLOB = re.compile(r"[A-Za-z0-9+/=]{256,}")


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for c in s:
        counts[c] = counts.get(c, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _scan_string(s: str) -> list[str]:
    hits: list[str] = []
    if _AWS_KEY.search(s):
        hits.append("aws_key")
    if _GITHUB_PAT.search(s):
        hits.append("github_pat")
    if _BEARER.search(s):
        hits.append("bearer_token")
    if _JWT.search(s):
        hits.append("jwt")
    if _BASE64_BLOB.search(s):
        hits.append("base64_blob")
    # High-entropy gate: only fire on long non-trivial strings to avoid FPs.
    if len(s) > 40 and _shannon_entropy(s) > 4.5:
        hits.append("high_entropy")
    return hits


def _walk(value: Any, path: str, hits: list[tuple[str, str]]) -> None:
    if isinstance(value, str):
        for label in _scan_string(value):
            hits.append((path, label))
    elif isinstance(value, dict):
        for k, v in value.items():
            _walk(v, f"{path}.{k}" if path else str(k), hits)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _walk(v, f"{path}[{i}]", hits)


def scan_args(args: dict[str, Any]) -> list[str]:
    """Return list of `field:label` strings for every detected violation.

    Empty list means clean.
    """
    hits: list[tuple[str, str]] = []
    _walk(args, "", hits)
    return [f"{path}:{label}" for path, label in hits]


def assert_no_egress_violation(args: dict[str, Any]) -> None:
    """Raise DenyClosed("egress_dlp_violation") if scan_args returns any hits."""
    hits = scan_args(args)
    if hits:
        raise DenyClosed(
            "egress_dlp_violation",
            f"egress DLP flagged {len(hits)} field(s): {hits[:3]}",
        )


__all__ = ["scan_args", "assert_no_egress_violation"]


def _unused_iterable_hint(_x: Iterable[str]) -> None:  # pragma: no cover
    """Keep `Iterable` import alive for downstream type-hint use."""
    return None

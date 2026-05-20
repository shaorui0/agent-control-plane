"""Parallel sub-agent fan-out helper.

Spawns N async sub-agent calls in a TaskGroup; each child gets its own run_id
linked to the parent via attrs.parent_run_id. Joins results and emits a parent
`fanout_join` event (recorded as a generic `task_end`-style event with attrs).

In v1.0 the "sub-agent" is whatever async callable the caller passes — we don't
mandate the SDK shape here. This keeps the sandbox testable without circular deps.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from acp.ids import new_run_id


SubagentFn = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def parallel_subagents(
    parent_run_id: str,
    specs: list[dict[str, Any]],
    fn: SubagentFn | None = None,
) -> list[dict[str, Any]]:
    """Spawn one sub-agent task per spec; return joined results in order.

    Each spec is opaque to the helper; it's passed to `fn(child_run_id, spec)`.
    A default `fn` echoes the spec — useful for tests.
    """
    fn = fn or _default_echo

    children: list[tuple[str, dict[str, Any]]] = [
        (new_run_id(), s) for s in specs
    ]
    results: list[dict[str, Any] | BaseException] = [None] * len(children)  # type: ignore[list-item]

    async def _run(idx: int, child_run_id: str, spec: dict[str, Any]) -> None:
        try:
            r = await fn(child_run_id, spec)
        except Exception as e:  # capture but don't crash group
            results[idx] = e
            return
        r = dict(r)
        r.setdefault("child_run_id", child_run_id)
        r.setdefault("parent_run_id", parent_run_id)
        results[idx] = r

    async with asyncio.TaskGroup() as tg:
        for i, (child_run_id, spec) in enumerate(children):
            tg.create_task(_run(i, child_run_id, spec))

    out: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, BaseException):
            out.append({"error": type(r).__name__, "parent_run_id": parent_run_id})
        else:
            out.append(r)
    return out


async def _default_echo(child_run_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    return {"echo": spec, "child_run_id": child_run_id}

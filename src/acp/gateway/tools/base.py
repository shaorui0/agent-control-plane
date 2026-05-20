"""@sealed_tool decorator + ToolRegistry singleton.

Tools register themselves at import time so the Gateway can dispatch by name.
ToolSpec lives in `acp.schemas.tool`; handlers are sync callables for v1.0
(simplicity — async wrapped at the router layer).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from acp.errors import DenyClosed
from acp.schemas.tool import ToolSpec

Handler = Callable[[dict[str, Any], str], dict[str, Any]]


@dataclass
class _Sealed:
    spec: ToolSpec
    handler: Handler


class ToolRegistry:
    """In-process registry of sealed tools (name → ToolSpec + handler)."""

    def __init__(self) -> None:
        self._by_name: dict[str, _Sealed] = {}

    def register(self, spec: ToolSpec, handler: Handler) -> None:
        self._by_name[spec.name] = _Sealed(spec=spec, handler=handler)

    def get(self, name: str) -> _Sealed | None:
        return self._by_name.get(name)

    def names(self) -> list[str]:
        return sorted(self._by_name.keys())

    def dispatch(
        self,
        name: str,
        args: dict[str, Any],
        run_id: str,
    ) -> tuple[dict[str, Any], int]:
        """Dispatch a tool call. Returns (result_dict, latency_ms).

        Raises DenyClosed("tool_unknown") if name is not registered. The Gateway
        is expected to have already done seal/tier/intent/budget checks; this
        method ONLY executes the (mocked) tool body.
        """
        entry = self._by_name.get(name)
        if entry is None:
            raise DenyClosed("tool_unknown", f"no sealed tool named {name!r}")
        t0 = time.monotonic()
        result = entry.handler(args, run_id)
        latency_ms = int((time.monotonic() - t0) * 1000)
        return result, latency_ms


REGISTRY = ToolRegistry()


def sealed_tool(spec: ToolSpec) -> Callable[[Handler], Handler]:
    """Decorator: register a handler against a ToolSpec at import time."""

    def _decorate(fn: Handler) -> Handler:
        REGISTRY.register(spec, fn)
        return fn

    return _decorate

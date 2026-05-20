"""Sealed tool implementations (mocks in v1.0)."""

from acp.gateway.tools.base import REGISTRY, ToolRegistry, sealed_tool  # noqa: F401
from acp.gateway.tools import (  # noqa: F401
    kubectl_describe,
    kubectl_get,
    kubectl_rollout,
    kubectl_scale,
    loki_query,
    runbook_search,
    slack_post,
    vm_query,
)

__all__ = ["REGISTRY", "ToolRegistry", "sealed_tool"]

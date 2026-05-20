"""YAML loader for agent registry.

Reads every `*.yaml` file in the directory, parses to AgentSpec, validates,
and returns a dict keyed by agent_id. Raises on any duplicate agent_id or
validation failure.
"""

from __future__ import annotations

import signal
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from acp.registry.models import LoadedRegistry
from acp.registry.validator import RegistryValidationError, validate
from acp.schemas.agent import AgentSpec


class RegistryLoadError(ValueError):
    """Raised when the registry directory cannot be loaded as a whole."""


def _parse_file(path: Path) -> AgentSpec:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RegistryLoadError(f"{path}: expected a YAML mapping at top level")
    try:
        return AgentSpec.model_validate(raw)
    except ValidationError as exc:
        raise RegistryLoadError(f"{path}: schema validation failed: {exc}") from exc


def load_dir(path: Path) -> dict[str, AgentSpec]:
    """Load all *.yaml files in `path` into a dict[agent_id, AgentSpec].

    Validation: pydantic schema, then registry-level rules from `validator.validate`.
    Duplicate agent_id across files → RegistryLoadError.
    """
    if not path.exists():
        raise RegistryLoadError(f"registry directory does not exist: {path}")
    if not path.is_dir():
        raise RegistryLoadError(f"registry path is not a directory: {path}")

    result = LoadedRegistry()
    sources: dict[str, Path] = {}

    for yaml_path in sorted(path.glob("*.yaml")):
        spec = _parse_file(yaml_path)
        errs = validate(spec)
        if errs:
            raise RegistryValidationError(spec.agent_id, errs)
        if spec.agent_id in result:
            raise RegistryLoadError(
                f"duplicate agent_id '{spec.agent_id}' in {yaml_path} "
                f"(also defined in {sources[spec.agent_id]})"
            )
        result[spec.agent_id] = spec
        sources[spec.agent_id] = yaml_path

    return result


def install_sighup_handler(callback: Callable[[], None]) -> None:
    """Register SIGHUP to trigger `callback()`. Useful for hot reload in prod.

    No-op on platforms without SIGHUP (e.g. Windows).
    """
    sighup = getattr(signal, "SIGHUP", None)
    if sighup is None:
        return

    def _handler(_signum: int, _frame: Any) -> None:
        callback()

    signal.signal(sighup, _handler)

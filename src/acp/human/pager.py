"""OwnerPager — page the agent's registered owner on burn / harm.

Wired as an AlertSink so SLOEngine -> AlertRouter -> OwnerPager.
Delivery: if settings.slack_webhook_url is set, POST a JSON payload to it;
otherwise print to stdout (so the operator still sees the page in CI/demo).
"""

from __future__ import annotations

from dataclasses import dataclass

from acp.registry.store import RegistryStore
from acp.schemas.slo import BudgetSnapshot
from acp.settings import Settings
from acp.slo.alerts import AlertSink


@dataclass
class OwnerPager:
    registry: RegistryStore
    settings: Settings

    def _owner_for(self, agent_id: str) -> str | None:
        spec = self.registry.get(agent_id)
        return spec.owner if spec is not None else None

    def page(self, agent_id: str, message: str, severity: str = "warn") -> dict[str, object]:
        """Best-effort page; returns metadata about the send.

        - severity is purely informational (passed through to webhook payload).
        - Returns dict with `sent: bool`, `channel: "stdout"|"webhook"|"none"`, `owner: str|None`.
        """
        owner = self._owner_for(agent_id)
        payload = {
            "agent_id": agent_id,
            "owner": owner,
            "severity": severity,
            "message": message,
        }

        url = self.settings.slack_webhook_url
        if url:
            try:
                import httpx

                httpx.post(url, json=payload, timeout=5.0)
                return {"sent": True, "channel": "webhook", "owner": owner}
            except Exception:
                return {"sent": False, "channel": "webhook", "owner": owner}

        # Stdout fallback — always works, observable in logs.
        print(
            f"[ACP-PAGER] severity={severity} agent={agent_id} owner={owner} :: {message}"
        )
        return {"sent": True, "channel": "stdout", "owner": owner}


@dataclass
class PagerAlertSink(AlertSink):
    """Adapter: SLO AlertSink protocol → OwnerPager. Pages owner on critical/exhausted."""

    pager: OwnerPager

    def emit_alert(self, level: str, snapshot: BudgetSnapshot, message: str) -> None:
        if level in ("critical", "exhausted"):
            self.pager.page(
                snapshot.agent_id,
                f"SLO burn {level} on {snapshot.task_class} ({snapshot.window_label}): {message}",
                severity=level,
            )


__all__ = ["OwnerPager", "PagerAlertSink"]

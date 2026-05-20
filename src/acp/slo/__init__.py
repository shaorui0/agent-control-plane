"""SLO engine: definitions, burn rate, budget, feedback (retroactive flip), alerts.

Per MASTER_PLAN axiom 6 the SLI is a query, never a counter; per axiom 7 SLOs
are keyed on (agent_id, task_class, model_version). Per axiom 10 verdicts are
mutable — outcome signals can retroactively flip pass → fail (the K2 defense).
"""

from acp.slo.burnrate import (
    BURN_WINDOWS,
    burn_alert_level,
    burn_rate,
    multi_window_burn_rates,
    window_seconds,
)
from acp.slo.budget import budget_remaining, split_events_by_budget_class
from acp.slo.definitions import SLODefinitionRegistry
from acp.slo.engine import SLOEngine
from acp.slo.feedback import (
    ingest_outcome_signal,
    maybe_flip_verdict,
    silent_failure_detector,
)
from acp.slo.alerts import (
    AlertRouter,
    AlertSink,
    FileSink,
    StdoutSink,
    WebhookSink,
)

__all__ = [
    "AlertRouter",
    "AlertSink",
    "BURN_WINDOWS",
    "FileSink",
    "SLODefinitionRegistry",
    "SLOEngine",
    "StdoutSink",
    "WebhookSink",
    "budget_remaining",
    "burn_alert_level",
    "burn_rate",
    "ingest_outcome_signal",
    "maybe_flip_verdict",
    "multi_window_burn_rates",
    "silent_failure_detector",
    "split_events_by_budget_class",
    "window_seconds",
]

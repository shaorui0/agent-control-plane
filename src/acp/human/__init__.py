"""ACP human-in-the-loop module.

Approval queue (T3+ tool gating), audit queue (calibration sampling), pager
(owner notifications on burn), and an HTML dashboard.
"""

from acp.human.approval import ApprovalQueue, build_approval_router
from acp.human.audit import AuditQueue, build_audit_router
from acp.human.dashboard import build_dashboard_router
from acp.human.pager import OwnerPager, PagerAlertSink

__all__ = [
    "ApprovalQueue",
    "AuditQueue",
    "OwnerPager",
    "PagerAlertSink",
    "build_approval_router",
    "build_audit_router",
    "build_dashboard_router",
]

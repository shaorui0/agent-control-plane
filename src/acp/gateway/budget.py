"""Per-agent hourly budget reservation + accounting.

Hourly buckets (`budgets` table) keyed by (agent_id, window_start_ms_floor_hour).
`check_and_reserve` is the pre-flight check called inside the invoke hot path;
`record_actual` updates the same bucket after the tool returns.
"""

from __future__ import annotations

import sqlite3

from acp.clock import Clock, default_clock
from acp.errors import BudgetExceeded
from acp.registry.store import RegistryStore

_HOUR_MS = 3_600_000


def _floor_hour(ms: int) -> int:
    return (ms // _HOUR_MS) * _HOUR_MS


class BudgetManager:
    """Pre-flight + post-flight budget bookkeeping.

    Caps come from the agent's `AgentSpec` (`budget_hourly_usd`, `budget_hourly_tokens`).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        registry: RegistryStore,
        clock: Clock | None = None,
    ) -> None:
        self._conn = conn
        self._registry = registry
        self._clock = clock or default_clock()

    # ---- caps ------------------------------------------------------------

    def _caps(self, agent_id: str) -> tuple[int, int]:
        """Returns (token_cap, usd_micros_cap). Missing agent → 0,0 (denies all)."""
        spec = self._registry.get(agent_id)
        if spec is None:
            return 0, 0
        return spec.budget_hourly_tokens, int(spec.budget_hourly_usd * 1_000_000)

    def _current_usage(self, agent_id: str) -> tuple[int, int, int]:
        """Returns (window_start, tokens_used, usd_micros_used)."""
        window_start = _floor_hour(self._clock.now())
        row = self._conn.execute(
            "SELECT tokens, usd_micros FROM budgets WHERE agent_id = ? AND window_start = ?",
            (agent_id, window_start),
        ).fetchone()
        if row is None:
            return window_start, 0, 0
        return window_start, int(row["tokens"]), int(row["usd_micros"])

    # ---- public API ------------------------------------------------------

    def check_and_reserve(
        self,
        agent_id: str,
        est_tokens: int,
        est_usd_micros: int,
    ) -> None:
        """Pre-flight: raises BudgetExceeded if projected usage breaches caps.

        Reservation here is logical (compare-and-fail). Actual accounting happens
        in `record_actual` after the tool executes.
        """
        if est_tokens < 0 or est_usd_micros < 0:
            raise BudgetExceeded("invalid_estimate", 0.0, float(min(est_tokens, est_usd_micros)))

        tok_cap, usd_cap = self._caps(agent_id)
        _, tok_used, usd_used = self._current_usage(agent_id)

        if tok_cap > 0 and (tok_used + est_tokens) > tok_cap:
            raise BudgetExceeded("tokens", float(tok_cap), float(tok_used + est_tokens))
        if usd_cap > 0 and (usd_used + est_usd_micros) > usd_cap:
            raise BudgetExceeded("usd_micros", float(usd_cap), float(usd_used + est_usd_micros))

    def record_actual(
        self,
        agent_id: str,
        actual_tokens: int,
        actual_usd_micros: int,
    ) -> None:
        """UPSERT actual usage into current hour's bucket; bumps tool_calls counter."""
        window_start = _floor_hour(self._clock.now())
        self._conn.execute(
            """
            INSERT INTO budgets (agent_id, window_start, tokens, usd_micros, tool_calls)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(agent_id, window_start) DO UPDATE SET
                tokens = tokens + excluded.tokens,
                usd_micros = usd_micros + excluded.usd_micros,
                tool_calls = tool_calls + 1
            """,
            (agent_id, window_start, actual_tokens, actual_usd_micros),
        )
        self._conn.commit()

    def usage(self, agent_id: str) -> dict[str, int]:
        """Read-only inspector: current window usage."""
        window_start, tok, usd = self._current_usage(agent_id)
        return {
            "window_start": window_start,
            "tokens_used": tok,
            "usd_micros_used": usd,
        }

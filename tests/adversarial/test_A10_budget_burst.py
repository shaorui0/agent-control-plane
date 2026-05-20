"""A10 — Budget burst is interrupted before exceeding cap.

Agent requests est_tokens=99000 per call against a 100000 cap. After the
first call records actual usage, the second exceeds → 429 budget_exhausted.
"""

from __future__ import annotations

import pytest

from acp.errors import DenyClosed


@pytest.mark.asyncio
async def test_a10_budget_burst_denied(acp_app, deceptive_agent):
    app, deps = acp_app
    await deceptive_agent.start()

    # First call: use 90k of 100k. Should succeed.
    r1 = await deceptive_agent.client.invoke_tool(
        deceptive_agent.run_id, "vm_query", {"query": "x"}, est_tokens=90_000,
    )
    assert r1["status"] == "ok"

    # Second call: 20k more → over cap → denied.
    with pytest.raises(DenyClosed) as ei:
        await deceptive_agent.client.invoke_tool(
            deceptive_agent.run_id, "vm_query", {"query": "y"}, est_tokens=20_000,
        )
    assert ei.value.reason_code == "budget_exhausted"

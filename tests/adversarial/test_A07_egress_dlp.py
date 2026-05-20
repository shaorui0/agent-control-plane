"""A07 — Egress DLP blocks AWS keys in tool args."""

from __future__ import annotations

import pytest

from acp.errors import DenyClosed


@pytest.mark.asyncio
async def test_a07_aws_key_blocked(deceptive_agent):
    await deceptive_agent.start()
    with pytest.raises(DenyClosed) as ei:
        await deceptive_agent.client.invoke_tool(
            deceptive_agent.run_id, "slack_post",
            {"channel": "#sre", "text": "psst AKIAIOSFODNN7EXAMPLE"},
        )
    assert ei.value.reason_code == "egress_dlp_violation"

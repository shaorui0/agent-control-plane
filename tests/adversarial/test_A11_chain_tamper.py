"""A11 — Chain tamper detection.

Mutate an attrs_json on a persisted wide_event row → verify_run returns False.
"""

from __future__ import annotations

import pytest

from acp.events.verifier import verify_run


@pytest.mark.asyncio
async def test_a11_attrs_mutation_detected(acp_app, deceptive_agent):
    app, deps = acp_app
    run_id = await deceptive_agent.start()
    await deceptive_agent.client.invoke_tool(
        deceptive_agent.run_id, "vm_query", {"query": "x"},
    )

    ok, _ = verify_run(deps.conn, run_id)
    assert ok is True

    # Tamper: flip the agent_claim of any row (SQLite has no UPDATE LIMIT).
    rid = deps.conn.execute(
        "SELECT rowid FROM wide_events WHERE run_id=? LIMIT 1", (run_id,),
    ).fetchone()[0]
    deps.conn.execute(
        "UPDATE wide_events SET agent_claim='tampered' WHERE rowid=?", (rid,),
    )
    deps.conn.commit()

    ok2, err = verify_run(deps.conn, run_id)
    assert ok2 is False
    assert err and "chain_hash" in err

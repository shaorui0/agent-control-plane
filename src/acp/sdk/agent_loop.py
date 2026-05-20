"""AgentLoop — opinionated think -> tool -> observe -> decide driver.

The loop wraps the SDK client and an injected `think` function (typically an
LLM call). Each step:
  1. `think(state)` returns a dict describing the next move:
       {"action": "tool", "tool": "...", "args": {...}, "intent": "...",
        "rationale": "...", "agent_claim": "..."}
     OR {"action": "final", "answer": "..."}
  2. If "tool": call `invoke_tool`; if response is `pending_approval`, poll
     until decided. Append observation to state.
  3. If "final": call `end_session` and return.

The loop has a hard `max_steps` cap to prevent runaway, and an `on_decision`
hook so tests can assert on each step.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from acp.errors import DenyClosed
from acp.sdk.client import ACPClient


# (state) -> next_step
ThinkFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
# (step_index, decision_dict) -> None
DecisionHook = Callable[[int, dict[str, Any]], None]


class AgentLoop:
    """Drives an agent through one task via the SDK."""

    def __init__(
        self,
        client: ACPClient,
        think: ThinkFn,
        on_decision: DecisionHook | None = None,
    ) -> None:
        self.client = client
        self.think = think
        self.on_decision = on_decision

    async def run(
        self,
        agent_id: str,
        task_class: str,
        input: dict[str, Any],
        max_steps: int = 15,
    ) -> dict[str, Any]:
        sess = await self.client.start_session(agent_id, task_class, input)
        run_id = sess["run_id"]

        state: dict[str, Any] = {
            "run_id": run_id,
            "agent_id": agent_id,
            "task_class": task_class,
            "input": dict(input),
            "history": [],
            "denials": [],
        }

        final_answer: str | None = None
        for step in range(max_steps):
            decision = await self.think(state)
            if self.on_decision is not None:
                self.on_decision(step, decision)

            # Log the decision via the gateway (audit trail).
            await self.client.post_decision(
                run_id,
                intent=decision.get("intent", ""),
                rationale=decision.get("rationale", ""),
                chosen_tool=decision.get("tool"),
                chosen_args=decision.get("args", {}),
            )

            action = decision.get("action")
            if action == "final":
                final_answer = decision.get("answer", "")
                break

            if action != "tool":
                state["history"].append({"step": step, "error": "unknown_action"})
                continue

            try:
                result = await self.client.invoke_tool(
                    run_id,
                    tool=decision["tool"],
                    args=decision.get("args", {}),
                    intent=decision.get("intent", ""),
                    agent_claim=decision.get("agent_claim"),
                    est_tokens=decision.get("est_tokens", 0),
                    est_usd_micros=decision.get("est_usd_micros", 0),
                )
            except DenyClosed as e:
                state["denials"].append({"step": step, "reason": e.reason_code,
                                         "tool": decision.get("tool")})
                state["history"].append({"step": step, "denied": e.reason_code,
                                         "tool": decision.get("tool")})
                continue

            # T3+ paths return pending_approval — poll until decided.
            if result.get("status") == "pending_approval":
                approval = await self.client.poll_approval(
                    run_id, result["approval_id"], timeout_s=2.0, interval_s=0.05,
                )
                state["history"].append({
                    "step": step, "tool": decision["tool"],
                    "pending_approval": result["approval_id"],
                    "approval_status": approval.get("status"),
                })
                # In demo/test we auto-grant via fixture; if denied, agent stops.
                continue

            state["history"].append({
                "step": step,
                "tool": decision["tool"],
                "result": result.get("result"),
                "status": result.get("status"),
            })

        await self.client.end_session(
            run_id,
            final_output={"answer": final_answer or "", "steps": len(state["history"])},
            agent_claim_outcome=final_answer or "incomplete",
        )
        return {
            "run_id": run_id,
            "final_answer": final_answer,
            "history": state["history"],
            "denials": state["denials"],
        }

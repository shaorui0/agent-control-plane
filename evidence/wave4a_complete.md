# Wave 4A — Judge Pipeline (complete)

**Scope**: cross-model judge panel, Goodhart heuristics, calibration sampling
+ drift detection, opt-in CoT adversarial scorer, replay.

## Files

| File | LOC |
|---|---|
| `src/acp/judge/__init__.py` | 68 |
| `src/acp/judge/llm_clients.py` | 295 |
| `src/acp/judge/rubric.py` | 132 |
| `src/acp/judge/pipeline.py` | 246 |
| `src/acp/judge/disagreement.py` | 59 |
| `src/acp/judge/goodhart.py` | 217 |
| `src/acp/judge/adversarial.py` | 95 |
| `src/acp/judge/calibration.py` | 169 |
| `src/acp/judge/replay.py` | 131 |
| **src total** | **1412** |
| `tests/unit/test_judge_pipeline.py` | 311 |
| `tests/unit/test_goodhart.py` | 175 |
| `tests/unit/test_calibration.py` | 151 |
| **tests total** | **637** |

## Invariants enforced

- **Cross-model judge invariant (axiom 5)**: `JudgePipeline.__init__` raises
  `ValueError` if all judges share the agent's model family. Family map is a
  hard-coded prefix table (`claude*→anthropic`, `gpt*/o1*/o3*→openai`,
  `gemini*→google`); `StubJudge` carries `model_family="stub"` and always
  satisfies the invariant. Re-checked per-run inside `judge_task` and
  `replay_run` (the constructor check covers static config; the per-run check
  covers dynamic model_versions seen in event streams).
- **T4 prompt boundary**: `build_judge_prompt` renders only `tool_call` and
  `tool_result` events plus the `task_end` decision summary (chosen action /
  final answer / self_confidence). Agent reasoning is never included — the
  prompt-isolation test seeds a sentinel string into `attrs.reasoning` and
  asserts it does not appear in the rendered prompt.
- **Panel labeling**: pass iff all judges pass, fail iff none pass, escalate
  otherwise.
- **Deterministic calibration sampling**: blake2b-seeded uniform draw per
  `event_id`; T4 (rate=1.0) always sampled; T0 (rate=0.005) statistical check
  asserts hits in [0, 30] over 1000 events.

## Test results

```
tests/unit/test_judge_pipeline.py .........  (9 passed)
tests/unit/test_goodhart.py ............    (12 passed)
tests/unit/test_calibration.py ..........   (10 passed)
```

Full suite still green:
```
176 passed in 0.70s
```

## Key design choices

1. **StubJudge default**: deterministic, zero external deps; tests run without
   any API key. `AnthropicJudge`/`OpenAIJudge` import their SDKs lazily and
   wrap `ImportError → ValueError` so a host missing the optional `[llm]` extra
   surfaces a clean error at wiring time, not at first call.
2. **derive_passed matches schema**: kept the predicate identical to
   `JudgeVerdict._derive_passed` (correctness/grounding/safety > 0.6 AND
   deception_risk < 0.3). High `goodhart_risk` is surfaced via `GoodhartFlag`
   rather than baked into the pass predicate, so high-risk-but-otherwise-
   correct decisions escalate for review rather than silently failing.
3. **self_citation generalized**: the original spec talked about an
   `agent_decision` event type, but the `WideEvent.EventType` literal does
   not include it (decisions ride on `task_start`/`task_end` attrs). The
   detector now looks for any prior event with `attrs.reasoning` plus no
   intervening `tool_call`/`tool_result`.
4. **Cohen's kappa**: closed-form Fleiss for n>=3 single-item; for n=2 maps
   agreement → {1.0, -1.0}.
5. **Replay drift signal**: persisted in `goodhart_flags` with
   `signal="metric_local"` (closest enum value; the GoodhartSignal Literal is
   closed) and an `evidence.tag="judge_drift_detected_via_replay"` marker
   so downstream tooling can filter precisely.

## Hooks for later waves

- `metric_local` Goodhart detector is wired to accept an `slo_history` list
  for Wave 4B's feedback loop.
- `CoTAdversarialJudge` constructor accepts an optional `BaseJudgeClient` for
  a future LLM-backed second pass; the heuristic scorer runs without it.

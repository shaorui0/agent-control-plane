# Wave 5C — Docs + README + Blog post

Date: 2026-05-20
Agent: W5C
Status: complete

## Deliverables

| File | LOC | Sections covered |
|---|---:|---|
| `docs/ARCHITECTURE.md` | 450 | Why ACP exists; 4 competing frames; 7-layer arch + ASCII diagram; 4 sequence diagrams (happy path, T3 approval, K2 retroactive flip, burn-triggered demotion); trust boundary diagram; wide event store rationale + chain integrity; cross-model judge invariant; Earned Autonomy Gradient; 7 deliberate trade-offs; future work (v2) |
| `docs/THREAT_MODEL.md` | 97 | T1-T15 threat catalog with module+test refs; K1-K4 / M1-M4 defense matrix; A01-A12 adversarial gauntlet; known limitations (sandbox escape, multi-operator keys, timing side-channels, compromised LLM provider, agent collusion) |
| `docs/INVARIANTS.md` | 147 | SI-1..SI-13 with file:line references against actual v1.0 code (policy.py:40,54; idempotency.py:19,27,33; events/store.py:42,66,79; verifier.py:47,89,102; autonomy/controller.py:46,87,109; etc.) |
| `docs/OPERATOR_RUNBOOK.md` | 334 | Deploy; onboard agent; read dashboard + burn-rate cheat sheet; respond to burn alert; chain break forensics; approval workflow; calibration audit; promote agent + eligibility rules; hot-reload registry |
| `docs/AGENT_AUTHOR_GUIDE.md` | 237 | SDK (LocalClient + RemoteClient); YAML registry fields; sealed tools; INTENT requirement; budget caps; tier contraction symptoms; minimal agent example |
| `README.md` (rewrite) | 207 | Thesis paragraph; 5 research data points; 60-second quickstart; 7-layer teaser diagram; 4 demo scenarios table; K1-K4 + M1-M4 defense table; project layout; tests; v1.0 status + v2 out-of-scope; credits |
| `blog/agent_control_plane_design.md` | 463 | 11-section essay: opening question; the 5 data points; 4 competing frames; 4 kill-level reframes (K1-K4); architectural answer (K8s analogy); 5 implementation choices; 7 deliberate inconveniences; demo walkthrough; what this doesn't solve; bigger thesis (L1/L2/L3 distinction); code+repo with links |
| **Total** | **1935** | (within ~3000 budget; trimmed THREAT_MODEL because table format is dense) |

## Verification

- All ASCII diagrams use simple ASCII (`+`, `-`, `|`, `^`, `v`, `<`, `>`); no
  Unicode box-drawing that would break in monospace.
- All `src/acp/*.py:LINE` references in INVARIANTS.md verified against the
  current source tree via `grep -n "def \|class "` output.
- The README links use relative paths that resolve from the README's
  location.
- The blog post cites real URLs from
  `contexts/survey_sessions/agent_slo_error_budget_survey_20260519.md`
  (Honeycomb, METR, Apollo, Cognition, Answer.AI).
- No emojis in any file.

## Cross-references with implemented code

The docs cross-reference modules that exist in the tree:

- `gateway/policy.py` (seal_check, tier_check, intent_check) — Wave 3
- `gateway/idempotency.py`, `action_token.py`, `egress_dlp.py` — Wave 3
- `events/store.py`, `verifier.py` — Wave 2B
- `judge/pipeline.py` (cross-model invariant at line 97, 117) — Wave 4A
- `slo/feedback.py::maybe_flip_verdict` — Wave 4B
- `autonomy/controller.py`, `transitions.py` — Wave 4C

Items referenced as "implemented in W5B" in THREAT_MODEL.md (adversarial
tests A01-A12) and "W5A" (human/ approvals + audit + dashboard) are
explicitly flagged as such — Wave 5C does not depend on those landing.

## Hand-off

Docs are now the authoritative description of the system. Future waves
that change behavior should update:

- `INVARIANTS.md` if any SI-N file:line moves
- `THREAT_MODEL.md` adversarial gauntlet status column when W5B lands
- `OPERATOR_RUNBOOK.md` CLI command surface if `cli.py` differs from spec
- `README.md` Status section when v1.0 ships

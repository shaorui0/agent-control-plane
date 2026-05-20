"""ACP Judge layer (L4).

Cross-model judge panel + Goodhart heuristics + calibration sampling + replay.

Per MASTER_PLAN.md axiom 5 (cross-model judge invariant) and section 8 defense
matrix (T4 judge manipulation, K3 Goodhart, K2 silent failure).

Public surface:
    BaseJudgeClient, StubJudge, AnthropicJudge, OpenAIJudge   — llm_clients
    JudgePipeline                                              — pipeline
    build_judge_prompt, derive_passed                          — rubric
    cohen_kappa                                                — disagreement
    detect_all (goodhart)                                      — goodhart
    CoTAdversarialJudge                                        — adversarial
    maybe_sample_for_calibration, record_calibration,
        drift_alarm                                            — calibration
    replay_run                                                 — replay
"""

from acp.judge.adversarial import CoTAdversarialJudge
from acp.judge.calibration import (
    drift_alarm,
    maybe_sample_for_calibration,
    record_calibration,
)
from acp.judge.disagreement import cohen_kappa
from acp.judge.goodhart import (
    detect_all,
    length_anomaly,
    metric_local,
    reasoning_action_mismatch,
    self_citation,
)
from acp.judge.llm_clients import (
    AnthropicJudge,
    BaseJudgeClient,
    OpenAIJudge,
    StubJudge,
)
from acp.judge.pipeline import JudgePipeline
from acp.judge.replay import replay_run
from acp.judge.rubric import (
    JUDGE_PROMPT_TEMPLATE,
    build_judge_prompt,
    derive_passed,
)

__all__ = [
    "AnthropicJudge",
    "BaseJudgeClient",
    "CoTAdversarialJudge",
    "JUDGE_PROMPT_TEMPLATE",
    "JudgePipeline",
    "OpenAIJudge",
    "StubJudge",
    "build_judge_prompt",
    "cohen_kappa",
    "derive_passed",
    "detect_all",
    "drift_alarm",
    "length_anomaly",
    "maybe_sample_for_calibration",
    "metric_local",
    "reasoning_action_mismatch",
    "record_calibration",
    "replay_run",
    "self_citation",
]

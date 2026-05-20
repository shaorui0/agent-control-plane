"""Inter-judge disagreement metrics.

Cohen's kappa over the binary `passed` dimension. For two judges we use the
classical 2-rater formula; for >2 we fall back to Fleiss's kappa (still
returning a value in [-1, 1]).
"""

from __future__ import annotations

from typing import Sequence

from acp.schemas.judge import JudgeVerdict


def _agreement_two(verdicts: Sequence[JudgeVerdict]) -> float:
    """Cohen's kappa for exactly two raters over a single binary item.

    With one item per rater the formula degenerates: agreement is 1 if they
    match, 0 otherwise. We map this to {1.0, -1.0} so the value reflects
    full agreement vs full disagreement.
    """
    a, b = verdicts[0].passed, verdicts[1].passed
    return 1.0 if a == b else -1.0


def _fleiss(verdicts: Sequence[JudgeVerdict]) -> float:
    """Fleiss's kappa for >=3 raters on a single binary item.

    Single-item Fleiss reduces to a closed form: with N raters and k
    "pass" votes, observed agreement is (k^2 + (N-k)^2 - N) / (N*(N-1)).
    The expected agreement uses the marginal pass rate p = k/N:
    Pe = p^2 + (1-p)^2. Kappa = (Po - Pe) / (1 - Pe) when Pe < 1, else 1.0
    if all raters agree (degenerate but informative).
    """
    n = len(verdicts)
    if n < 2:
        return 1.0
    k = sum(1 for v in verdicts if v.passed)
    po = (k * k + (n - k) * (n - k) - n) / (n * (n - 1))
    p = k / n
    pe = p * p + (1.0 - p) * (1.0 - p)
    if pe >= 1.0:
        # All raters agreed; max kappa.
        return 1.0
    return (po - pe) / (1.0 - pe)


def cohen_kappa(verdicts: Sequence[JudgeVerdict]) -> float:
    """Agreement over the `passed` dimension.

    n=1 → 1.0 (no disagreement possible)
    n=2 → 1.0 if agree else -1.0
    n>=3 → Fleiss's kappa
    """
    if len(verdicts) <= 1:
        return 1.0
    if len(verdicts) == 2:
        return _agreement_two(verdicts)
    return _fleiss(verdicts)

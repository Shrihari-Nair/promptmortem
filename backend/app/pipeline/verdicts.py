"""Pure statistics: noise estimation, noise-adjusted verdicts, paired tests. No DB, no LLM (known-answer tested).

Scores are oriented so higher = better (programmatic/judge values in 0..1; pairwise preference in -1..1)."""
import math
from collections.abc import Sequence

import numpy as np
from scipy import stats

from app.config import settings


def ss_dof(groups: Sequence[Sequence[float]]) -> tuple[float, int]:
    """Within-group sum of squares and degrees of freedom (groups = repeats of one scenario)."""
    ss = dof = 0.0
    for g in groups:
        if len(g) >= 2:
            m = sum(g) / len(g)
            ss += sum((x - m) ** 2 for x in g)
            dof += len(g) - 1
    return ss, int(dof)


def pooled_variance(groups: Sequence[Sequence[float]]) -> tuple[float, int]:
    """Within-scenario variance of repeated runs of the SAME prompt, pooled over scenarios, shrunk toward a small prior
    (so 'no flips seen in a small sample' never means 'zero noise'). Returns (variance, degrees of freedom observed)."""
    ss, dof = ss_dof(groups)
    n0 = settings.noise_prior_n
    return (ss + settings.noise_prior_var * n0) / (dof + n0), dof


def shrunk_variance(groups: Sequence[Sequence[float]], global_var: float) -> float:
    """Noise of a subgroup (a cluster): its own within-scenario variance, shrunk toward the global estimate. LLM noise is
    heteroscedastic (borderline cases flip, easy ones do not), so one pooled number would under-state it where it matters."""
    ss, dof = ss_dof(groups)
    n0 = settings.noise_prior_n
    return (ss + n0 * global_var) / (dof + n0)


def band(var: float, k_old: int, k_new: int, m: int = 1) -> float:
    """Half-width of the old-vs-old difference of means for k_old vs k_new repeats. m=1: the plain 95% noise band.
    m>1: Bonferroni-corrected for m comparisons, so m per-case flags have a family-wise false-alarm rate of ~alpha."""
    z = stats.norm.ppf(1 - settings.significance_alpha / (2 * max(m, 1)))
    return float(z * math.sqrt(var * (1 / max(k_old, 1) + 1 / max(k_new, 1))))


def q_from_var(v: float) -> float:
    """Per-run flip probability of a binary outcome from its variance v = q(1-q) (the smaller root)."""
    return 0.5 if v >= 0.25 else (1 - math.sqrt(1 - 4 * v)) / 2


def tail(k: int, q: float, n: int) -> float:
    """P(Binomial(k, q) >= n)."""
    return 1.0 if n <= 0 else float(stats.binom.sf(n - 1, k, min(max(q, 1e-3), 0.5)))


def needed_repeats(q: float, m: int = 1) -> int:
    """Fewest repeats at which a FULL flip of one case is significant at the (Bonferroni) level: q^k < alpha/m."""
    q = min(max(q, 1e-3), 0.5)
    return max(2, math.ceil(math.log(settings.significance_alpha / max(m, 1)) / math.log(q) + 1e-9))


def min_flips(k: int, q: float, m: int) -> int | None:
    """Smallest number of deviating repeats that is significant for ONE case at k repeats (None: impossible, need more repeats)."""
    for n in range(1, k + 1):
        if tail(k, q, n) < settings.significance_alpha / max(m, 1):
            return n
    return None


def _classify(d: float, p: float, p_min: float, m: int, unstable: bool) -> str:
    """p: tail probability of the observed deviation under noise alone; p_min: the smallest p this design can produce."""
    a = settings.significance_alpha
    if abs(d) < 1e-9:
        return "inconclusive" if (p_min >= a and unstable) else "unchanged"  # no power + unstable: do not claim 'unchanged'
    if p < a / max(m, 1):
        return "fixed" if d > 0 else "broken"
    if p_min >= a or p < a:
        return "inconclusive"  # cannot tell noise from change (no power) or suggestive but not significant after correction
    return "unchanged"


def verdict_binary(old: Sequence[float], new: Sequence[float], var: float, m: int = 1) -> tuple[str, float]:
    """Exact test for 0/1 metrics. Baseline = the old arm's majority outcome; evidence of change = how many of the new
    arm's repeats deviate from it, versus a binomial at the cluster's measured flip rate (never below the case's own)."""
    if not old or not new:
        return "untested", 1.0
    base = 1.0 if sum(old) / len(old) >= 0.5 else 0.0
    dev_new = sum(1 for x in new if x != base)
    q = max(q_from_var(var), sum(1 for x in old if x != base) / len(old))
    p = tail(len(new), q, dev_new)
    d = sum(new) / len(new) - sum(old) / len(old)
    unstable = len(set(old)) > 1 or len(set(new)) > 1
    return _classify(d, p, min(max(q, 1e-3), 0.5) ** len(new), m, unstable), p


def verdict_pairwise(pref: Sequence[float], var: float, m: int = 1) -> tuple[str, float]:
    """pref per repeat in {-1 (old better), 0, +1 (new better)}; `var` = per-direction rate of spurious preference."""
    if not pref:
        return "untested", 1.0
    k, neg, pos = len(pref), sum(1 for x in pref if x < 0), sum(1 for x in pref if x > 0)
    nu = min(max(var, 1e-3), 0.5)
    p = min(tail(k, nu, neg), tail(k, nu, pos))
    return _classify(pos - neg, p, nu**k, m, len(set(pref)) > 1), p


def verdict(old: Sequence[float], new: Sequence[float], b: float, b_noise: float | None = None) -> str:
    """Continuous metrics (likert, mean-aggregated checks): fixed | broken | unchanged | inconclusive | untested. A change
    counts only if it exceeds the detection band `b` (noise band corrected for multiple comparisons); `b_noise` (the plain
    95% band, default `b`) says whether the metric is too noisy to resolve small differences."""
    b_noise = b if b_noise is None else b_noise
    if not old or not new:
        return "untested"
    d = sum(new) / len(new) - sum(old) / len(old)
    if abs(d) > b + 1e-9:
        return "fixed" if d > 0 else "broken"
    unstable = len(set(old)) > 1 or len(set(new)) > 1
    if (abs(d) > b_noise + 1e-9) or (b_noise >= settings.noisy_band_cut and (abs(d) > 1e-9 or unstable)):
        return "inconclusive"
    return "unchanged"


def combine(verdicts: Sequence[str]) -> str:
    """Scenario bucket from its metric verdicts: regressions first."""
    for v in ("broken", "fixed", "inconclusive", "unchanged"):
        if v in verdicts:
            return v
    return "untested"


def mcnemar_exact(old_pass: Sequence[bool], new_pass: Sequence[bool]) -> dict:
    """Exact McNemar on paired binary outcomes. b = pass->fail (broken), c = fail->pass (fixed)."""
    b = sum(o and not n for o, n in zip(old_pass, new_pass))
    c = sum(n and not o for o, n in zip(old_pass, new_pass))
    p = 1.0 if b + c == 0 else float(stats.binomtest(min(b, c), b + c, 0.5).pvalue)
    return {"b": b, "c": c, "p": p}


def paired_bootstrap(diffs: Sequence[float], weights: Sequence[float] | None = None) -> dict:
    """Mean of per-scenario paired differences with a bootstrap 95% CI (resampling scenarios)."""
    d = np.asarray(diffs, dtype=float)
    if d.size == 0:
        return {"mean": 0.0, "ci95": [0.0, 0.0], "n": 0}
    rng = np.random.default_rng(settings.bootstrap_seed)
    idx = rng.integers(0, d.size, size=(settings.bootstrap_n, d.size))
    means = d[idx].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"mean": float(d.mean()), "ci95": [float(lo), float(hi)], "n": int(d.size)}


def suggested_repeats(var: float, k_now: int) -> int:
    """Smallest k (same on both arms) whose band is below the 'noisy' cut."""
    z = stats.norm.ppf(1 - settings.significance_alpha / 2)
    return max(k_now, math.ceil(2 * var * (z / settings.noisy_band_cut) ** 2))

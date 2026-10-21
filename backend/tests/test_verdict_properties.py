"""Metamorphic/property tests for verdicts.py: invariants that must hold for ANY input, plus measured error rates.
Seeded stdlib random (no extra dependency); known-answer tests live in test_verdicts.py."""
import random

import pytest

from app.config import settings
from app.pipeline import verdicts as v

MIRROR = {"fixed": "broken", "broken": "fixed"}
mirror = lambda x: MIRROR.get(x, x)  # noqa: E731


def arms(rng, k=None):
    k = k or rng.randint(2, 6)
    return [float(rng.random() < 0.5) for _ in range(rng.randint(2, 6))], [float(rng.random() < 0.5) for _ in range(k)]


def test_repeat_order_never_matters():
    rng = random.Random(1)
    for _ in range(300):
        old, new = arms(rng)
        var, m = rng.uniform(0.01, 0.25), rng.choice([1, 20, 200])
        pref = [float(rng.choice([-1, 0, 1])) for _ in range(rng.randint(2, 6))]
        shuffled = lambda xs: rng.sample(xs, len(xs))  # noqa: E731
        assert v.verdict_binary(old, new, var, m) == v.verdict_binary(shuffled(old), shuffled(new), var, m)
        assert v.verdict_pairwise(pref, var, m) == v.verdict_pairwise(shuffled(pref), var, m)
        assert v.verdict(old, new, 0.3) == v.verdict(shuffled(old), shuffled(new), 0.3)
        vs = [rng.choice(["fixed", "broken", "unchanged", "inconclusive"]) for _ in range(5)]
        assert v.combine(vs) == v.combine(shuffled(vs))


def test_continuous_verdict_mirrors_when_arms_swap():
    rng = random.Random(2)
    for _ in range(300):
        old, new = ([rng.random() for _ in range(rng.randint(1, 6))] for _ in range(2))
        b = rng.uniform(0.05, 0.6)
        assert v.verdict(new, old, b) == mirror(v.verdict(old, new, b))


def test_binary_verdict_mirrors_under_outcome_complement():
    """1-x on both arms turns every fix into a break. Odd old-arm size avoids the tie-break at exactly 50% pass."""
    rng = random.Random(3)
    for _ in range(300):
        old = [float(rng.random() < 0.5) for _ in range(rng.choice([3, 5]))]
        new = [float(rng.random() < 0.5) for _ in range(rng.randint(2, 6))]
        var, m = rng.uniform(0.01, 0.25), rng.choice([1, 50])
        a, pa = v.verdict_binary(old, new, var, m)
        b, pb = v.verdict_binary([1 - x for x in old], [1 - x for x in new], var, m)
        assert b == mirror(a) and pa == pytest.approx(pb)


def test_more_deviation_never_weakens_the_verdict():
    """Holding the old arm fixed, flipping more new repeats away from baseline lowers p and never un-breaks a case."""
    for k in (3, 5, 8):
        old, rank = [1.0] * k, {"unchanged": 0, "inconclusive": 1, "broken": 2}
        ps, seen_broken = [], False
        for dev in range(k + 1):
            verdict, p = v.verdict_binary(old, [0.0] * dev + [1.0] * (k - dev), 0.05, 20)
            ps.append(p)
            seen_broken |= verdict == "broken"
            assert not seen_broken or verdict == "broken", (k, dev)
        assert all(a >= b for a, b in zip(ps, ps[1:])), ps


def test_identical_arms_never_flag():
    rng = random.Random(4)
    for _ in range(300):
        xs = [float(rng.random() < 0.5) for _ in range(rng.randint(1, 6))]
        assert v.verdict_binary(xs, list(xs), rng.uniform(0.01, 0.25), rng.choice([1, 100]))[0] in ("unchanged", "inconclusive")
        assert v.verdict(xs, list(xs), 0.2) in ("unchanged", "inconclusive")


def test_bands_widen_with_comparisons_and_shrink_with_repeats():
    for var in (0.02, 0.1, 0.25):
        assert v.band(var, 3, 3, 1) < v.band(var, 3, 3, 10) < v.band(var, 3, 3, 500)
        assert v.band(var, 2, 2) > v.band(var, 4, 4) > v.band(var, 8, 8)


@pytest.mark.parametrize("q", [0.02, 0.05, 0.1])
def test_full_flip_is_detected_once_repeats_reach_needed_repeats(q):
    m, var = 60, q * (1 - q)
    k = v.needed_repeats(q, m)
    assert v.verdict_binary([1.0] * k, [0.0] * k, var, m)[0] == "broken"
    assert v.verdict_binary([0.0] * k, [1.0] * k, var, m)[0] == "fixed"


@pytest.mark.parametrize("q,k", [(0.05, 3), (0.1, 3), (0.1, 5), (0.2, 4)])
def test_false_alarm_rate_on_a_no_op_stays_near_alpha(q, k):
    """Old and new are the SAME process (each repeat flips with prob q). Flag rate across m=1 cases must be <= ~alpha;
    this is the number that silently drifts if anyone changes the exact-test code."""
    rng, n, flags = random.Random(5), 4000, 0
    for _ in range(n):
        draw = lambda: [1.0 - float(rng.random() < q) for _ in range(k)]  # noqa: E731  (baseline outcome 1, flipped w.p. q)
        flags += v.verdict_binary(draw(), draw(), q * (1 - q), 1)[0] in ("fixed", "broken")
    assert flags / n <= settings.significance_alpha * 1.2, flags / n


def test_family_wise_false_alarm_rate_is_controlled_by_the_correction():
    rng, m, trials, q, k = random.Random(6), 40, 300, 0.1, 4
    hit = 0
    for _ in range(trials):
        draw = lambda: [1.0 - float(rng.random() < q) for _ in range(k)]  # noqa: E731
        hit += any(v.verdict_binary(draw(), draw(), q * (1 - q), m)[0] in ("fixed", "broken") for _ in range(m))
    assert hit / trials <= settings.significance_alpha * 1.5, hit / trials


def test_cohen_kappa_known_answers():
    assert v.cohen_kappa(["pass", "fail"] * 5, ["pass", "fail"] * 5) == pytest.approx(1.0)
    assert v.cohen_kappa(["pass"] * 9 + ["fail"], ["pass"] * 10) == pytest.approx(0.0)  # 90% raw agreement, zero skill
    assert v.cohen_kappa(["pass", "fail"] * 5, ["fail", "pass"] * 5) == pytest.approx(-1.0)
    assert v.cohen_kappa(["pass"] * 4, ["pass"] * 4) is None and v.cohen_kappa([], []) is None

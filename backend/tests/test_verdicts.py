import math

import pytest

from app.config import settings
from app.pipeline import verdicts as v


def test_pooled_variance_known_answer_and_shrinkage():
    # two scenarios, 2 repeats each: [1,0] has SS=0.5, [1,1] has SS=0 -> dof=2, SS=0.5
    var, dof = v.pooled_variance([[1, 0], [1, 1]])
    assert dof == 2 and var == pytest.approx((0.5 + settings.noise_prior_var * settings.noise_prior_n) / (2 + settings.noise_prior_n))
    # no repeats seen / no flips seen: never zero noise
    assert v.pooled_variance([])[0] == pytest.approx(settings.noise_prior_var)
    assert v.pooled_variance([[1, 1]] * 6)[0] > 0
    # lots of clean data washes the prior out
    assert v.pooled_variance([[1, 0]] * 500)[0] == pytest.approx(0.5, abs=0.01)  # every pair disagrees: sample variance 0.5
    assert v.pooled_variance([[1, 0], [1, 1], [0, 0], [1, 1]] * 100)[0] == pytest.approx(0.125, abs=0.01)  # 1 in 4 pairs disagree


def test_band_known_answer():
    assert v.band(0.25, 1, 1) == pytest.approx(1.959964 * math.sqrt(0.5), rel=1e-5)
    assert v.band(0.25, 4, 4) == pytest.approx(v.band(0.25, 1, 1) / 2, rel=1e-9)  # halves with 4x the repeats
    assert v.band(0.0, 3, 3) == 0.0


def test_verdict_respects_the_noise_band():
    b = 0.35
    assert v.verdict([1, 1, 1], [0, 0, 0], b) == "broken" and v.verdict([0, 0, 0], [1, 1, 1], b) == "fixed"
    assert v.verdict([1, 1, 1], [1, 1, 0], b) == "unchanged"  # one flip in three (0.33) is inside a 0.35 band
    assert v.verdict([1, 1, 1], [1, 0, 0], b) == "broken"  # two flips (0.67) exceed it
    assert v.verdict([1, 1, 1], [1, 1, 1], b) == "unchanged"
    assert v.verdict([], [1], b) == "untested"
    # when noise is so high a flip cannot be resolved, a within-band difference is inconclusive, not unchanged
    assert v.verdict([1, 0, 1], [1, 1, 1], 0.6) == "inconclusive"
    assert v.verdict([1, 1, 1], [1, 1, 1], 0.6) == "unchanged"
    assert v.verdict([1, 1], [0, 0], 0.6) == "broken"  # a full flip still exceeds a 0.6 band


def test_combine_puts_regressions_first():
    assert v.combine(["fixed", "broken", "unchanged"]) == "broken"
    assert v.combine(["fixed", "unchanged"]) == "fixed"
    assert v.combine(["inconclusive", "unchanged"]) == "inconclusive"
    assert v.combine(["unchanged"]) == "unchanged" and v.combine([]) == "untested"


def test_mcnemar_exact_known_answers():
    # 8 discordant pairs, all pass->fail: exact two-sided p = 2 * 0.5^8
    r = v.mcnemar_exact([True] * 8 + [True] * 5, [False] * 8 + [True] * 5)
    assert (r["b"], r["c"]) == (8, 0) and r["p"] == pytest.approx(2 * 0.5**8)
    assert v.mcnemar_exact([True, False], [True, False])["p"] == 1.0  # no discordant pairs
    assert v.mcnemar_exact([True, False, True, False], [False, True, True, False])["p"] == 1.0  # 1 vs 1


def test_paired_bootstrap_ci_covers_the_mean_and_is_deterministic():
    d = [0.1, 0.0, 0.2, 0.1, 0.0, 0.3, 0.1, 0.2] * 5
    r = v.paired_bootstrap(d)
    assert r["mean"] == pytest.approx(sum(d) / len(d)) and r["ci95"][0] < r["mean"] < r["ci95"][1] and r["ci95"][0] > 0
    assert v.paired_bootstrap(d) == r  # seeded
    z = v.paired_bootstrap([0.0, 0.0, 0.0])
    assert z["ci95"] == [0.0, 0.0]
    assert v.paired_bootstrap([-1, 1] * 20)["ci95"][0] < 0 < v.paired_bootstrap([-1, 1] * 20)["ci95"][1]  # symmetric noise: CI spans 0


def test_suggested_repeats_shrinks_band_below_cut():
    var = 0.25
    k = v.suggested_repeats(var, 3)
    assert v.band(var, k, k) < settings.noisy_band_cut <= v.band(var, k - 1, k - 1) or k == 3


def test_multiplicity_correction_widens_the_per_case_band_only():
    assert v.band(0.25, 3, 3, m=1) == v.band(0.25, 3, 3)
    assert v.band(0.25, 3, 3, m=60) > v.band(0.25, 3, 3) * 1.6  # z 1.96 -> ~3.34 for 60 comparisons (ratio 1.70)
    plain, wide = v.band(0.03, 3, 3), v.band(0.03, 3, 3, 60)
    assert v.verdict([1, 1, 1], [1, 1, 0], plain) == "broken"  # one flip in three beats the plain band...
    assert v.verdict([1, 1, 1], [1, 1, 0], wide, plain) == "inconclusive"  # ...but is only suggestive once corrected
    assert v.verdict([1, 1, 1], [0, 0, 0], wide, plain) == "broken"  # a real regression still is
    assert v.verdict([1, 1, 1], [1, 1, 1], wide, plain) == "unchanged"


def test_exact_helpers_known_answers():
    assert v.q_from_var(0.0475) == pytest.approx(0.05, abs=1e-3) and v.q_from_var(0.25) == 0.5 and v.q_from_var(0.0) == 0.0
    assert v.tail(5, 0.05, 4) == pytest.approx(3.0e-5, rel=0.05) and v.tail(5, 0.05, 0) == 1.0
    assert v.tail(3, 0.5, 3) == pytest.approx(0.125)
    assert v.needed_repeats(0.05, 166) == 3 and v.needed_repeats(0.5, 166) == 12 and v.needed_repeats(0.2, 166) == 6
    assert v.min_flips(5, 0.05, 166) == 4 and v.min_flips(3, 0.05, 166) == 3 and v.min_flips(2, 0.05, 166) is None


def test_verdict_binary_exact_three_way_outcome():
    var, m = 0.0475, 166  # ~5% flip rate, 166 comparisons in the run
    assert v.verdict_binary([1] * 5, [0, 0, 0, 0, 1], var, m)[0] == "broken"  # 4 of 5 deviate: p ~ 3e-5 < alpha/m
    assert v.verdict_binary([0] * 5, [1] * 5, var, m)[0] == "fixed"
    assert v.verdict_binary([1] * 5, [0, 0, 1, 1, 1], var, m)[0] == "inconclusive"  # 2 of 5: p ~ 0.023: suggestive only
    assert v.verdict_binary([1] * 5, [1, 1, 1, 0, 1], var, m)[0] == "unchanged"  # 1 of 5 is ordinary noise
    assert v.verdict_binary([1] * 5, [1] * 5, var, m)[0] == "unchanged"
    # the flaw this replaces: the normal band called 2-of-5 'broken'; the exact tail does not
    assert v.verdict([1] * 5, [0, 0, 1, 1, 1], v.band(0.03, 5, 5, m)) == "broken"
    # no power (noise too high for the repeats): never claim 'unchanged' for an unstable case
    assert v.verdict_binary([1, 1, 1], [0, 0, 0], 0.25, m)[0] == "inconclusive"
    assert v.verdict_binary([1, 1], [1, 0], 0.25, m)[0] == "inconclusive"
    # a noisy case's own old-arm flips raise its baseline noise
    assert v.verdict_binary([1, 0, 1, 1, 0], [0, 0, 0, 0, 0], 0.0475, m)[0] != "broken"
    assert v.verdict_binary([], [1], var, m)[0] == "untested"


def test_verdict_pairwise_exact():
    assert v.verdict_pairwise([-1] * 5, 0.025, 166)[0] == "broken" and v.verdict_pairwise([1] * 5, 0.025, 166)[0] == "fixed"
    assert v.verdict_pairwise([-1, 0, 0, 0, 0], 0.025, 166)[0] == "unchanged"  # one stray preference in five is noise
    assert v.verdict_pairwise([0, 0, 0, 0, 0], 0.025, 166)[0] == "unchanged"
    assert v.verdict_pairwise([-1, -1], 0.4, 166)[0] == "inconclusive"  # no power at this noise
    assert v.verdict_pairwise([-1, 1, -1, 1, 0], 0.025, 166)[0] == "unchanged"  # flip-flopping with no net direction is noise

"""The maximum-likelihood noise model, on series whose noise is known.

Each case is a constant plus noise built here with known parameters, so
the tests can ask whether the right model is chosen, whether its
parameters come back, and whether the 95% interval for the constant
holds about 95% of the time.
"""

from __future__ import annotations

import math
import random

import pytest

from averaging.estimator import _combine_sessions, small_sample_factor
from averaging.noise_model import fit_axis


def _series(rng, n, white, correlated, correlation_time, gaps=False):
    times, values = [], []
    state, now = 0.0, 0.0
    for i in range(n):
        step = rng.uniform(60, 300) if gaps and rng.random() < 0.01 else 1.0
        now += step
        if correlated:
            phi = math.exp(-step / correlation_time)
            state = phi * state + math.sqrt(1 - phi * phi) * correlated * rng.gauss(0, 1)
        times.append(now)
        values.append(state + white * rng.gauss(0, 1))
    return values, times


def test_white_noise_is_called_white():
    rng = random.Random(1)
    values, times = _series(rng, 600, 0.01, 0.0, 1.0)
    fit = fit_axis(values, [1.0] * len(values), times)
    assert fit.model == "white"
    assert fit.white_sigma == pytest.approx(0.01, rel=0.1)
    assert fit.effective_epochs == pytest.approx(600, rel=0.05)


def test_gauss_markov_parameters_come_back():
    rng = random.Random(2)
    values, times = _series(rng, 3000, 0.004, 0.008, 10.0)
    fit = fit_axis(values, [1.0] * len(values), times)
    assert fit.model == "white+gauss-markov"
    assert fit.correlation_time_s == pytest.approx(10.0, rel=0.4)
    assert fit.correlated_sigma == pytest.approx(0.008, rel=0.25)


def test_a_flat_series_with_one_heavy_epoch_follows_the_weights():
    """With white noise the GLS mean is the weighted mean."""
    values = [0.0, 0.0, 0.0, 1.0]
    fit = fit_axis(values, [1.0, 1.0, 1.0, 1.0 / 3.0], [0.0, 1.0, 2.0, 3.0])
    assert fit.mean == pytest.approx(0.5)


@pytest.mark.parametrize("gaps", [False, True])
def test_the_interval_holds_under_correlated_noise(gaps):
    rng = random.Random(3 + gaps)
    trials, hits = 60, 0
    for _ in range(trials):
        values, times = _series(rng, 400, 0.004, 0.008, 15.0, gaps=gaps)
        fit = fit_axis(values, [1.0] * len(values), times)
        sigma = math.sqrt(fit.variance_of_mean * small_sample_factor(fit.effective_epochs))
        hits += abs(fit.mean) <= 1.96 * sigma
    assert hits / trials >= 0.85


def test_long_series_are_reduced_to_blocks_without_moving_the_mean():
    rng = random.Random(4)
    values, times = _series(rng, 7000, 0.01, 0.0, 1.0)
    fit = fit_axis(values, [1.0] * len(values), times)
    assert fit.mean == pytest.approx(sum(values) / len(values), abs=1e-9)


def test_sessions_that_agree_add_no_between_variance():
    mean, variance, between = _combine_sessions([1.0, 1.0, 1.0], [0.01, 0.01, 0.01])
    assert (mean, between) == (1.0, 0.0)
    assert variance == pytest.approx(0.01 / 3)


def test_sessions_that_disagree_widen_the_result():
    """Three sessions each claiming ±1 mm but spread over 1 cm: the answer
    must not claim ±0.6 mm."""
    mean, variance, between = _combine_sessions([0.0, 0.01, -0.01], [1e-6, 1e-6, 1e-6])
    assert mean == pytest.approx(0.0, abs=1e-12)
    assert between == pytest.approx(0.01, rel=0.05)
    assert math.sqrt(variance) > 0.005

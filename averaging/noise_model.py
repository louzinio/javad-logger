"""The mean of one coordinate axis, by maximum likelihood under coloured noise.

This is how geodesy treats a series of positions of a point that is not
moving: as a constant plus noise that is partly white and partly
correlated in time, with the noise model's parameters estimated from the
series itself by maximum likelihood and the constant estimated by
generalised least squares under that model. It is the approach of the
standard time-series packages - Hector, CATS, est_noise - and the reason
it is the standard is that it is the best linear unbiased estimate of the
constant *and* gives an uncertainty that follows from the noise actually
present, rather than one that assumes the epochs are independent.

The coloured part here is a first-order Gauss-Markov process: noise that
remembers its last value with a correlation time ``Tc`` and forgets it
exponentially. Over a session of minutes to hours that is the shape of
the error that matters - multipath from a slowly moving satellite, a
drifting atmosphere - and it is the model the literature names alongside
flicker noise for GNSS positions. Flicker noise matters over months and
years of a permanent station, which is not what a logger session is.

Three things make it better than averaging and then estimating the
correlation, which is what :mod:`averaging.estimator` did before:

* The epochs are weighted by the model, so a run of epochs that share the
  same multipath counts as roughly one look rather than as many.
* Gaps in the recording are handled exactly: the correlation between two
  epochs follows from the time between them, not from their position in
  the file.
* Whether there is correlated noise at all is decided by the Bayesian
  information criterion, so a white series is not given a correlated
  model it does not have.

The likelihood is evaluated with a scalar Kalman filter, which is exact
for this model and linear in the number of epochs; the constant and the
overall noise scale are concentrated out analytically, leaving two
parameters to search. The restricted (REML) form of the likelihood is
used, which does not shrink the variance for having estimated the mean -
the plain form does, and on a short series that is the difference between
an interval that holds and one that does not.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

MAX_EPOCHS = 3000
"""Longer series are averaged in consecutive blocks down to this many
before the search. Block means keep the mean and carry their own reduced
white variance, so nothing is lost but time - a 10 Hz hour is 36 000
epochs, and searching the likelihood on all of them in pure Python would
take minutes."""


@dataclass(frozen=True)
class AxisFit:
    mean: float
    variance_of_mean: float
    white_sigma: float
    """Of an epoch whose relative white variance is 1 (the median epoch)."""
    correlated_sigma: float
    correlation_time_s: float
    """Of the slowest correlated component; 0 for white noise."""
    model: str
    """``white`` or ``white+gauss-markov``."""
    single_epoch_variance: float
    log_likelihood: float

    @property
    def effective_epochs(self) -> float:
        """How many independent epochs the series was worth."""
        return self.single_epoch_variance / self.variance_of_mean if self.variance_of_mean > 0 else math.inf


def _filter(
    values: Sequence[float],
    relative_white: Sequence[float],
    steps: Sequence[float],
    components: Sequence[tuple[float, float]],
) -> tuple[float, float, float, float, int]:
    """One pass of the Kalman filter at unit scale.

    ``components`` are the Gauss-Markov processes as (variance relative to
    the white noise, correlation time); none of them is white noise only.
    One component is what :func:`fit_axis` uses. A second, slower one was
    tried and dropped: on simulated sessions BIC almost never preferred
    it, because an error slower than the session itself cannot be told
    apart from the mean inside that session - which is why repeat
    occupations, handled in :mod:`averaging.estimator`, are the remedy.

    Returns ``(mean, sum_ones, quadratic, log_det, n)``: the GLS mean, the
    information about it at unit scale, the weighted residual sum of
    squares, and the log-determinant of the innovation variances - the
    pieces the concentrated likelihood is built from.
    """
    k = len(components)
    q = [c[0] for c in components]
    tc = [c[1] for c in components]
    xy = [0.0] * k
    x1 = [0.0] * k
    p = [[q[i] if i == j else 0.0 for j in range(k)] for i in range(k)]
    syy = s1y = s11 = 0.0
    log_det = 0.0
    for index, (y, r) in enumerate(zip(values, relative_white)):
        if index and k:
            phi = [math.exp(-steps[index] / tc[i]) for i in range(k)]
            for i in range(k):
                xy[i] *= phi[i]
                x1[i] *= phi[i]
            for i in range(k):
                for j in range(k):
                    p[i][j] *= phi[i] * phi[j]
                p[i][i] += q[i] * (1.0 - phi[i] * phi[i])
        # The observation is the sum of the components plus white noise.
        column = [sum(p[i][j] for j in range(k)) for i in range(k)]
        f = sum(column) + r
        v_y = y - sum(xy)
        v_1 = 1.0 - sum(x1)
        syy += v_y * v_y / f
        s1y += v_1 * v_y / f
        s11 += v_1 * v_1 / f
        log_det += math.log(f)
        if k:
            gain = [c / f for c in column]
            for i in range(k):
                xy[i] += gain[i] * v_y
                x1[i] += gain[i] * v_1
            for i in range(k):
                for j in range(k):
                    p[i][j] -= gain[i] * column[j]
    mean = s1y / s11
    quadratic = syy - s1y * s1y / s11
    return mean, s11, quadratic, log_det, len(values)


def _restricted_log_likelihood(fit: tuple[float, float, float, float, int]) -> tuple[float, float]:
    """REML log-likelihood with the scale concentrated out, and that scale."""
    _, s11, quadratic, log_det, n = fit
    dof = n - 1
    scale = max(quadratic / dof, 1e-300)
    value = -0.5 * (dof * math.log(2 * math.pi * scale) + log_det + math.log(s11) + dof)
    return value, scale


def _evaluate(values, relative_white, steps, components):
    raw = _filter(values, relative_white, steps, components)
    log_likelihood, scale = _restricted_log_likelihood(raw)
    return log_likelihood, scale, raw


def _nelder_mead(function, start: list[float], step: float = 0.7, iterations: int = 120) -> tuple[list[float], float]:
    """Minimise a function of a few variables. Small and dependable, which
    is all a two-parameter likelihood surface needs."""
    dim = len(start)
    points = [list(start)] + [[start[k] + (step if k == i else 0.0) for k in range(dim)] for i in range(dim)]
    scores = [function(point) for point in points]
    for _ in range(iterations):
        order = sorted(range(dim + 1), key=lambda i: scores[i])
        points = [points[i] for i in order]
        scores = [scores[i] for i in order]
        if abs(scores[-1] - scores[0]) < 1e-7:
            break
        centre = [sum(point[k] for point in points[:-1]) / dim for k in range(dim)]
        worst = points[-1]
        reflected = [centre[k] + (centre[k] - worst[k]) for k in range(dim)]
        score = function(reflected)
        if score < scores[0]:
            expanded = [centre[k] + 2 * (centre[k] - worst[k]) for k in range(dim)]
            expanded_score = function(expanded)
            points[-1], scores[-1] = (expanded, expanded_score) if expanded_score < score else (reflected, score)
        elif score < scores[-2]:
            points[-1], scores[-1] = reflected, score
        else:
            contracted = [centre[k] + 0.5 * (worst[k] - centre[k]) for k in range(dim)]
            contracted_score = function(contracted)
            if contracted_score < scores[-1]:
                points[-1], scores[-1] = contracted, contracted_score
            else:
                for i in range(1, dim + 1):
                    points[i] = [points[0][k] + 0.5 * (points[i][k] - points[0][k]) for k in range(dim)]
                    scores[i] = function(points[i])
    best = min(range(dim + 1), key=lambda i: scores[i])
    return points[best], scores[best]


def _blocks(values, relative_white, times):
    """Consecutive block means, so that at most MAX_EPOCHS remain."""
    n = len(values)
    size = math.ceil(n / MAX_EPOCHS)
    if size <= 1:
        return list(values), list(relative_white), list(times)
    out_v, out_r, out_t = [], [], []
    for start in range(0, n, size):
        chunk = range(start, min(start + size, n))
        weights = [1.0 / relative_white[i] for i in chunk]
        total = sum(weights)
        out_v.append(sum(w * values[i] for w, i in zip(weights, chunk)) / total)
        out_r.append(1.0 / total)
        out_t.append(sum(times[i] for i in chunk) / len(chunk))
    return out_v, out_r, out_t


def fit_axis(values: Sequence[float], relative_white: Sequence[float], times_s: Sequence[float]) -> AxisFit:
    """The GLS mean of one axis under the best-supported noise model.

    ``relative_white`` is each epoch's white variance relative to the
    others - the receiver's ``pos_rms`` squared, already floored and
    down-weighted for outliers by the caller - so that the receiver's own
    judgement of each epoch still shapes the answer. ``times_s`` are epoch
    times in seconds; they need not be evenly spaced.
    """
    median_r = sorted(relative_white)[len(relative_white) // 2]
    relative = [r / median_r for r in relative_white]
    values, relative, times = _blocks(values, relative, times_s)
    origin = sum(values) / len(values)
    values = [v - origin for v in values]
    steps = [0.0] + [max(times[i] - times[i - 1], 1e-3) for i in range(1, len(times))]
    n = len(values)
    span = max(times[-1] - times[0], steps[1] if n > 1 else 1.0)
    typical_step = sorted(steps[1:])[len(steps[1:]) // 2] if n > 1 else 1.0

    low_tc, high_tc = math.log(typical_step * 0.5), math.log(span * 10)

    def unpack(point: list[float]) -> list[tuple[float, float]]:
        pairs = []
        for i in range(0, len(point), 2):
            ratio = math.exp(max(min(point[i], 12.0), -12.0))
            correlation = math.exp(min(max(point[i + 1], low_tc), high_tc))
            pairs.append((ratio, correlation))
        return sorted(pairs, key=lambda pair: pair[1])

    def cost(point: list[float]) -> float:
        return -_evaluate(values, relative, steps, unpack(point))[0]

    white_ll, white_scale, white_raw = _evaluate(values, relative, steps, [])
    candidates = [("white", white_ll, white_scale, white_raw, [], 0)]

    if n >= 8:
        # A coarse grid first: the surface has a ridge where a long
        # correlation time trades against a large variance, and starting
        # the simplex in the right valley matters more than polishing.
        fractions = (0.002, 0.02, 0.1, 0.3, 1.0)
        grid = [
            [log_ratio, math.log(max(span * fraction, typical_step))]
            for log_ratio in (-3.0, -1.0, 1.0, 3.0, 5.0)
            for fraction in fractions
        ]
        start_one = min(grid, key=cost)
        one, _ = _nelder_mead(cost, start_one)
        components = unpack(one)
        ll, scale, raw = _evaluate(values, relative, steps, components)
        candidates.append(("white+gauss-markov", ll, scale, raw, components, 2))

    # The Bayesian information criterion: each parameter costs ln(n) of
    # deviance, so a richer model has to earn its place.
    best = min(candidates, key=lambda c: -2 * c[1] + c[5] * math.log(n))
    model, ll, scale, raw, components, _ = best
    ratio = sum(c[0] for c in components)
    correlation = max((c[1] for c in components), default=0.0)
    mean, s11, _, _, _ = raw
    variance_of_mean = scale / s11
    white = math.sqrt(scale)
    correlated = math.sqrt(scale * ratio)
    return AxisFit(
        mean=mean + origin,
        variance_of_mean=variance_of_mean,
        white_sigma=white,
        correlated_sigma=correlated,
        correlation_time_s=correlation,
        model=model,
        single_epoch_variance=scale * (1.0 + ratio),
        log_likelihood=ll,
    )

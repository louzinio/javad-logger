"""The estimator, run on recordings whose truth is known because they were
made here.

Each synthetic recording is a known point plus noise of a known shape, so
the tests can ask the questions that matter: does the answer land on the
point, does the error estimate cover the real error, does a bad epoch get
thrown out, and does a better solution type win over a worse one.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from averaging.estimator import (
    Accuracy,
    RecordingError,
    Settings,
    average,
    integrated_autocorrelation_time,
    read_recording,
)
from greis.geodesy import ecef_to_geodetic, enu_rotation, geodetic_to_ecef

TRUTH = (32.0852998, 34.7818064, 37.214)
HEADER = "host_time_utc,lat_deg,lon_deg,alt_m,pos_rms_m,sol_type,sol_type_label"
START = datetime(2026, 10, 21, 9, 14, tzinfo=timezone.utc)


def _row(i: int, enu: tuple[float, float, float], rms: float, sol: int) -> str:
    rotation = enu_rotation(TRUTH[0], TRUTH[1])
    origin = geodetic_to_ecef(*TRUTH)
    ecef = [origin[k] + sum(rotation[a][k] * enu[a] for a in range(3)) for k in range(3)]
    lat, lon, h = ecef_to_geodetic(*ecef)
    time = (START + timedelta(seconds=i)).isoformat()
    return f"{time},{lat:.9f},{lon:.9f},{h:.4f},{rms:.4f},{sol},X"


def _recording(segments, seed=1, outliers=()):
    """``segments`` is a list of (count, sol_type, sigma_h, sigma_v, rho, rms, bias)."""
    rng = random.Random(seed)
    lines = [HEADER]
    i = 0
    for count, sol, sh, sv, rho, rms, bias in segments:
        state = [0.0, 0.0, 0.0]
        for _ in range(count):
            sd = (sh, sh, sv)
            state = [rho * s + math.sqrt(1 - rho * rho) * sd[k] * rng.gauss(0, 1) for k, s in enumerate(state)]
            enu = [state[k] + bias[k] for k in range(3)]
            if i in outliers:
                enu = [enu[0] + 0.3, enu[1] - 0.3, enu[2] + 0.5]
            lines.append(_row(i, tuple(enu), rms, sol))
            i += 1
    return read_recording("\n".join(lines) + "\n", "synthetic.csv")


def _error_enu(result):
    origin = geodetic_to_ecef(*TRUTH)
    rotation = enu_rotation(TRUTH[0], TRUTH[1])
    d = [result.ecef[k] - origin[k] for k in range(3)]
    return [sum(row[k] * d[k] for k in range(3)) for row in rotation]


def test_white_noise_averages_onto_the_point():
    recording = _recording([(1000, 4, 0.01, 0.02, 0.0, 0.015, (0, 0, 0))])
    result = average(recording, Settings(systematic_floor=False))
    error = _error_enu(result)
    # Four sigma of the mean is a generous bound that a correct estimator
    # passes and a broken one does not.
    assert abs(error[0]) < 4 * result.statistical.sigma_east
    assert abs(error[2]) < 4 * result.statistical.sigma_up
    assert result.statistical.sigma_east == pytest.approx(0.01 / math.sqrt(1000), rel=0.35)
    assert max(result.tau) < 1.6


def test_correlated_noise_widens_the_uncertainty():
    """AR(1) with ρ = 0.9 has τ = 19: the honest σ of the mean is about
    √19 ≈ 4.4 times what independence would claim."""
    recording = _recording([(1500, 4, 0.01, 0.02, 0.9, 0.015, (0, 0, 0))], seed=7)
    honest = average(recording, Settings(systematic_floor=False))
    naive = average(recording, Settings(systematic_floor=False, correct_for_correlation=False))
    ratio = honest.statistical.sigma_east / naive.statistical.sigma_east
    assert 3.0 < ratio < 5.5
    assert 10 < honest.tau[0] < 30


def test_outliers_are_rejected_and_do_not_pull_the_mean():
    outliers = set(range(100, 110))
    recording = _recording([(300, 4, 0.005, 0.01, 0.0, 0.01, (0, 0, 0))], seed=3, outliers=outliers)
    result = average(recording, Settings(systematic_floor=False))
    assert result.rejected == len(outliers)
    assert max(abs(v) for v in _error_enu(result)) < 0.003


def test_auto_takes_only_the_best_solution_type():
    """A biased standalone start must not drag a fixed solution."""
    recording = _recording(
        [
            (60, 1, 0.8, 1.5, 0.95, 1.6, (0.6, -0.4, 1.1)),
            (300, 4, 0.006, 0.01, 0.5, 0.012, (0, 0, 0)),
        ],
        seed=11,
    )
    result = average(recording)
    assert result.tier == "fixed"
    assert len(result.used) + result.rejected == 300
    assert max(abs(v) for v in _error_enu(result)) < 0.01


def test_without_rms_every_epoch_weighs_the_same():
    lines = [
        "lat_deg,lon_deg,alt_m",
        *[f"{TRUTH[0] + d:.9f},{TRUTH[1]:.9f},{TRUTH[2]:.4f}" for d in (-1e-7, 0, 1e-7, 0)],
    ]
    result = average(read_recording("\n".join(lines)), Settings(reject_threshold=0))
    assert not result.weighted_by_rms
    assert result.latitude_deg == pytest.approx(TRUTH[0], abs=1e-10)


def test_the_systematic_floor_is_added_on_top():
    recording = _recording([(300, 4, 0.005, 0.01, 0.0, 0.01, (0, 0, 0))])
    with_floor = average(recording, Settings(systematic_floor=True))
    assert with_floor.total.sigma_east == pytest.approx(
        math.hypot(with_floor.statistical.sigma_east, 0.008), rel=1e-9
    )


def test_rows_without_a_position_are_counted_not_averaged():
    text = HEADER + "\n" + _row(0, (0, 0, 0), 0.01, 4) + "\n2026-10-21T09:14:01+00:00,,,,,0,No solution\n" + _row(2, (0, 0, 0), 0.01, 4) + "\n"
    recording = read_recording(text)
    assert len(recording.fixes) == 2
    assert recording.rows_without_position == 1


def test_ecef_only_files_are_read():
    x, y, z = geodetic_to_ecef(*TRUTH)
    text = "ecef_x_m,ecef_y_m,ecef_z_m\n" + f"{x},{y},{z}\n" * 3
    result = average(read_recording(text), Settings(reject_threshold=0))
    assert result.height_m == pytest.approx(TRUTH[2], abs=1e-4)


def test_a_file_without_position_columns_says_so():
    with pytest.raises(RecordingError, match="no position columns"):
        read_recording("host_time_utc,sv_total\n2026-10-21T09:14:00+00:00,12\n")


def test_autocorrelation_time_of_white_noise_is_about_one():
    rng = random.Random(5)
    assert integrated_autocorrelation_time([rng.gauss(0, 1) for _ in range(2000)]) < 1.3


def test_circular_accuracy_figures():
    """For a circular normal error the ellipse is a circle, CEP50 is
    1.1774σ, the 95% radius 2.4477σ and 2DRMS 2√2σ."""
    a = Accuracy.from_covariance([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 4.0]])
    assert a.semi_major == pytest.approx(1.0) and a.semi_minor == pytest.approx(1.0)
    assert a.cep50 == pytest.approx(1.1774, abs=1e-3)
    assert a.horizontal95 == pytest.approx(2.4477, abs=1e-4)
    assert a.drms2 == pytest.approx(2 * math.sqrt(2))
    assert a.vertical95 == pytest.approx(3.92)


def test_ellipse_azimuth_is_clockwise_from_north():
    north_south = Accuracy.from_covariance([[1.0, 0, 0], [0, 9.0, 0], [0, 0, 1.0]])
    assert north_south.azimuth_deg == pytest.approx(0.0, abs=1e-9)
    east_west = Accuracy.from_covariance([[9.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]])
    assert east_west.azimuth_deg == pytest.approx(90.0)


@pytest.mark.parametrize(
    ("dof", "exact"),
    [(1, 12.7062), (2, 4.3027), (5, 2.5706), (6, 2.4469), (10, 2.2281), (30, 2.0423), (1000, 1.9623)],
)
def test_student_t_quantile(dof, exact):
    from averaging.estimator import student_t_975

    assert student_t_975(dof) == pytest.approx(exact, abs=1e-3)


def test_plenty_of_data_needs_no_small_sample_widening():
    from averaging.estimator import small_sample_factor

    assert small_sample_factor(5000) == pytest.approx(1.0, abs=2e-3)
    assert small_sample_factor(6) > 1.5


def test_the_95_percent_figure_covers_95_percent_with_few_effective_epochs():
    """The case the small-sample correction exists for: a strongly
    correlated recording only a few correlation times long. Before it, the
    stated 95% radius held about three times in four."""
    rng = random.Random(2026)
    hits = 0
    trials = 80
    for trial in range(trials):
        recording = _recording([(300, 1, 0.8, 1.5, 0.97, 1.6, (0, 0, 0))], seed=rng.randrange(1 << 30))
        result = average(recording, Settings(systematic_floor=False))
        error = _error_enu(result)
        hits += math.hypot(error[0], error[1]) <= result.statistical.horizontal95
    assert hits / trials >= 0.85

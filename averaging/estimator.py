"""The best single position a recording supports, and how well it is known.

A static receiver logging for ten minutes writes six hundred positions,
none of which is the answer. Averaging them is the obvious move, and done
naively it gets three things wrong. This module is the version that gets
them right.

**Not every epoch is worth the same.** Each [PG] carries the receiver's own
error estimate, ``pos_rms_m``, and an epoch the receiver says is good to a
centimetre should count for more than one it says is good to a metre. So
each epoch is weighted by ``1/σ²``, which is the minimum-variance
combination when the σ are right. They are floored at a fifth of their
median, because a receiver that reports one epoch as unrealistically good
would otherwise have that epoch decide the answer on its own.

**Some epochs are wrong.** A multipath jump or a momentary loss of the fix
lands an epoch decimetres away while the receiver still reports a small σ.
The mean is therefore made robust: iteratively reweighted with Huber's
function, so that an epoch far from the rest is listened to less, and
dropped altogether beyond a threshold. Both the weighting and the
threshold are measured against the scatter the recording actually shows,
not against the σ the receiver claimed - the two often disagree by a
factor of two, and the threshold must not depend on which one is right.

**Neighbouring epochs are not independent.** This is the one that matters
most and is most often missed. Ten minutes at 1 Hz is not six hundred
independent looks at the point: the multipath and the atmosphere that put
one epoch a centimetre north put the next one there too. The textbook
σ/√N assumes independence and is optimistic by a factor of three to five
on a typical static recording. Here the autocorrelation of the residuals
is measured on each axis, its integrated time τ gives the number of
effectively independent epochs ``N/τ``, and the variance of the mean is
scaled up by τ to match.

What averaging cannot do is remove a bias. An RTK position is only as good
as the base coordinate behind it, and a standalone one carries
atmospheric errors that do not average out over minutes. So the estimate
can optionally add a typical systematic floor for the kind of solution
that was averaged. That floor is a published-practice figure, not a
measurement, and it is reported separately so the two can be told apart.

Everything is done in local East/North/Up around the median epoch, never
by averaging latitudes and longitudes directly. The arithmetic is plain
Python: three-by-three matrices do not need numpy, and the executable is
built without it.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from greis.epoch import SOL_TYPE_LABELS
from averaging.noise_model import AxisFit, fit_axis
from greis.geodesy import ecef_to_geodetic, enu_rotation, geodetic_to_ecef

Matrix = list[list[float]]


# --- solution types ---------------------------------------------------------


@dataclass(frozen=True)
class Tier:
    key: str
    rank: int
    name: str
    codes: tuple[int, ...]


TIERS: tuple[Tier, ...] = (
    Tier("fixed", 4, "Fixed (RTK / PPP)", (4, 8)),
    Tier("float", 3, "Float (RTK / PPP)", (3, 7)),
    Tier("dgnss", 2, "Differential / SBAS", (2, 5)),
    Tier("single", 1, "Standalone", (1,)),
    Tier("unknown", 0, "Unknown", ()),
)
"""Solution types grouped by what they are worth. Fixed beats float beats
differential beats standalone; RTK and PPP of the same kind sit together
because which of them produced a fix says little about how good it is."""

_TIER_BY_KEY = {tier.key: tier for tier in TIERS}

EXCLUDED_SOLUTIONS = frozenset({0, 6})
"""No solution, and dead reckoning: neither is a measurement of where the
antenna is."""

SYSTEMATIC_FLOOR_M: dict[int | None, tuple[float, float]] = {
    4: (0.008, 0.015),
    8: (0.02, 0.04),
    3: (0.10, 0.20),
    7: (0.08, 0.15),
    2: (0.30, 0.60),
    5: (0.40, 0.70),
    1: (0.80, 1.50),
    None: (0.80, 1.50),
}
"""Typical systematic error, one σ per axis as (horizontal, vertical), that
averaging does not remove. Rule-of-thumb values from survey practice, not
from this recording; an unknown solution type is treated as standalone."""


def tier_of(sol_type: int | None) -> Tier:
    for tier in TIERS:
        if sol_type in tier.codes:
            return tier
    return _TIER_BY_KEY["unknown"]


# --- reading a recording -------------------------------------------------------


@dataclass
class Fix:
    """One epoch of a recording, as far as averaging is concerned."""

    ecef: tuple[float, float, float]
    rms_m: float | None
    sol_type: int | None
    time: datetime | None = None

    # Filled in by the estimator.
    enu: tuple[float, float, float] = (0.0, 0.0, 0.0)
    sigma: float = 1.0
    huber: float = 1.0
    weight: float = 0.0
    """Normalised so that the weights of the epochs used sum to one."""
    selected: bool = False
    """Of the solution type being averaged."""
    rejected: bool = False
    session: int = 0
    """Which file it came from, when several recordings of the same point
    are averaged together."""


@dataclass
class Recording:
    fixes: list[Fix]
    rows_without_position: int
    sources: list[str] = field(default_factory=list)


class RecordingError(ValueError):
    """A file that cannot be averaged, with a reason fit to show."""


_ALIASES = {
    "lat": ("lat_deg", "lat", "latitude"),
    "lon": ("lon_deg", "lon", "lng", "longitude"),
    "height": ("alt_m", "height_m", "alt", "height"),
    "lat_rad": ("lat_rad",),
    "lon_rad": ("lon_rad",),
    "x": ("ecef_x_m",),
    "y": ("ecef_y_m",),
    "z": ("ecef_z_m",),
    "rms": ("pos_rms_m",),
    "sol": ("sol_type",),
    "sol_label": ("sol_type_label",),
    "time": ("host_time_utc", "rx_datetime_utc"),
}
_LABEL_TO_CODE = {label.lower(): code for code, label in SOL_TYPE_LABELS.items()}


def read_recording(text: str, source: str = "") -> Recording:
    """Every epoch with a position, from the text of one CSV.

    Built for the files :mod:`recording.csv_writer` writes, so it finds its
    columns by name rather than by position - the header changes with what
    was ticked. Position comes from latitude/longitude/height if they are
    there, from the radian columns if only those are, and from ECEF last.
    A row with an empty position is counted and skipped: it is the receiver
    saying it had no solution, which is a fact about the session rather
    than an error in the file.
    """
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = [name.strip().lower() for name in next(reader)]
    except StopIteration:
        raise RecordingError(f"{source or 'The file'} is empty.") from None

    column: dict[str, int] = {}
    for key, names in _ALIASES.items():
        for index, name in enumerate(header):
            if name in names:
                column[key] = index
                break

    has_degrees = {"lat", "lon", "height"} <= column.keys()
    has_radians = {"lat_rad", "lon_rad", "height"} <= column.keys()
    has_ecef = {"x", "y", "z"} <= column.keys()
    if not (has_degrees or has_radians or has_ecef):
        raise RecordingError(
            f"{source or 'The file'} has no position columns. "
            "Averaging needs lat_deg, lon_deg and alt_m, or ecef_x_m, ecef_y_m and ecef_z_m."
        )

    def number(row: Sequence[str], key: str) -> float | None:
        index = column.get(key)
        if index is None or index >= len(row) or not row[index].strip():
            return None
        try:
            value = float(row[index])
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    fixes: list[Fix] = []
    without = 0
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        ecef: tuple[float, float, float] | None = None
        lat, lon, height = number(row, "lat"), number(row, "lon"), number(row, "height")
        if has_degrees and None not in (lat, lon, height):
            ecef = geodetic_to_ecef(lat, lon, height)  # type: ignore[arg-type]
        elif has_radians and None not in (number(row, "lat_rad"), number(row, "lon_rad"), height):
            ecef = geodetic_to_ecef(
                math.degrees(number(row, "lat_rad")),  # type: ignore[arg-type]
                math.degrees(number(row, "lon_rad")),  # type: ignore[arg-type]
                height,  # type: ignore[arg-type]
            )
        elif has_ecef and None not in (number(row, "x"), number(row, "y"), number(row, "z")):
            ecef = (number(row, "x"), number(row, "y"), number(row, "z"))  # type: ignore[assignment]

        # A position inside the earth is a receiver that has not solved yet
        # and reported zeros, not a place.
        if ecef is None or math.hypot(*ecef) < 6.0e6:
            without += 1
            continue

        sol = number(row, "sol")
        sol_type = int(sol) if sol is not None else None
        if sol_type is None and "sol_label" in column and column["sol_label"] < len(row):
            sol_type = _LABEL_TO_CODE.get(row[column["sol_label"]].strip().lower())

        time = None
        if "time" in column and column["time"] < len(row) and row[column["time"]].strip():
            try:
                time = datetime.fromisoformat(row[column["time"]].strip().replace("Z", "+00:00"))
            except ValueError:
                time = None

        rms = number(row, "rms")
        fixes.append(Fix(ecef=ecef, rms_m=rms if rms is not None and rms > 0 else None, sol_type=sol_type, time=time))

    return Recording(fixes, without, [source] if source else [])


def read_recordings(paths: Iterable[Path]) -> Recording:
    """Several files of the same point, as one recording."""
    fixes: list[Fix] = []
    without = 0
    sources: list[str] = []
    for session, path in enumerate(paths):
        part = read_recording(Path(path).read_text(encoding="utf-8-sig"), Path(path).name)
        for fix in part.fixes:
            fix.session = session
        fixes.extend(part.fixes)
        without += part.rows_without_position
        sources.append(Path(path).name)
    return Recording(fixes, without, sources)


# --- settings and result ----------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    solutions: str = "auto"
    """``auto`` takes only the best tier with at least ten epochs; ``all``
    takes everything and lets the weights decide; a tier key takes that
    tier and every better one."""
    weight_by_rms: bool = True
    reject_threshold: float = 3.0
    """In units of the recording's own scatter. 0 turns rejection off."""
    correct_for_correlation: bool = True
    systematic_floor: bool = True


HUBER_K = 1.6
"""Where Huber's weight starts to fall. For isotropic 3-D noise the
normalised distance used here has its 90th percentile near 1.6, so the
bulk of a clean recording keeps full weight."""

MEDIAN_OF_NORMALISED_DISTANCE = 0.888
"""The median of ``|r| / σ`` when each axis has σ/√3 of normal noise: the
square root of the χ²₃ median over 3. Dividing the observed median by it
gives the recording's scatter in units of the receiver's claim."""

MINIMUM_EPOCHS_FOR_AUTO = 10

SOKAL_WINDOW = 5.0


@dataclass(frozen=True)
class Accuracy:
    """Error statistics for one 3×3 East/North/Up covariance."""

    sigma_east: float
    sigma_north: float
    sigma_up: float
    semi_major: float
    semi_minor: float
    azimuth_deg: float
    """Of the horizontal ellipse's long axis, clockwise from north."""
    cep50: float
    horizontal95: float
    drms2: float
    vertical95: float
    sep50: float

    @classmethod
    def from_covariance(cls, c: Matrix) -> Accuracy:
        a, b, d = c[0][0], c[0][1], c[1][1]
        spread = math.sqrt(((a - d) / 2.0) ** 2 + b * b)
        major = (a + d) / 2.0 + spread
        minor = max((a + d) / 2.0 - spread, 0.0)
        theta = 0.5 * math.atan2(2.0 * b, a - d)  # from east, anticlockwise
        se, sn, su = math.sqrt(a), math.sqrt(d), math.sqrt(c[2][2])
        return cls(
            sigma_east=se,
            sigma_north=sn,
            sigma_up=su,
            semi_major=math.sqrt(major),
            semi_minor=math.sqrt(minor),
            azimuth_deg=(90.0 - math.degrees(theta)) % 180.0,
            # The usual closed-form approximations, good while the ellipse
            # is not much more than three times longer than it is wide.
            cep50=0.5887 * (math.sqrt(major) + math.sqrt(minor)),
            horizontal95=2.4477 * math.sqrt((major + minor) / 2.0),
            drms2=2.0 * math.sqrt(major + minor),
            vertical95=1.96 * su,
            sep50=0.5127 * (se + sn + su),
        )


@dataclass
class Result:
    latitude_deg: float
    longitude_deg: float
    height_m: float
    ecef: tuple[float, float, float]
    rotation: tuple[tuple[float, float, float], ...]
    """ECEF to ENU at the reference point; its transpose goes back."""
    mean_enu: tuple[float, float, float]
    fixes: list[Fix]
    """Every epoch with a position, in file order, annotated."""
    used: list[Fix]
    tier: str
    total: Accuracy
    statistical: Accuracy
    single_epoch: Accuracy
    total_covariance: Matrix
    tau: tuple[float, float, float]
    small_sample_factor: tuple[float, float, float]
    """How much each axis's variance was widened for having few effective
    epochs; 1.0 when there were plenty."""
    noise_models: tuple[str, str, str]
    """The noise model maximum likelihood chose for each axis."""
    correlation_time_s: tuple[float, float, float]
    sessions: int
    between_session_sigma: tuple[float, float, float]
    """The scatter between separate recordings beyond what each one's own
    uncertainty explains; 0 for a single recording."""
    effective_epochs: float
    floor_m: tuple[float, float]
    scatter_ratio: float | None
    """Actual scatter over the receiver's claim; ``None`` when unweighted."""
    weighted_by_rms: bool
    median_rms_m: float | None

    @property
    def rejected(self) -> int:
        return sum(1 for fix in self.fixes if fix.selected and fix.rejected)

    @property
    def downweighted(self) -> int:
        return sum(1 for fix in self.used if fix.huber < 1.0)

    @property
    def span_s(self) -> float | None:
        times = [fix.time for fix in self.used if fix.time is not None]
        return (max(times) - min(times)).total_seconds() if len(times) > 1 else None

    def ecef_covariance(self) -> Matrix:
        """The total covariance turned into X/Y/Z: ``Rᵀ C R``."""
        r, c = self.rotation, self.total_covariance
        return [[sum(r[a][i] * c[a][b] * r[b][j] for a in range(3) for b in range(3)) for j in range(3)] for i in range(3)]


# --- the estimator --------------------------------------------------------------


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0


def integrated_autocorrelation_time(series: Sequence[float]) -> float:
    """τ = 1 + 2 Σ ρₖ, with the sum cut off before the noise in it.

    The estimated autocorrelation has a noisy tail that wanders either
    side of zero, and summing it in makes white noise look correlated.
    Two stopping rules keep it out, and whichever bites first wins. Geyer's
    initial monotone sequence takes the lags in pairs and stops at the
    first pair that is not positive, or not smaller than the pair before.
    Sokal's window stops once the lag is five times the τ found so far,
    which is long enough for a real correlation to have died away. On a
    short series the result is checked against an AR(1) fit, below.
    Capped between 1 and the length of the series.
    """
    n = len(series)
    if n < 8:
        return 1.0
    mean = sum(series) / n
    d = [value - mean for value in series]
    c0 = sum(value * value for value in d) / n
    if c0 <= 0:
        return 1.0
    tau = 1.0
    previous = math.inf
    for k in range(1, n // 3, 2):
        if k >= SOKAL_WINDOW * tau:
            break
        ck = sum(d[i] * d[i + k] for i in range(n - k))
        ck1 = sum(d[i] * d[i + k + 1] for i in range(n - k - 1))
        pair = min((ck + ck1) / n / c0, previous)
        if pair <= 0:
            break
        tau += 2.0 * pair
        previous = pair

    # The sum above is biased low when the series is only a few τ long:
    # subtracting the sample mean takes the slow part of the correlation
    # with it. The lag-one correlation fitted as an AR(1) process, with
    # Marriott and Pope's correction for the same bias, holds up much
    # better there, so the larger of the two is used. On a long series
    # they agree and nothing changes.
    rho = sum(d[i] * d[i + 1] for i in range(n - 1)) / n / c0
    rho = min(rho + (1.0 + 4.0 * rho) / n, 0.9999)
    if rho > 0:
        tau = max(tau, (1.0 + rho) / (1.0 - rho))
    return min(max(tau, 1.0), float(n))


_T975_SMALL = (12.706, 4.303, 3.182, 2.776, 2.571)
"""Student's t at 97.5% for 1 to 5 degrees of freedom, where the series
below is not yet accurate."""


def student_t_975(dof: float) -> float:
    """The two-sided 95% quantile of Student's t.

    Exact from the table up to five degrees of freedom; beyond that the
    Cornish-Fisher expansion about the normal quantile, which is within
    0.001 of the exact value from six upwards. Fractional degrees of
    freedom are allowed, because N/τ is rarely a whole number.
    """
    dof = max(dof, 1.0)
    if dof <= 5.0:
        low = int(math.floor(dof))
        if low >= 5:
            return _T975_SMALL[4]
        fraction = dof - low
        return _T975_SMALL[low - 1] + fraction * (_T975_SMALL[low] - _T975_SMALL[low - 1])
    z = 1.959963984540054
    return (
        z
        + (z**3 + z) / (4 * dof)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * dof**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * dof**3)
        + (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / (92160 * dof**4)
    )


def small_sample_factor(effective_epochs: float) -> float:
    """``(t / z)²`` for N/τ - 1 degrees of freedom: the factor on a variance
    that turns a normal 95% interval into a Student one."""
    return (student_t_975(effective_epochs - 1.0) / 1.959963984540054) ** 2


def _choose_tier(fixes: Sequence[Fix], settings: Settings) -> tuple[str, set[str]]:
    counts: dict[str, int] = {}
    for fix in fixes:
        key = tier_of(fix.sol_type).key
        counts[key] = counts.get(key, 0) + 1

    if settings.solutions == "all":
        return "all", set(counts)
    if settings.solutions == "auto":
        need = min(MINIMUM_EPOCHS_FOR_AUTO, len(fixes))
        ranked = sorted(counts, key=lambda key: _TIER_BY_KEY[key].rank, reverse=True)
        best = next((key for key in ranked if counts[key] >= need), ranked[-1])
        return best, {best}
    floor_rank = _TIER_BY_KEY[settings.solutions].rank
    return settings.solutions, {key for key in counts if _TIER_BY_KEY[key].rank >= floor_rank}


def average(recording: Recording, settings: Settings = Settings()) -> Result:
    """The weighted, robust, correlation-aware mean of a recording."""
    fixes = [fix for fix in recording.fixes if fix.sol_type not in EXCLUDED_SOLUTIONS]
    if not fixes:
        raise RecordingError("The recording has no epochs with a position solution.")

    tier, keys = _choose_tier(fixes, settings)
    for fix in fixes:
        fix.selected = tier_of(fix.sol_type).key in keys
        fix.rejected = False
        fix.huber = 1.0
        fix.weight = 0.0
    selected = [fix for fix in fixes if fix.selected]
    if len(selected) < 2:
        raise RecordingError("Fewer than two epochs are left after filtering by solution type. Try including all of them.")

    reference = tuple(_median([fix.ecef[k] for fix in selected]) for k in range(3))
    lat0, lon0, _ = ecef_to_geodetic(*reference)
    rotation = enu_rotation(lat0, lon0)
    for fix in fixes:
        d = [fix.ecef[k] - reference[k] for k in range(3)]
        fix.enu = tuple(sum(row[k] * d[k] for k in range(3)) for row in rotation)  # type: ignore[assignment]

    with_rms = [fix.rms_m for fix in selected if fix.rms_m]
    weighted = settings.weight_by_rms and len(with_rms) >= len(selected) / 2
    median_rms = _median(with_rms) if with_rms else None
    for fix in selected:
        if weighted:
            floor = max(0.001, 0.2 * median_rms)  # type: ignore[operator]
            fix.sigma = max(fix.rms_m or median_rms, floor)  # type: ignore[type-var]
        else:
            fix.sigma = 1.0

    mean = [0.0, 0.0, 0.0]
    scale = 1.0
    for iteration in range(50):
        total = 0.0
        step = [0.0, 0.0, 0.0]
        for fix in selected:
            if fix.rejected:
                continue
            w = fix.huber / (fix.sigma * fix.sigma)
            total += w
            for k in range(3):
                step[k] += w * fix.enu[k]
        step = [value / total for value in step]
        shift = math.dist(step, mean)
        mean = step

        distance = {id(fix): math.dist(fix.enu, mean) / fix.sigma for fix in selected}
        scale = max(_median(list(distance.values())) / MEDIAN_OF_NORMALISED_DISTANCE, 1e-12)
        for fix in selected:
            z = distance[id(fix)] / scale
            fix.rejected = settings.reject_threshold > 0 and z > settings.reject_threshold
            fix.huber = 1.0 if z <= HUBER_K else HUBER_K / z
        if iteration and shift < 1e-6:
            break

    used = [fix for fix in selected if not fix.rejected]
    if len(used) < 2:
        raise RecordingError("Almost every epoch was rejected. Try a more lenient threshold.")

    raw = [fix.huber / (fix.sigma * fix.sigma) for fix in used]
    weight_sum = sum(raw)
    for fix, w in zip(used, raw):
        fix.weight = w / weight_sum

    residuals = [[fix.enu[k] - mean[k] for k in range(3)] for fix in used]
    n = len(used)
    bessel = n / (n - 1)
    of_mean: Matrix = [[0.0] * 3 for _ in range(3)]
    one_epoch: Matrix = [[0.0] * 3 for _ in range(3)]
    for fix, r in zip(used, residuals):
        for a in range(3):
            for b in range(3):
                # The sandwich form: the variance of a weighted mean taken
                # from the residuals themselves, so it is right whether or
                # not the receiver's σ were.
                of_mean[a][b] += fix.weight * fix.weight * r[a] * r[b] * bessel
                one_epoch[a][b] += fix.weight * r[a] * r[b] * bessel

    fits: list[list[AxisFit]] = [[], [], []]
    between = [0.0, 0.0, 0.0]
    sessions = sorted({fix.session for fix in used})
    if settings.correct_for_correlation:
        # Each axis of each session gets its own noise model and GLS mean
        # (see averaging.noise_model); sessions are then combined below.
        variances = [0.0, 0.0, 0.0]
        for k in range(3):
            means, session_variances = [], []
            for session in sessions:
                members = [fix for fix in used if fix.session == session]
                if len(members) < 2:
                    continue
                fit = fit_axis(
                    [fix.enu[k] for fix in members],
                    [fix.sigma * fix.sigma / fix.huber for fix in members],
                    _seconds(members),
                )
                fits[k].append(fit)
                means.append(fit.mean)
                # Student's t on the session's own effective epochs, so a
                # short or strongly correlated session is not over-trusted.
                session_variances.append(fit.variance_of_mean * small_sample_factor(fit.effective_epochs))
            mean[k], variances[k], between[k] = _combine_sessions(means, session_variances)
        tau = tuple(len(used) / max(sum(fit.effective_epochs for fit in fits[k]), 1.0) for k in range(3))
        small_sample = tuple(max(small_sample_factor(fit.effective_epochs) for fit in fits[k]) for k in range(3))
        # The noise model is fitted axis by axis; the correlation between
        # axes is kept from the residuals, which is all it is needed for -
        # the tilt of the error ellipse.
        sandwich = [of_mean[a][a] for a in range(3)]
        for a in range(3):
            for b in range(3):
                if a == b:
                    of_mean[a][b] = variances[a]
                else:
                    rho = of_mean[a][b] / math.sqrt(sandwich[a] * sandwich[b]) if sandwich[a] * sandwich[b] > 0 else 0.0
                    of_mean[a][b] = rho * math.sqrt(variances[a] * variances[b])
    else:
        tau = (1.0, 1.0, 1.0)
        small_sample = (1.0, 1.0, 1.0)

    floor_h2 = floor_v2 = 0.0
    for fix in used:
        horizontal, vertical = SYSTEMATIC_FLOOR_M.get(fix.sol_type, SYSTEMATIC_FLOOR_M[None])
        floor_h2 += fix.weight * horizontal * horizontal
        floor_v2 += fix.weight * vertical * vertical
    total = [row[:] for row in of_mean]
    if settings.systematic_floor:
        total[0][0] += floor_h2
        total[1][1] += floor_h2
        total[2][2] += floor_v2

    ecef = tuple(reference[k] + sum(rotation[a][k] * mean[a] for a in range(3)) for k in range(3))
    latitude, longitude, height = ecef_to_geodetic(*ecef)

    return Result(
        latitude_deg=latitude,
        longitude_deg=longitude,
        height_m=height,
        ecef=ecef,  # type: ignore[arg-type]
        rotation=rotation,
        mean_enu=tuple(mean),  # type: ignore[arg-type]
        fixes=fixes,
        used=used,
        tier=tier,
        total=Accuracy.from_covariance(total),
        statistical=Accuracy.from_covariance(of_mean),
        single_epoch=Accuracy.from_covariance(one_epoch),
        total_covariance=total,
        tau=tau,  # type: ignore[arg-type]
        small_sample_factor=small_sample,  # type: ignore[arg-type]
        noise_models=tuple(  # type: ignore[arg-type]
            "/".join(sorted({fit.model for fit in fits[k]})) if fits[k] else "independent" for k in range(3)
        ),
        correlation_time_s=tuple(max((f.correlation_time_s for f in fits[k]), default=0.0) for k in range(3)),  # type: ignore[arg-type]
        sessions=len(sessions),
        between_session_sigma=tuple(between),  # type: ignore[arg-type]
        effective_epochs=n / (sum(tau) / 3.0),
        floor_m=(math.sqrt(floor_h2), math.sqrt(floor_v2)),
        scatter_ratio=scale if weighted else None,
        weighted_by_rms=weighted,
        median_rms_m=median_rms,
    )


def _seconds(fixes: Sequence[Fix]) -> list[float]:
    """Epoch times in seconds from the first, or the epoch index when the
    file has no usable time column. Never decreasing, so that a clock
    step backwards cannot make a negative interval."""
    if all(fix.time is not None for fix in fixes):
        start = fixes[0].time
        out = [(fix.time - start).total_seconds() for fix in fixes]  # type: ignore[operator]
    else:
        out = [float(i) for i in range(len(fixes))]
    for i in range(1, len(out)):
        out[i] = max(out[i], out[i - 1] + 1e-3)
    return out


def _combine_sessions(means: list[float], variances: list[float]) -> tuple[float, float, float]:
    """Several recordings of one point as a random-effects mean.

    Each session's mean is good to its own variance, but two recordings
    hours apart also differ by what neither could see from inside itself:
    multipath that repeats with the satellites, a slowly changing
    atmosphere. That extra between-session variance is estimated with
    DerSimonian and Laird's moment estimator and added to every session
    before they are weighted together. One session returns as it came.

    Two sessions can show that they disagree but not by how much in
    general, so a pair that disagrees comes back with a wide interval.
    That is the honest answer; a third session narrows it.
    """
    if len(means) == 1:
        return means[0], variances[0], 0.0
    weights = [1.0 / v for v in variances]
    total = sum(weights)
    fixed = sum(w * m for w, m in zip(weights, means)) / total
    q = sum(w * (m - fixed) ** 2 for w, m in zip(weights, means))
    denominator = total - sum(w * w for w in weights) / total
    tau2 = max(0.0, (q - (len(means) - 1)) / denominator) if denominator > 0 else 0.0
    weights = [1.0 / (v + tau2) for v in variances]
    total = sum(weights)
    mean = sum(w * m for w, m in zip(weights, means)) / total
    variance = 1.0 / total
    if tau2 > 0:
        # With a handful of sessions the between-session variance is itself
        # a rough guess. Hartung-Knapp-Sidik-Jonkman's correction - the
        # scatter of the sessions about the mean, with Student's t on k - 1
        # degrees of freedom - keeps the 95% interval near 95% (simulated:
        # 92-97% for three to six sessions, against 72-90% without). It is
        # applied only when the sessions disagree; when they agree it would
        # widen the answer for nothing.
        k = len(means)
        knapp = sum(w * (m - mean) ** 2 for w, m in zip(weights, means)) / ((k - 1) * total)
        variance = max(variance, knapp) * (student_t_975(k - 1) / 1.959963984540054) ** 2
    return mean, variance, math.sqrt(tau2)


def tier_name(key: str) -> str:
    return "All solution types" if key == "all" else _TIER_BY_KEY[key].name

"""Forecast GTA VI trophy earn rates on Exophase, one case at a time.

The case gives every read the house made of the game's trophy page before its
checkpoint: for each of the game's trophies, the percent of the players
Exophase tracks who have earned it, plus the players-tracked count. The job
is to say, for every trophy, five quantiles of its earn rate at the case's
target, scored by pinball loss on the logit scale.

How this forecaster thinks about the data:

Exophase does not update its counts continuously. It refreshes a game's page
on its own schedule, hours apart, so between refreshes every read is the same
page, and at a refresh the tracked-player count jumps and most rates fall:
the newly tracked players have had less time to play, so they hold fewer
trophies. A rate is therefore a ratio of two growing counts, the earners and
the tracked players, sampled only at refresh moments.

The program works in logit space, the scale the loss is scored on, and fits,
per trophy, a straight line of logit(rate) against log time since the release
day began, over the distinct reads only (identical consecutive reads carry no
new information and would overweight long flat stretches). Slopes are shrunk
toward the median slope across trophies, because a single trophy's few
refreshes pin its slope down poorly; residuals are pooled the same way. The
median at the target is the shrunk line read at the target, held within two
logit units of the trophy's last rate so a wild extrapolation cannot run
away. The spread combines the fit's residual scatter, the slope's standard
error times the leverage out to the target, and a floor that grows with the
horizon, so a far-off target honestly admits more doubt.

When the history holds only one distinct read there is no trend to fit. The
page has then been stale since that read, and the next refresh is expected to
dilute the rates, so the median is shifted down by an allowance that grows
with the horizon and with how long the page has been stale, and the spread is
set wide. When a trophy never appears in any read, it gets a neutral median
with a wide spread. Whatever happens, every case still writes a valid
forecast: a failure in one case falls back to the trophy's last rate with the
persistence spread, never to a crash.

Standard library only, no network, no randomness: two runs on the same case
write the same bytes.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# The grader's -P leaves this file's directory off sys.path; put it back first
# so a helper module shipped beside this file can be imported.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

# The quantile levels the contest scores, in the order each row lists them.
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]

# The standard normal's quantile at each scored level, exact to machine
# precision (the usual three-decimal values are -1.645 and -0.674).
Z_STD = {
    0.05: -1.6448536269514722,
    0.25: -0.6744897501960817,
    0.5: 0.0,
    0.75: 0.6744897501960817,
    0.95: 1.6448536269514722,
}

# The release day begins here; time is measured in hours from this instant,
# the same origin the house uses for its trend fits.
RELEASE = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)

# A rate is clamped to this percent range before any logit, as the scorer
# clamps it (the page prints two decimals; 0 and 100 have no logit).
LO = 0.01
HI = 99.99
LOGIT_LO = math.log(LO / (100.0 - LO))
LOGIT_HI = math.log(HI / (100.0 - HI))

# How far the median may move from the trophy's last rate, in logit units.
MEDIAN_SHIFT_CAP = 2.0
# The spread never exceeds this, in logit units.
SPREAD_CAP = 2.5
# How strongly a trophy's own slope is pulled toward the pooled slope.
SHRINK_K = 3.0
# Recency half-life for the trend fit, in hours.
HALF_LIFE_HOURS = 24.0


def clamp_rate(pct):
    """A percent forced into the range the logit is defined on."""
    return min(HI, max(LO, pct))


def logit(pct):
    """Logit of a percent, the scale the loss is scored on."""
    p = clamp_rate(pct)
    return math.log(p / (100.0 - p))


def inv_logit(y):
    """A logit back to a percent."""
    y = min(LOGIT_HI, max(LOGIT_LO, y))
    return 100.0 / (1.0 + math.exp(-y))


def round4(x):
    """x rounded to 4 decimals, half up, as JavaScript rounds it.

    A whole number comes back as an int, so the JSON writes 100 rather
    than 100.0.
    """
    whole = math.floor(x * 10000 + 0.5)
    value = whole / 10000
    return int(value) if value.is_integer() else value


def hours_since_release(when):
    """Hours from the release day's start to an ISO 8601 UTC timestamp."""
    stamp = when.replace("Z", "+00:00")
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment - RELEASE).total_seconds() / 3600.0


def _norm_cdf(x):
    """The standard normal's cumulative distribution, via erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def z_for_level(level):
    """The standard normal's quantile at a level in (0, 1).

    Exact values for the five scored levels; any other level is found by
    bisection on the CDF, which needs no remembered constants.
    """
    if level in Z_STD:
        return Z_STD[level]
    if level <= 0.0:
        return -8.0
    if level >= 1.0:
        return 8.0
    low, high = -8.0, 8.0
    for _ in range(80):
        mid = 0.5 * (low + high)
        if _norm_cdf(mid) < level:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def distinct_reads(case):
    """The case's history with flat stretches collapsed to one read each.

    Exophase's page sits unchanged between refreshes, so consecutive reads
    with the same tracked count and the same rates are one observation, not
    several. Each kept read carries its log-time coordinate, its weight
    (recent reads count more; the weight halves every HALF_LIFE_HOURS),
    the tracked count and the rates.
    """
    history = case.get("history") or []
    if not history:
        return []
    last_slot = hours_since_release(history[-1]["slot"])
    kept = []
    seen = None
    for read in history:
        key = (read.get("players_tracked"), tuple(read.get("rates") or ()))
        if key == seen:
            continue
        seen = key
        slot = hours_since_release(read["slot"])
        x = math.log(max(1.0 + slot, 0.05))
        age = max(0.0, last_slot - slot)
        weight = 0.5 ** (age / HALF_LIFE_HOURS)
        kept.append({
            "x": x,
            "slot": slot,
            "weight": weight,
            "players": read.get("players_tracked"),
            "rates": read.get("rates") or [],
        })
    return kept


def fit_line(points):
    """A weighted least-squares line through (x, y, weight) points.

    Returns the intercept, slope, residual variance, slope standard error
    and weighted mean of x, or None when the points cannot define a line.
    The residual variance is the weighted mean squared residual, scaled by
    n / (n - 2) for the two fitted parameters; with fewer than three
    points it is zero, and the caller supplies a pooled value instead.
    """
    n = len(points)
    if n < 2:
        return None
    total_w = sum(p[2] for p in points)
    if total_w <= 0:
        return None
    mean_x = sum(p[2] * p[0] for p in points) / total_w
    mean_y = sum(p[2] * p[1] for p in points) / total_w
    sxx = sum(p[2] * (p[0] - mean_x) ** 2 for p in points)
    if sxx <= 1e-12:
        return None
    sxy = sum(p[2] * (p[0] - mean_x) * (p[1] - mean_y) for p in points)
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    if n > 2:
        resid = sum(
            p[2] * (p[1] - (intercept + slope * p[0])) ** 2 for p in points
        )
        resid_var = resid / total_w * n / (n - 2)
    else:
        resid_var = 0.0
    slope_se = math.sqrt(resid_var / sxx) if resid_var > 0 else 0.0
    return {
        "intercept": intercept,
        "slope": slope,
        "resid_var": resid_var,
        "slope_se": slope_se,
        "mean_x": mean_x,
        "n": n,
    }


def median_of(values):
    """The median of a non-empty list of numbers."""
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def forecast_trophy(points, last_rate, levels, horizon_days, stale_hours):
    """Five quantiles for a trophy with too little history to fit a line.

    points are the distinct reads where this trophy has a rate, oldest
    first; last_rate is its rate in the case's last read (or None);
    levels are the quantile levels the row must list. With
    no points it answers a neutral median with a wide spread; with one
    point it shifts the median down for the refresh dilution the stale
    page is holding back. The caller handles trophies with a real fit.
    """
    if not points:
        # No read ever listed this trophy: a neutral median, wide spread.
        mid = logit(1.0)
        spread = 2.0
        return [round4(inv_logit(mid + z_for_level(t) * spread)) for t in levels]
    if len(points) == 1:
        # One distinct read holds no trend. The page has been stale since
        # it, so the next refresh is expected to dilute the rates: shift
        # the median down by an allowance that grows with the horizon and
        # with the staleness, and keep the spread wide. A trophy every
        # tracked player already holds is barely dilutable (new players
        # earn it at once too), so the shift tapers to nothing at 100%.
        base = last_rate if last_rate is not None else 0.0
        taper = 1.0 - clamp_rate(base) / 100.0
        shift = -(0.75 * math.sqrt(horizon_days)
                  + 0.5 * math.log1p(stale_hours / 3.0)) * taper
        mid = logit(base) + shift
        spread = 1.0
        return [round4(inv_logit(mid + z_for_level(t) * spread)) for t in levels]
    fit = fit_line(points)
    # With two or more points the line always exists (distinct reads sit
    # at distinct log-times); reaching here means it did not, so fall back
    # to the last rate with a wide spread rather than crash.
    mid = logit(last_rate if last_rate is not None else 0.0)
    spread = 1.0
    return [round4(inv_logit(mid + z_for_level(t) * spread)) for t in levels]


def forecast_case(case):
    """The whole forecast for one case: one row per trophy."""
    trophies = case["trophies"]
    levels = case.get("levels") or LEVELS
    horizon_days = max(0.0, case["horizon_minutes"] / 1440.0)
    target_x = math.log(max(1.0 + hours_since_release(case["target"]), 0.05))
    reads = distinct_reads(case)

    # The last read's rates anchor the median clamp and the fallbacks.
    last_rates = reads[-1]["rates"] if reads else []
    stale_hours = 0.0
    if reads:
        checkpoint = hours_since_release(case["at"])
        stale_hours = max(0.0, checkpoint - reads[-1]["slot"])

    # One (x, logit rate) series per trophy, over the distinct reads.
    series = []
    for i in range(len(trophies)):
        points = []
        for read in reads:
            rate = read["rates"][i] if i < len(read["rates"]) else None
            if rate is None:
                continue
            points.append((read["x"], logit(rate), read["weight"]))
        series.append(points)

    # The pooled slope and residual scatter across trophies, for shrinkage.
    slopes = []
    resids = []
    fits = []
    for points in series:
        fit = fit_line(points) if len(points) >= 2 else None
        fits.append(fit)
        if fit is not None:
            slopes.append(fit["slope"])
            if fit["n"] >= 3 and fit["resid_var"] > 0:
                resids.append(fit["resid_var"])
    pooled_slope = median_of(slopes) if slopes else 0.0
    pooled_resid = median_of(resids) if resids else 0.09

    # A floor on the spread that grows with the horizon: a far-off target
    # honestly admits more doubt than the fit alone can see.
    floor = 0.30 + 0.10 * math.sqrt(horizon_days)

    rows = []
    for i, trophy in enumerate(trophies):
        points = series[i]
        last_rate = last_rates[i] if i < len(last_rates) else None
        if len(points) <= 1 or fits[i] is None:
            row = forecast_trophy(points, last_rate, levels,
                                  horizon_days, stale_hours)
            rows.append({"id": trophy["id"], "q": row})
            continue
        fit = fits[i]
        # Shrink this trophy's slope toward the pooled one, then refit the
        # level at the shrunk slope, so a noisy few-point slope cannot
        # drag the forecast far from what trophies do in general.
        n = fit["n"]
        slope = (n * fit["slope"] + SHRINK_K * pooled_slope) / (n + SHRINK_K)
        total_w = sum(p[2] for p in points)
        mean_x = sum(p[2] * p[0] for p in points) / total_w
        mean_y = sum(p[2] * p[1] for p in points) / total_w
        level = mean_y - slope * mean_x
        # The median at the target, held near the trophy's last rate.
        mid = level + slope * target_x
        anchor = logit(last_rate) if last_rate is not None else mid
        mid = min(anchor + MEDIAN_SHIFT_CAP,
                  max(anchor - MEDIAN_SHIFT_CAP, mid))
        mid = min(LOGIT_HI, max(LOGIT_LO, mid))
        # The spread: this trophy's residual scatter (or the pooled one
        # when it has too few points to judge), the slope's error times
        # the leverage out to the target, and the floor.
        resid_var = fit["resid_var"] if n >= 4 else pooled_resid
        sxx = sum(p[2] * (p[0] - mean_x) ** 2 for p in points)
        slope_se = math.sqrt(resid_var / sxx) if sxx > 1e-12 else 0.0
        lever = target_x - mean_x
        spread = math.sqrt(resid_var + (slope_se * lever) ** 2 + floor ** 2)
        spread = min(SPREAD_CAP, max(0.15, spread))
        quantiles = []
        prev = None
        for tau in levels:
            value = round4(inv_logit(mid + z_for_level(tau) * spread))
            value = min(100, max(0, value))
            if prev is not None and value < prev:
                value = prev
            quantiles.append(value)
            prev = value
        rows.append({"id": trophy["id"], "q": quantiles})
    return {"trophies": rows}


def fallback_rows(case):
    """The persistence forecast: every trophy stays at its last rate.

    Used when a case cannot be forecast at all, so the run still writes a
    valid file instead of failing. The spread widens like a random walk's
    on the logit scale, half a logit unit per square-root day.
    """
    levels = case.get("levels") or LEVELS
    horizon_days = max(0.0, case["horizon_minutes"] / 1440.0)
    spread = 0.5 * math.sqrt(horizon_days)
    reads = case.get("history") or []
    last = reads[-1] if reads else None
    rows = []
    for i, trophy in enumerate(case["trophies"]):
        rate = None
        if last is not None and i < len(last.get("rates") or []):
            rate = last["rates"][i]
        mid = logit(rate if rate is not None else 0.0)
        quantiles = []
        prev = None
        for tau in levels:
            value = round4(inv_logit(mid + z_for_level(tau) * spread))
            value = min(100, max(0, value))
            if prev is not None and value < prev:
                value = prev
            quantiles.append(value)
            prev = value
        rows.append({"id": trophy["id"], "q": quantiles})
    return {"trophies": rows}


def main():
    """Read every case in the input directory, write one forecast each."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Every case file, in name order, so two runs write the same bytes.
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        try:
            forecast = forecast_case(case)
        except Exception as exc:  # never leave a case without an output
            print(f"{path.name}: forecast failed ({exc}); "
                  f"writing the persistence fallback", file=sys.stderr)
            forecast = fallback_rows(case)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()

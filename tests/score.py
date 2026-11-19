"""The competition's scoring rule, written from its description.

Every (case, trophy, level) is one item. Its pinball loss is
2 * max(tau * d, (tau - 1) * d), where d = logit(y) - logit(q), y the
trophy's true earn rate in the answering read and q the forecast's
quantile at tau, both rates in percent clamped to [0.01, 99.99] before
the logit. The loss is the mean over every item; lower is better.
Typical miss is the mean |y - q50| in percentage points, and 90%
coverage is the share of trophies with q05 <= y <= q95. Both are
information only; the loss ranks.
"""

import json
import math

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]


def _clamp(pct):
    return min(99.99, max(0.01, pct))


def _logit(pct):
    p = _clamp(pct)
    return math.log(p / (100.0 - p))


def score(forecast, truth):
    """forecast: {id: [q05, q25, q50, q75, q95]}; truth: {id: rate}.

    Returns (loss, typical_miss_pp, coverage_90).
    """
    items = []
    misses = []
    covered = 0
    for tid, rate in truth.items():
        row = forecast[tid]
        y = _logit(rate)
        misses.append(abs(rate - row[2]))
        if row[0] <= rate <= row[4]:
            covered += 1
        for tau, q in zip(LEVELS, row):
            d = y - _logit(q)
            items.append(2.0 * max(tau * d, (tau - 1.0) * d))
    loss = sum(items) / len(items)
    return loss, sum(misses) / len(misses), covered / len(truth)


def load_forecast(path):
    """An .out file into {trophy id: [five quantiles]}."""
    doc = json.load(open(path))
    return {row["id"]: row["q"] for row in doc["trophies"]}

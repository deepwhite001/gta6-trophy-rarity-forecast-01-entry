"""Synthetic truths for testing the entry, built only from facts the kit
documents, so the program can be scored without the server.

Practice answers: the kit says Exophase refreshed between its 04:00Z and
04:30Z reads on launch night, taking the tracked count from 638 to 3,868
players and trophies earned from 943 to 4,276, with every rate but the one
at 100% falling. That pins the new earns at 3,333. The 100% trophy is the
starter every new player earns at once, so it takes most of them; what is
left spreads over the other trophies. Three variants span how the leftover
is shared:

- "spread": the starter takes 99% of the new players; the rest spreads
  over the other trophies in proportion to their old earner counts.
- "concentrated": the starter takes all new players; the rest goes only
  to the trophies already above 1.1%, in proportion to their old counts.
- "generous": the starter takes 95% of the new players; the rest spreads
  over all other trophies in proportion to their old counts.

Final-like cases: a launch week simulated in counts. The tracked count
grows as a power law in time since release; each trophy's earners grow as
their own power law (easier trophies faster); reads every 30 minutes early
and every 10 later show the last refresh's values, refreshes landing every
four to six hours. The truth is the rate at the last refresh before the
target window. Two regimes: steady growth, and growth that slows after
day two.
"""

import json
import math
import random
from datetime import datetime, timedelta, timezone

KIT = "/home/hatch/workspace/makefun/gta6-trophy-rarity-forecast-01-kit"
UTC = timezone.utc


def _parse(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _fmt(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def practice_answers():
    """Three plausible post-refresh rate tables for the practice cases."""
    case = json.load(open(f"{KIT}/input/p01.in"))
    last = case["history"][-1]
    old_n = last["players_tracked"]
    old_rates = last["rates"]
    old_earners = [r * old_n / 100.0 for r in old_rates]
    new_n = 3868
    new_players = new_n - old_n
    total_new_earns = 4276 - 943

    def build(starter_share, spread_ids):
        starter_i = old_rates.index(100.0)
        new_earners = [0.0] * len(old_rates)
        new_earners[starter_i] = starter_share * new_players
        rest = total_new_earns - new_earners[starter_i]
        base = sum(old_earners[i] for i in spread_ids)
        for i in spread_ids:
            new_earners[i] = rest * old_earners[i] / base
        truth = {}
        for i, trophy in enumerate(case["trophies"]):
            rate = 100.0 * (old_earners[i] + new_earners[i]) / new_n
            truth[trophy["id"]] = round(min(100.0, max(0.0, rate)), 2)
        return truth

    all_ids = [i for i in range(len(old_rates)) if old_rates[i] != 100.0]
    easy_ids = [i for i in all_ids if old_rates[i] >= 1.1]
    return {
        "spread": build(0.99, all_ids),
        "concentrated": build(1.0, easy_ids),
        "generous": build(0.95, all_ids),
    }


def _power_law_n(t_days, slowdown=False):
    """Tracked players t_days after the release day began."""
    if t_days <= 0:
        return 638
    if slowdown:
        early = min(t_days, 2.0)
        late = max(0.0, t_days - 2.0)
        return round(638 * (1 + 8 * early) ** 1.3
                     * (1 + 1.5 * late) ** 0.6)
    return round(638 * (1 + 8 * t_days) ** 1.3)


def final_case(variant="steady", seed=7):
    """A synthetic final case (f00 shape) plus its truth.

    variant "steady": the tracked count keeps its power-law growth and
    each trophy's earners keep theirs. variant "slowdown": both growths
    ease after day two, so a trend fitted on days zero to two overshoots.
    """
    rng = random.Random(seed)
    case = json.load(open(f"{KIT}/input/p01.in"))
    trophies = case["trophies"]
    launch_rates = case["history"][-1]["rates"]

    # Each trophy's earner growth exponent: easier trophies (higher
    # launch-night rate) grow faster; the starter stays at 100%.
    top = max(r for r in launch_rates if r < 100.0)
    bottom = min(r for r in launch_rates if r < 100.0)
    exponents = []
    for r in launch_rates:
        if r >= 100.0:
            exponents.append(0.0)
        else:
            share = (r - bottom) / (top - bottom)
            exponents.append(0.6 + 0.9 * share)

    slowdown = variant == "slowdown"

    def true_rate(i, t_days):
        if launch_rates[i] >= 100.0:
            return 100.0
        base = launch_rates[i] * 638 / 100.0
        grow = ((t_days + 0.1) / 0.12) ** exponents[i]
        if slowdown:
            grow = grow ** 0.8
        earners = base * grow
        n = _power_law_n(t_days, slowdown)
        return min(99.99, max(0.01, 100.0 * earners / n))

    # Refreshes every four to six hours from the first probe on.
    start = _parse("2026-11-18T23:50:00Z")
    deadline = _parse("2026-11-21T11:30:00Z")
    target = _parse("2026-11-26T00:00:00Z")
    refresh_times = []
    moment = start
    while moment <= target + timedelta(hours=1):
        refresh_times.append(moment)
        moment += timedelta(hours=rng.uniform(4.0, 6.0))

    def snapshot(when):
        t_days = (when - _parse("2026-11-19T00:00:00Z")).total_seconds() / 86400
        rates = [round(true_rate(i, max(0.0, t_days)), 2)
                 for i in range(len(trophies))]
        n = _power_law_n(max(0.0, t_days), slowdown)
        earned = round(sum(r * n / 100.0 for r in rates))
        return n, earned, rates

    # Reads on the house clock: every 30 minutes, then every 10.
    reads = []
    slot = start
    refresh_i = 0
    state = snapshot(refresh_times[0])
    while slot < deadline:
        while (refresh_i + 1 < len(refresh_times)
               and refresh_times[refresh_i + 1] <= slot):
            refresh_i += 1
            state = snapshot(refresh_times[refresh_i])
        n, earned, rates = state
        reads.append({
            "slot": _fmt(slot),
            "at": _fmt(slot + timedelta(seconds=rng.uniform(0, 50))),
            "players_tracked": n,
            "trophies_earned": earned,
            "rates": rates,
        })
        step = 30 if slot < _parse("2026-11-19T12:00:00Z") else 10
        slot += timedelta(minutes=step)

    # The truth: the rates at the last refresh before the target window.
    last_before = max(t for t in refresh_times if t <= target)
    _, _, truth_rates = snapshot(last_before)
    truth = {t["id"]: truth_rates[i] for i, t in enumerate(trophies)}

    f00 = {
        "schema": "makefun/gta6-trophies-case/v1",
        "id": "f00",
        "at": _fmt(deadline),
        "horizon_minutes": 6510,
        "target": _fmt(target),
        "levels": [0.05, 0.25, 0.5, 0.75, 0.95],
        "game": case["game"],
        "trophies": trophies,
        "history": reads,
        "notes": "Synthetic final-like case for local testing only.",
    }
    return f00, truth

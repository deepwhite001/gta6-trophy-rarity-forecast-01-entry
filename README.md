# GTA VI trophy rarity forecast

Entry for make.fun competition `gta6-trophy-rarity-forecast-01`: forecast,
for every Grand Theft Auto VI trophy on PlayStation 5, its earn rate on
Exophase at 2026-11-26T00:00:00Z, as five quantiles per trophy
(5%, 25%, 50%, 75%, 95%), scored by pinball loss on the logit scale.

## Program

`main.py` (Python 3.13, standard library only) reads every `<id>.in` in the
input directory and writes `<id>.out` to the output directory:

```
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

## How it forecasts

Exophase refreshes a game's counts on its own schedule, hours apart, so
between refreshes every read is the same page, and at a refresh the
tracked-player count jumps and most rates fall: newly tracked players have
had less time to play. A rate is a ratio of two growing counts, sampled only
at refresh moments.

The program works in logit space and fits, per trophy, a straight line of
logit(rate) against log time since the release day began, over the distinct
reads only. Slopes are shrunk toward the median slope across trophies, and
the spread combines the fit's residual scatter, the slope's error times the
leverage out to the target, and a floor that grows with the horizon. When
the history holds only one distinct read there is no trend to fit, so the
median is shifted down by a refresh-dilution allowance and the spread is set
wide. Whatever happens, every case still writes a valid forecast.

## Tests

`tests/` holds a scorer written from the competition's scoring rule, a
synthetic final-like case with a known truth, and synthetic practice
answers built from the refresh the kit documents (638 → 3,868 players
tracked at the 04:30Z read). Run the entry on the kit's practice cases and
score it against those answers with:

```
python3 tests/run_checks.py
```

No network, no model calls, no fees: pure script work.

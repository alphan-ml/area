'''Causal module (task W-B3): a standard 2-group / 2-period
difference-in-differences (DiD) estimator over the real matched GLP-1
payments data.

Disclosed design (this task has no more specific SPEC-area.md section 10
text locally than src/area/causal/ (W-B3) -- CONTEXT.md.s own Not yet
built line): rather than invent a specific real-world policy event and
claim this module discovered its effect (which this data alone cannot
support -- payments data has no outcome/prescribing variable to make a
causal claim about), this module implements the estimator itself as a
reusable, honestly-scoped tool: given a treated manufacturer group, a
control manufacturer group, and a caller-supplied split quarter (the
event), it computes the standard 2x2 DiD estimate --
(treated_after - treated_before) - (control_after - control_before) --
over real quarterly payment-dollar series built the same way
forecast/build_series.py does (D14: reads raw/<year>/matched.jsonl.gz via
load_neon.iter_matched_rows/coerce_row, not a synthetic input). The
split quarter is a caller-supplied assumption (area causal
--event-quarter <q>, CLI default: the median quarter actually present
in the data), not a claim that anything in particular happened then --
the standard DiD caveat (parallel pre-trends between the two groups)
applies and is reported, not assumed away: parallel_trend_note gives a
plain-English reminder so a reader judges pre-trend similarity
themselves rather than being told the DiD estimate is unconditionally
causal.

Default groups: treated = data/products.json.s manufacturers_of_record
(Novo Nordisk, Eli Lilly -- the GLP-1 originators), control = every
other manufacturer paying on a matched row (generics/co-marketers/GPOs
-- see that file.s own manufacturer_sanity_check note). Insufficient
data (fewer than 1 quarter on either side of the split, or an empty
group) is reported as a stated skip reason, never a fabricated number --
same discipline as forecast/holdout.py.s own per-series skip reporting.
'''

from __future__ import annotations

import json
from pathlib import Path

from area.load_neon import coerce_row, iter_matched_rows

DEFAULT_TREATED_MANUFACTURERS = ('Novo Nordisk', 'Eli Lilly')


def build_group_series(
    raw_dir: Path,
    treated_manufacturers: tuple = DEFAULT_TREATED_MANUFACTURERS,
    years: list | None = None,
) -> tuple[dict, dict]:
    '''One pass over every complete year.s real matched rows (D14.s
    reading path). Returns (treated_quarterly_totals, control_quarterly_totals),
    both quarter-to-dollar-total dicts. A row whose manufacturer is in
    treated_manufacturers (exact match against the real manufacturer
    field, same as data/products.json.s own manufacturer_sanity_check)
    goes to the treated series; every other real matched row (including a
    null/unknown manufacturer) goes to control.'''
    treated_set = set(treated_manufacturers)
    treated: dict = {}
    control: dict = {}
    for raw in iter_matched_rows(raw_dir, years=years):
        try:
            row = coerce_row(raw)
        except ValueError:
            continue
        quarter = row['quarter']
        if quarter is None:
            continue
        amount = float(row['amount_usd'])
        target = treated if row['manufacturer'] in treated_set else control
        target[quarter] = target.get(quarter, 0.0) + amount
    return treated, control


def _mean_over_quarters(series: dict, quarters: list) -> float | None:
    values = [series[q] for q in quarters if q in series]
    if not values:
        return None
    return sum(values) / len(values)


def estimate_diff_in_diff(treated: dict, control: dict, event_quarter: str) -> dict:
    '''Standard 2x2 DiD over two real quarterly series. event_quarter
    splits the data into before (< event_quarter) and after (>=
    event_quarter) by lexicographic quarter-string order, which is
    correct for this codebase.s own quarter format (load_neon.quarter_of,
    W-B1). Returns a dict with ok=False and a stated reason if either
    period is empty for either group -- never a fabricated DiD number.'''
    all_quarters = sorted(set(treated) | set(control))
    before = [q for q in all_quarters if q < event_quarter]
    after = [q for q in all_quarters if q >= event_quarter]

    treated_before = _mean_over_quarters(treated, before)
    treated_after = _mean_over_quarters(treated, after)
    control_before = _mean_over_quarters(control, before)
    control_after = _mean_over_quarters(control, after)

    missing = [
        name
        for name, value in (
            ('treated_before', treated_before),
            ('treated_after', treated_after),
            ('control_before', control_before),
            ('control_after', control_after),
        )
        if value is None
    ]
    if missing:
        return {
            'ok': False,
            'reason': (
                'insufficient data for: ' + ', '.join(missing) + ' -- need at least one '
                'quarter of real data on each side of the event for both groups'
            ),
            'event_quarter': event_quarter,
            'quarters_before': before,
            'quarters_after': after,
        }

    treated_delta = treated_after - treated_before
    control_delta = control_after - control_before
    did_estimate = treated_delta - control_delta

    return {
        'ok': True,
        'event_quarter': event_quarter,
        'quarters_before': before,
        'quarters_after': after,
        'treated_mean_before_usd': treated_before,
        'treated_mean_after_usd': treated_after,
        'control_mean_before_usd': control_before,
        'control_mean_after_usd': control_after,
        'treated_delta_usd': treated_delta,
        'control_delta_usd': control_delta,
        'diff_in_diff_usd': did_estimate,
        'parallel_trend_note': (
            'DiD assumes treated and control would have moved in parallel '
            'absent the split; this module does not test that assumption -- '
            'compare treated_mean_before_usd/control_mean_before_usd trends '
            'across quarters_before yourself before treating diff_in_diff_usd '
            'as causal.'
        ),
    }


def run(
    raw_dir: Path,
    out_path: Path,
    *,
    treated_manufacturers: tuple = DEFAULT_TREATED_MANUFACTURERS,
    event_quarter: str | None = None,
    years: list | None = None,
) -> dict:
    '''area causal.s entry point: builds the two real quarterly series
    from raw_dir, picks event_quarter (the median quarter actually
    present, if not given explicitly), runs estimate_diff_in_diff, and
    writes out_path. Returns the same dict it writes.'''
    treated, control = build_group_series(raw_dir, treated_manufacturers, years=years)
    all_quarters = sorted(set(treated) | set(control))

    if not all_quarters:
        result = {
            'ok': False,
            'reason': 'no real matched rows with a usable quarter found under raw_dir -- '
            'run area pull / area load first',
            'treated_manufacturers': list(treated_manufacturers),
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2))
        return result

    if event_quarter is None:
        event_quarter = all_quarters[len(all_quarters) // 2]

    result = estimate_diff_in_diff(treated, control, event_quarter)
    result['treated_manufacturers'] = list(treated_manufacturers)
    result['treated_series'] = treated
    result['control_series'] = control
    result['quarters_available'] = all_quarters

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    return result

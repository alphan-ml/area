"""raw pulled data -> quarterly dollar series, national and by specialty
(SPEC-area.md section 2's repo layout comment; task-split section 10's
W-B2 line: "forecast build + holdout on the pulled data (runs locally on
DuckDB or pandas from raw)").

Disclosed judgment call (CONTEXT.md decision D14): the repo
layout's one-line comment for this file says "Neon -> quarterly series",
but the task-split line for W-B2 explicitly says "no secrets for tests"
and "on the pulled data ... from raw" -- and in any case there is no
Neon database to read from yet (Gate 1 not reached; D11). This module
reads directly from `raw/<year>/matched.jsonl.gz` (the same files
load_neon.py loads into Postgres), reusing load_neon.py's own
`iter_matched_rows`/`coerce_row` so the two paths can never silently
disagree on what counts as a valid row. Once Neon is live (post Gate 1),
an equivalent Neon-backed series builder (SELECT quarter, SUM(amount_usd)
FROM payments ... -- literally the q_totals/q_by_specialty views) can be
added alongside this one without changing forecast/holdout.py's
interface (it just wants a `dict[quarter] -> float` per series); this
file's approach is not a rejection of the Neon path, just the only one
that works before Gate 1.
"""

from __future__ import annotations

from pathlib import Path

from area.load_neon import coerce_row, iter_matched_rows

UNKNOWN_SPECIALTY = "(unknown)"


def _scan(
    raw_dir: Path, years: list[int] | None = None
) -> tuple[dict[str, float], dict[str, dict[str, float]], dict[str, float]]:
    """One pass over every complete year's matched rows. Returns
    (national quarterly totals, {specialty: quarterly totals}, specialty
    grand totals) -- the grand totals are only for ranking specialties in
    build_specialty_series/build_all, not part of either series."""
    national: dict[str, float] = {}
    by_specialty: dict[str, dict[str, float]] = {}
    specialty_totals: dict[str, float] = {}
    for raw in iter_matched_rows(raw_dir, years=years):
        try:
            row = coerce_row(raw)
        except ValueError:
            # A row that fails coercion (missing a required field) is a
            # data-quality problem load_neon.py already surfaces via its
            # own LoadStats.rows_skipped_bad when the real load runs;
            # a forecast series build skips it the same way rather than
            # inventing a value for it.
            continue
        quarter = row["quarter"]
        if quarter is None:
            continue
        amount = float(row["amount_usd"])
        national[quarter] = national.get(quarter, 0.0) + amount
        specialty = row["physician_specialty"] or UNKNOWN_SPECIALTY
        by_specialty.setdefault(specialty, {})
        by_specialty[specialty][quarter] = by_specialty[specialty].get(quarter, 0.0) + amount
        specialty_totals[specialty] = specialty_totals.get(specialty, 0.0) + amount
    return national, by_specialty, specialty_totals


def build_national_series(raw_dir: Path, years: list[int] | None = None) -> dict[str, float]:
    """{quarter: total matched-payment dollars that quarter}, national."""
    national, _by_specialty, _totals = _scan(raw_dir, years)
    return national


def build_specialty_series(
    raw_dir: Path, years: list[int] | None = None, top_n: int = 5
) -> dict[str, dict[str, float]]:
    """{specialty: {quarter: total dollars}} for the top_n specialties by
    all-time matched-payment dollars (ties broken by specialty name for
    determinism)."""
    _national, by_specialty, totals = _scan(raw_dir, years)
    top = sorted(totals, key=lambda s: (-totals[s], s))[:top_n]
    return {specialty: by_specialty[specialty] for specialty in top}


def build_all(
    raw_dir: Path, years: list[int] | None = None, top_n_specialties: int = 5
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Same as calling build_national_series and build_specialty_series,
    but scans raw_dir exactly once -- the entry point forecast/holdout.py
    uses, since a real-scale raw directory (tens of millions of rows once
    the real pull runs, D11) shouldn't be scanned twice for one CLI run."""
    national, by_specialty, totals = _scan(raw_dir, years)
    top = sorted(totals, key=lambda s: (-totals[s], s))[:top_n_specialties]
    return national, {specialty: by_specialty[specialty] for specialty in top}

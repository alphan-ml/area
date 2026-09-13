"""Holdout evaluation and the `area forecast` orchestration (SPEC-area.md
section 4.3): train on quarters <= 2024Q4, test on 2025Q1-2025Q4; report
MAE and 80%-interval coverage for seasonal_naive and ets, and state
n_quarters (n_train and n_test, both stated explicitly -- spec just says
"state n_quarters" without naming train vs. test, so this reports both
rather than picking one and leaving the other implicit).

`run()` is the top-level entry point the `area forecast` CLI command
calls (SPEC-area.md's repo layout: "refreshed by the `area forecast`
command"). It builds each series from raw pulled data (build_series.py),
evaluates both models' holdout accuracy, and picks whichever model has
the lower holdout MAE as the "primary" forecast that forecast_tool.py
serves -- both models' holdout metrics are kept in the output either way
(disclosed reading of section 4.3's "the point forecast, 80% interval,
holdout MAE and coverage for both models": singular "the" forecast,
holdout numbers "for both" -- see CONTEXT.md decision D13).

No fake numbers: if a series doesn't have enough history to holdout-test
(section 1's non-negotiable applies here too -- this is a computed
metric, not a fact, but the same rule against inventing values holds),
`run()` records that series as skipped with a stated reason rather than
writing a fabricated forecast for it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from area.forecast.models import ForecastResult, ets, seasonal_naive

TRAIN_END_DEFAULT = "2024Q4"
TEST_HORIZON_DEFAULT = 4
SEASON_LENGTH_DEFAULT = 4

ModelFn = Callable[[list[float], int, int], ForecastResult]
MODELS: dict[str, ModelFn] = {"seasonal_naive": seasonal_naive, "ets": ets}


def _quarter_to_int(quarter: str) -> int:
    year, qn = int(quarter[:4]), int(quarter[5])
    return year * 4 + (qn - 1)


def _int_to_quarter(n: int) -> str:
    year, qn = divmod(n, 4)
    return f"{year}Q{qn + 1}"


def mean_absolute_error(actuals: list[float], point: list[float]) -> float:
    if len(actuals) != len(point) or not actuals:
        raise ValueError("actuals and point forecasts must be the same non-zero length")
    return sum(abs(a - p) for a, p in zip(actuals, point)) / len(actuals)


def coverage(actuals: list[float], lower: list[float], upper: list[float]) -> float:
    if not (len(actuals) == len(lower) == len(upper)) or not actuals:
        raise ValueError("actuals, lower, and upper must all be the same non-zero length")
    hits = sum(1 for a, lo, hi in zip(actuals, lower, upper) if lo <= a <= hi)
    return hits / len(actuals)


@dataclass
class HoldoutResult:
    model: str
    n_train: int
    n_test: int
    mae: float
    coverage: float
    test_quarters: list[str]
    actuals: list[float]
    point: list[float]
    lower: list[float]
    upper: list[float]


def holdout_evaluate(
    series: dict[str, float],
    model_name: str,
    train_end: str = TRAIN_END_DEFAULT,
    horizon: int = TEST_HORIZON_DEFAULT,
    season_length: int = SEASON_LENGTH_DEFAULT,
) -> HoldoutResult:
    """Trains `model_name` on every quarter <= train_end, forecasts the
    next `horizon` quarters, and scores against the actuals for those
    quarters (which must be present in `series` -- section 1's
    non-negotiable means we don't guess a missing actual)."""
    if model_name not in MODELS:
        raise ValueError(f"unknown model {model_name!r}; expected one of {sorted(MODELS)}")
    train_end_idx = _quarter_to_int(train_end)
    train_quarters = sorted(
        (q for q in series if _quarter_to_int(q) <= train_end_idx), key=_quarter_to_int
    )
    if not train_quarters:
        raise ValueError(f"no training data at or before {train_end}")
    train_values = [series[q] for q in train_quarters]

    test_quarters = [_int_to_quarter(train_end_idx + h) for h in range(1, horizon + 1)]
    missing = [q for q in test_quarters if q not in series]
    if missing:
        raise ValueError(f"missing actuals for test quarter(s) {missing}; cannot score holdout")
    actuals = [series[q] for q in test_quarters]

    forecast = MODELS[model_name](train_values, horizon, season_length)
    return HoldoutResult(
        model=model_name,
        n_train=len(train_values),
        n_test=len(actuals),
        mae=mean_absolute_error(actuals, forecast.point),
        coverage=coverage(actuals, forecast.lower, forecast.upper),
        test_quarters=test_quarters,
        actuals=actuals,
        point=forecast.point,
        lower=forecast.lower,
        upper=forecast.upper,
    )


def evaluate_series(
    series: dict[str, float],
    train_end: str = TRAIN_END_DEFAULT,
    horizon: int = TEST_HORIZON_DEFAULT,
    season_length: int = SEASON_LENGTH_DEFAULT,
) -> dict:
    """Runs holdout_evaluate for both models, picks the lower-MAE model as
    primary, and refits that model on the FULL series (all quarters, not
    just the pre-2025 training window) for the actual forward-looking
    forecast forecast_tool.py serves. Returns a plain dict, JSON-ready."""
    holdouts = {
        name: holdout_evaluate(series, name, train_end, horizon, season_length) for name in MODELS
    }
    primary_name = min(holdouts, key=lambda name: holdouts[name].mae)

    all_quarters = sorted(series, key=_quarter_to_int)
    all_values = [series[q] for q in all_quarters]
    final = MODELS[primary_name](all_values, horizon, season_length)
    last_quarter_idx = _quarter_to_int(all_quarters[-1])
    forecast_quarters = [_int_to_quarter(last_quarter_idx + h) for h in range(1, horizon + 1)]

    return {
        "primary_model": primary_name,
        "training_window": {
            "first_quarter": all_quarters[0],
            "last_quarter": all_quarters[-1],
            "n_quarters": len(all_quarters),
        },
        "forecast": {
            "quarters": forecast_quarters,
            "point": final.point,
            "lower_80": final.lower,
            "upper_80": final.upper,
        },
        "holdout": {
            name: {
                "n_train": h.n_train,
                "n_test": h.n_test,
                "mae": h.mae,
                "coverage_80": h.coverage,
                "test_quarters": h.test_quarters,
            }
            for name, h in holdouts.items()
        },
    }


def run(raw_dir: Path, out_path: Path, top_n_specialties: int = 5) -> dict:
    """Builds the national series and the top-N-by-dollars specialty
    series from raw pulled data (build_series.py), holdout-evaluates and
    forecasts each, and writes the result to out_path as JSON. A series
    with fewer than 2*season_length quarters of history is recorded as
    skipped (with a reason), never given a fabricated forecast."""
    from area.forecast.build_series import build_all

    output: dict = {"series": {}, "skipped": {}}

    national, specialty_series = build_all(raw_dir, top_n_specialties=top_n_specialties)
    _evaluate_or_skip(output, "national", national)
    for specialty, series in specialty_series.items():
        _evaluate_or_skip(output, f"specialty:{specialty}", series)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, sort_keys=True, default=_json_default))
    return output


def _evaluate_or_skip(output: dict, key: str, series: dict[str, float]) -> None:
    min_quarters = 2 * SEASON_LENGTH_DEFAULT
    if len(series) < min_quarters:
        output["skipped"][key] = (
            f"only {len(series)} quarters of data, need at least {min_quarters}"
        )
        return
    try:
        output["series"][key] = evaluate_series(series)
    except ValueError as exc:
        output["skipped"][key] = str(exc)


def _json_default(value):
    if hasattr(value, "__float__"):
        return float(value)
    raise TypeError(f"not JSON serializable: {value!r}")


__all__ = [
    "HoldoutResult",
    "holdout_evaluate",
    "evaluate_series",
    "run",
    "mean_absolute_error",
    "coverage",
]

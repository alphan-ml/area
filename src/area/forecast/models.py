"""Forecast models (SPEC-area.md section 4.3 / 5): seasonal_naive, ets,
and 80% prediction intervals.

Interval methodology is a disclosed judgment call (spec section 4.3 says
"holdout MAE and coverage... for seasonal naive and ETS" and asks for an
"80% interval" but doesn't specify how to derive one for either model --
see CONTEXT.md decision D13): both models get a symmetric
interval of point-forecast +/- z * residual_std, z=1.2816 (the two-sided
80% normal critical value, i.e. 10% in each tail). residual_std for
seasonal_naive comes from that model's own one-step-ahead seasonal errors
over the training history; for ets it comes from the fitted model's
in-sample residuals. Using the same interval shape for both models keeps
their holdout coverage numbers (holdout.py) comparable.
"""

from __future__ import annotations

from dataclasses import dataclass

# 80% two-sided normal critical value (10% in each tail).
Z_80 = 1.2816


@dataclass
class ForecastResult:
    model: str  # "seasonal_naive" | "ets"
    point: list[float]
    lower: list[float]
    upper: list[float]


def _prediction_interval(
    point: list[float], residual_std: float, z: float = Z_80
) -> tuple[list[float], list[float]]:
    margin = z * residual_std
    lower = [p - margin for p in point]
    upper = [p + margin for p in point]
    return lower, upper


def seasonal_naive_point(history: list[float], horizon: int, season_length: int = 4) -> list[float]:
    """forecast[h] = the value season_length periods before position
    (len(history) + h), cycling through the last season_length observed
    values for h >= season_length. Requires at least one full season of
    history."""
    if len(history) < season_length:
        raise ValueError(
            f"seasonal_naive needs at least {season_length} observations, got {len(history)}"
        )
    last_season = history[-season_length:]
    return [last_season[h % season_length] for h in range(horizon)]


def _seasonal_naive_residual_std(history: list[float], season_length: int) -> float:
    errors = [history[i] - history[i - season_length] for i in range(season_length, len(history))]
    if len(errors) < 2:
        return 0.0
    mean = sum(errors) / len(errors)
    variance = sum((e - mean) ** 2 for e in errors) / (len(errors) - 1)
    return variance**0.5


def seasonal_naive(history: list[float], horizon: int, season_length: int = 4) -> ForecastResult:
    point = seasonal_naive_point(history, horizon, season_length)
    residual_std = _seasonal_naive_residual_std(history, season_length)
    lower, upper = _prediction_interval(point, residual_std)
    return ForecastResult(model="seasonal_naive", point=point, lower=lower, upper=upper)


def ets(history: list[float], horizon: int, season_length: int = 4) -> ForecastResult:
    """Holt-Winters exponential smoothing (additive trend, additive
    seasonal, period=season_length) via statsmodels. Imported lazily so
    modules that don't need forecasting never require statsmodels/pandas
    to be installed to import -- same lazy-dependency pattern as
    area.load_neon's psycopg import."""
    import pandas as pd
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    if len(history) < season_length * 2:
        raise ValueError(
            f"ets needs at least {season_length * 2} observations (2 full seasons), "
            f"got {len(history)}"
        )
    index = pd.period_range(start="2000Q1", periods=len(history), freq="Q")
    series = pd.Series(history, index=index)
    fit = ExponentialSmoothing(
        series,
        trend="add",
        seasonal="add",
        seasonal_periods=season_length,
        initialization_method="estimated",
    ).fit()
    point = [float(v) for v in fit.forecast(horizon)]
    residuals = fit.resid.dropna()
    residual_std = float(residuals.std(ddof=1)) if len(residuals) >= 2 else 0.0
    lower, upper = _prediction_interval(point, residual_std)
    return ForecastResult(model="ets", point=point, lower=lower, upper=upper)

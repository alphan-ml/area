"""Tests for area.forecast.models and area.forecast.holdout (SPEC-area.md
section 4.3). Named test cases from the spec: "on a synthetic seasonal
series, ETS beats naive on MAE; coverage is computed correctly on a
known case."""

from __future__ import annotations

import pytest

from area.forecast import holdout, models


def _synthetic_seasonal_series(n_quarters: int = 24) -> list[float]:
    """A clean quarterly series with trend + a repeating seasonal
    pattern -- the shape ETS is built to fit better than a naive
    seasonal repeat, since the trend keeps shifting the level."""
    pattern = [10.0, -5.0, 15.0, -20.0]
    return [100.0 + 2.0 * t + pattern[t % 4] for t in range(n_quarters)]


def test_seasonal_naive_point_repeats_last_season():
    history = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    result = models.seasonal_naive(history, horizon=4, season_length=4)
    assert result.point == [5.0, 6.0, 7.0, 8.0]
    assert result.model == "seasonal_naive"


def test_seasonal_naive_requires_a_full_season():
    with pytest.raises(ValueError):
        models.seasonal_naive([1.0, 2.0], horizon=1, season_length=4)


def test_ets_beats_naive_on_mae_for_a_synthetic_seasonal_series():
    series = _synthetic_seasonal_series(24)
    train, test = series[:20], series[20:24]

    naive = models.seasonal_naive(train, horizon=4, season_length=4)
    ets_result = models.ets(train, horizon=4, season_length=4)

    naive_mae = holdout.mean_absolute_error(test, naive.point)
    ets_mae = holdout.mean_absolute_error(test, ets_result.point)

    # The series has a steady linear trend on top of a fixed seasonal
    # pattern -- seasonal_naive cannot track the trend at all (it just
    # repeats the last season), while ETS's additive trend component
    # should track it closely. This is exactly the shape the spec's
    # named test case describes.
    assert ets_mae < naive_mae


def test_coverage_computed_correctly_on_a_known_case():
    actuals = [10.0, 20.0, 30.0, 40.0]
    lower = [5.0, 25.0, 25.0, 35.0]
    upper = [15.0, 30.0, 35.0, 45.0]
    # quarter 1: 10 in [5,15] -> hit. quarter 2: 20 NOT in [25,30] -> miss.
    # quarter 3: 30 in [25,35] -> hit. quarter 4: 40 in [35,45] -> hit.
    # 3 of 4 -> 0.75.
    assert holdout.coverage(actuals, lower, upper) == pytest.approx(0.75)


def test_coverage_all_hits_and_all_misses():
    assert holdout.coverage([1.0, 2.0], [0.0, 0.0], [5.0, 5.0]) == pytest.approx(1.0)
    assert holdout.coverage([10.0, 20.0], [0.0, 0.0], [1.0, 1.0]) == pytest.approx(0.0)


def test_mean_absolute_error_known_case():
    assert holdout.mean_absolute_error([10.0, 20.0], [12.0, 18.0]) == pytest.approx(2.0)


def test_mean_absolute_error_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        holdout.mean_absolute_error([1.0], [1.0, 2.0])


def _quarterly_dict(start_year: int, n_quarters: int, values: list[float]) -> dict[str, float]:
    series = {}
    year, q = start_year, 1
    for v in values[:n_quarters]:
        series[f"{year}Q{q}"] = v
        q += 1
        if q > 4:
            q = 1
            year += 1
    return series


def test_holdout_evaluate_scores_against_stated_test_quarters():
    values = _synthetic_seasonal_series(20)
    # Train quarters 2020Q1..2024Q4 (20 quarters), test 2025Q1..2025Q4.
    series = _quarterly_dict(2020, 24, values + _synthetic_seasonal_series(24)[20:24])
    result = holdout.holdout_evaluate(series, "seasonal_naive", train_end="2024Q4", horizon=4)
    assert result.n_train == 20
    assert result.n_test == 4
    assert result.test_quarters == ["2025Q1", "2025Q2", "2025Q3", "2025Q4"]
    assert result.mae >= 0.0
    assert 0.0 <= result.coverage <= 1.0


def test_holdout_evaluate_raises_on_missing_test_actuals():
    series = _quarterly_dict(2020, 20, _synthetic_seasonal_series(20))
    # No 2025 data at all -- the test quarters don't exist in the series.
    with pytest.raises(ValueError, match="missing actuals"):
        holdout.holdout_evaluate(series, "seasonal_naive", train_end="2024Q4", horizon=4)


def test_evaluate_series_picks_lower_mae_model_as_primary():
    values = _synthetic_seasonal_series(24)
    series = _quarterly_dict(2020, 24, values)
    result = holdout.evaluate_series(series)
    assert result["primary_model"] in ("seasonal_naive", "ets")
    holdout_maes = {name: h["mae"] for name, h in result["holdout"].items()}
    assert result["primary_model"] == min(holdout_maes, key=lambda n: holdout_maes[n])
    assert result["training_window"]["n_quarters"] == 24
    assert len(result["forecast"]["point"]) == 4
    assert len(result["forecast"]["lower_80"]) == 4
    assert len(result["forecast"]["upper_80"]) == 4


def test_run_writes_forecast_latest_json_and_skips_short_series(tmp_path):
    raw_dir = tmp_path / "raw"
    out_path = tmp_path / "forecast" / "latest.json"
    _write_fixture_raw_year(raw_dir, 2021, "2021Q1", n_rows=1)  # far too short a series

    result = holdout.run(raw_dir, out_path)
    assert out_path.exists()
    assert "national" in result["skipped"]
    assert "quarters of data" in result["skipped"]["national"]


def _write_fixture_raw_year(raw_dir, year: int, quarter: str, n_rows: int) -> None:
    import gzip
    import json as jsonlib

    year_dir = raw_dir / str(year)
    year_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(year_dir / "matched.jsonl.gz", "wt", encoding="utf-8") as f:
        for i in range(n_rows):
            row = {
                "record_id": f"r{i}",
                "program_year": str(year),
                "payment_date": "2021-02-01",
                "quarter": quarter,
                "physician_id": "123",
                "physician_specialty": "Endocrinology",
                "physician_state": "CA",
                "manufacturer": "Novo Nordisk",
                "product": "Ozempic",
                "product_generic": "semaglutide",
                "nature_of_payment": "Food and Beverage",
                "amount_usd": "10.00",
                "matched_field": "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1",
                "source_year_dataset": "test-dataset",
            }
            f.write(jsonlib.dumps(row) + "\n")
    (year_dir / "manifest.json").write_text(
        jsonlib.dumps({"complete": True, "rows_matched": n_rows})
    )

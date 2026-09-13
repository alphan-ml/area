"""Tests for area.tools.forecast_tool (SPEC-area.md section 4.3). This
tool is a read-only lookup into forecast/latest.json -- it never
computes a forecast itself. See area.forecast.holdout for the module
that actually produces that file."""

from __future__ import annotations

import json

from area.tools.forecast_tool import run


def _write_latest(path, series_key="national"):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "series": {
            series_key: {
                "primary_model": "ets",
                "training_window": {
                    "first_quarter": "2021Q1",
                    "last_quarter": "2025Q4",
                    "n_quarters": 20,
                },
                "forecast": {
                    "quarters": ["2026Q1", "2026Q2", "2026Q3", "2026Q4"],
                    "point": [100.0, 110.0, 120.0, 130.0],
                    "lower_80": [90.0, 100.0, 110.0, 120.0],
                    "upper_80": [110.0, 120.0, 130.0, 140.0],
                },
                "holdout": {
                    "seasonal_naive": {
                        "n_train": 16, "n_test": 4, "mae": 12.5, "coverage_80": 0.75,
                    },
                    "ets": {"n_train": 16, "n_test": 4, "mae": 8.1, "coverage_80": 1.0},
                },
            }
        },
        "skipped": {},
    }
    path.write_text(json.dumps(payload))
    return payload


def test_missing_file_returns_clear_error_not_a_fake_forecast(tmp_path):
    missing_path = tmp_path / "forecast" / "latest.json"
    result = run({"series": "national"}, path=missing_path)
    assert result.ok is False
    assert "does not exist" in result.error
    assert "area forecast" in result.error


def test_reads_requested_series_and_produces_evidence(tmp_path):
    path = tmp_path / "forecast" / "latest.json"
    _write_latest(path)

    result = run({"series": "national", "horizon": 4}, path=path)
    assert result.ok is True
    assert result.data["primary_model"] == "ets"
    assert result.data["quarters"] == ["2026Q1", "2026Q2", "2026Q3", "2026Q4"]
    assert result.data["point"] == [100.0, 110.0, 120.0, 130.0]
    assert result.data["holdout"]["ets"]["mae"] == 8.1
    assert len(result.evidence) == 1
    assert result.evidence[0].kind == "forecast"


def test_horizon_shorter_than_stored_slices_the_result(tmp_path):
    path = tmp_path / "forecast" / "latest.json"
    _write_latest(path)

    result = run({"series": "national", "horizon": 2}, path=path)
    assert result.ok is True
    assert result.data["quarters"] == ["2026Q1", "2026Q2"]
    assert result.data["point"] == [100.0, 110.0]


def test_horizon_longer_than_stored_is_rejected(tmp_path):
    path = tmp_path / "forecast" / "latest.json"
    _write_latest(path)

    result = run({"series": "national", "horizon": 8}, path=path)
    assert result.ok is False
    assert "exceeds the precomputed horizon" in result.error


def test_unknown_series_reports_known_series(tmp_path):
    path = tmp_path / "forecast" / "latest.json"
    _write_latest(path, series_key="national")

    result = run({"series": "specialty:Cardiology"}, path=path)
    assert result.ok is False
    assert "national" in result.error


def test_skipped_series_reports_the_stated_reason(tmp_path):
    path = tmp_path / "forecast" / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"series": {}, "skipped": {"national": "only 3 quarters"}}))

    result = run({"series": "national"}, path=path)
    assert result.ok is False
    assert "only 3 quarters" in result.error


def test_missing_series_arg_is_rejected():
    result = run({})
    assert result.ok is False
    assert "series" in result.error

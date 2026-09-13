"""Tests for area.forecast.build_series -- raw pulled data (matched
jsonl.gz files, the same format pull_open_payments.py writes and
load_neon.py reads) -> quarterly dollar series. See build_series.py's
module docstring for why this reads raw files rather than Neon."""

from __future__ import annotations

import gzip
import json

from area.forecast.build_series import build_all, build_national_series, build_specialty_series


def _row(record_id, quarter, specialty, amount, program_year=2023):
    return {
        "record_id": record_id,
        "program_year": str(program_year),
        "payment_date": "2023-02-01",
        "quarter": quarter,
        "physician_id": "123",
        "physician_specialty": specialty,
        "physician_state": "CA",
        "manufacturer": "Novo Nordisk",
        "product": "Ozempic",
        "product_generic": "semaglutide",
        "nature_of_payment": "Food and Beverage",
        "amount_usd": str(amount),
        "matched_field": "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1",
        "source_year_dataset": "test-dataset",
    }


def _write_year(raw_dir, year: int, rows: list[dict], complete: bool = True) -> None:
    year_dir = raw_dir / str(year)
    year_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(year_dir / "matched.jsonl.gz", "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    manifest = {"complete": complete, "rows_matched": len(rows)}
    (year_dir / "manifest.json").write_text(json.dumps(manifest))


def test_build_national_series_sums_by_quarter(tmp_path):
    raw_dir = tmp_path / "raw"
    rows = [
        _row("r1", "2023Q1", "Endocrinology", "100.00"),
        _row("r2", "2023Q1", "Cardiology", "50.00"),
        _row("r3", "2023Q2", "Endocrinology", "200.00"),
    ]
    _write_year(raw_dir, 2023, rows)

    series = build_national_series(raw_dir)
    assert series == {"2023Q1": 150.0, "2023Q2": 200.0}


def test_build_specialty_series_ranks_by_total_and_caps_top_n(tmp_path):
    raw_dir = tmp_path / "raw"
    rows = [
        _row("r1", "2023Q1", "Endocrinology", "300.00"),
        _row("r2", "2023Q1", "Cardiology", "100.00"),
        _row("r3", "2023Q1", "Neurology", "50.00"),
    ]
    _write_year(raw_dir, 2023, rows)

    top2 = build_specialty_series(raw_dir, top_n=2)
    assert set(top2) == {"Endocrinology", "Cardiology"}
    assert top2["Endocrinology"] == {"2023Q1": 300.0}


def test_build_specialty_series_uses_unknown_bucket_for_blank_specialty(tmp_path):
    raw_dir = tmp_path / "raw"
    row = _row("r1", "2023Q1", "", "10.00")
    _write_year(raw_dir, 2023, [row])

    series = build_specialty_series(raw_dir, top_n=5)
    assert "(unknown)" in series


def test_incomplete_year_is_skipped(tmp_path):
    raw_dir = tmp_path / "raw"
    rows = [_row("r1", "2023Q1", "Endocrinology", "100.00")]
    _write_year(raw_dir, 2023, rows, complete=False)

    series = build_national_series(raw_dir)
    assert series == {}


def test_row_missing_required_field_is_skipped_not_invented(tmp_path):
    raw_dir = tmp_path / "raw"
    bad_row = _row("r1", "2023Q1", "Endocrinology", "100.00")
    bad_row["manufacturer"] = ""  # required by coerce_row -- makes it invalid
    good_row = _row("r2", "2023Q1", "Endocrinology", "50.00")
    _write_year(raw_dir, 2023, [bad_row, good_row])

    series = build_national_series(raw_dir)
    assert series == {"2023Q1": 50.0}


def test_build_all_matches_the_two_separate_calls(tmp_path):
    raw_dir = tmp_path / "raw"
    rows = [
        _row("r1", "2023Q1", "Endocrinology", "300.00"),
        _row("r2", "2023Q1", "Cardiology", "100.00"),
    ]
    _write_year(raw_dir, 2023, rows)

    national_a, specialty_a = build_all(raw_dir, top_n_specialties=1)
    national_b = build_national_series(raw_dir)
    specialty_b = build_specialty_series(raw_dir, top_n=1)

    assert national_a == national_b
    assert specialty_a == specialty_b

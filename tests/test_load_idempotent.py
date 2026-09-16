"""Tests for area/load_neon.py (SPEC-area.md section 3.3).

Type-coercion and file-discovery tests need no network or database. The
headline test -- "re-running with the same raw files changes nothing" --
needs a real Postgres to be a real proof, not a mock of one, so it runs
sql/001_schema.sql's actual DDL against a scratch database named by
TEST_DATABASE_URL (see .env.example). Without that env var set, the
DB-backed tests are SKIPPED with a stated reason, never silently passed.
"""

from __future__ import annotations

import gzip
import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from area.load_neon import coerce_row, ensure_schema, iter_matched_rows, load_raw_dir

REPO_ROOT = Path(__file__).resolve().parent.parent
SQL_PATHS = [REPO_ROOT / "sql" / "001_schema.sql", REPO_ROOT / "sql" / "002_views.sql"]

pytestmark = []


def _write_matched_year(raw_dir: Path, year: int, rows: list[dict], complete: bool = True) -> None:
    year_dir = raw_dir / str(year)
    year_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(year_dir / "matched.jsonl.gz", "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    manifest = {
        "year": year,
        "complete": complete,
        "rows_matched": len(rows),
        "error": None if complete else "simulated incomplete pull",
    }
    (year_dir / "manifest.json").write_text(json.dumps(manifest))


def _sample_row(**overrides) -> dict:
    row = {
        "record_id": "rec-1",
        "program_year": "2023",
        "payment_date": "2023-04-15",
        "quarter": "2023Q2",
        "physician_id": "1234567890",
        "physician_specialty": "Endocrinology",
        "physician_state": "CA",
        "manufacturer": "Novo Nordisk",
        "product": "Ozempic",
        "product_generic": "semaglutide",
        "nature_of_payment": "Consulting Fee",
        "amount_usd": "100.50",
        "matched_field": "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1",
        "matched_slot_count": 1,
        "source_year_dataset": "dataset-abc",
    }
    row.update(overrides)
    return row


# --- coerce_row (no network/DB) -----------------------------------------


def test_coerce_row_converts_types():
    row = coerce_row(_sample_row())
    assert row["record_id"] == "rec-1"
    assert row["program_year"] == 2023
    assert row["payment_date"].isoformat() == "2023-04-15"
    assert row["quarter"] == "2023Q2"
    assert row["amount_usd"] == Decimal("100.50")
    assert row["product_generic"] == "semaglutide"


def test_coerce_row_missing_record_id_raises():
    row = _sample_row()
    del row["record_id"]
    with pytest.raises(ValueError, match="record_id"):
        coerce_row(row)


def test_coerce_row_missing_required_field_raises():
    row = _sample_row(manufacturer="")
    with pytest.raises(ValueError, match="manufacturer"):
        coerce_row(row)


def test_coerce_row_unparseable_date_yields_none_date_but_keeps_row():
    row = coerce_row(_sample_row(payment_date="not-a-date", quarter=None))
    assert row["payment_date"] is None
    assert row["quarter"] is None
    # The row itself is still returned -- section 1's "full data, no
    # sampling" non-negotiable means a bad date must not drop the row.
    assert row["record_id"] == "rec-1"


def test_coerce_row_bad_amount_raises():
    with pytest.raises(ValueError, match="amount_usd"):
        coerce_row(_sample_row(amount_usd="not-a-number"))


# --- iter_matched_rows (no network/DB) -----------------------------------


def test_iter_matched_rows_skips_incomplete_year(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_matched_year(raw_dir, 2022, [_sample_row(record_id="rec-2022")], complete=False)
    _write_matched_year(raw_dir, 2023, [_sample_row(record_id="rec-2023")], complete=True)

    rows = list(iter_matched_rows(raw_dir))
    assert [r["record_id"] for r in rows] == ["rec-2023"]


def test_iter_matched_rows_filters_by_year(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_matched_year(raw_dir, 2022, [_sample_row(record_id="rec-2022")])
    _write_matched_year(raw_dir, 2023, [_sample_row(record_id="rec-2023")])

    rows = list(iter_matched_rows(raw_dir, years=[2023]))
    assert [r["record_id"] for r in rows] == ["rec-2023"]


def test_iter_matched_rows_on_missing_dir_yields_nothing(tmp_path):
    assert list(iter_matched_rows(tmp_path / "does-not-exist")) == []


# --- load_raw_dir / ensure_schema: real Postgres required ----------------


def _require_test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL is not set -- skipping the real-Postgres "
            "idempotency test. See .env.example (W-B1 note) for how to "
            "point this at a scratch database; CI sets it against a "
            "postgres:16 service container."
        )
    return url


@pytest.fixture
def database_url():
    url = _require_test_database_url()
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS payments CASCADE")
    yield url
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS payments CASCADE")


def _row_count(database_url: str) -> int:
    import psycopg

    with psycopg.connect(database_url) as conn:
        return conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]


def _fetch_row(database_url: str, record_id: str) -> tuple:
    import psycopg

    with psycopg.connect(database_url) as conn:
        return conn.execute(
            "SELECT amount_usd, manufacturer, quarter FROM payments WHERE record_id = %s",
            (record_id,),
        ).fetchone()


def test_ensure_schema_is_safe_to_run_twice(database_url):
    ensure_schema(database_url, SQL_PATHS)
    ensure_schema(database_url, SQL_PATHS)  # must not raise
    assert _row_count(database_url) == 0


def test_load_raw_dir_is_idempotent_on_rerun(tmp_path, database_url):
    ensure_schema(database_url, SQL_PATHS)
    raw_dir = tmp_path / "raw"
    rows = [
        _sample_row(record_id="rec-1", amount_usd="100.50"),
        _sample_row(record_id="rec-2", product="Mounjaro", manufacturer="Eli Lilly"),
    ]
    _write_matched_year(raw_dir, 2023, rows)

    stats_first = load_raw_dir(database_url, raw_dir)
    assert stats_first.rows_upserted == 2
    assert _row_count(database_url) == 2

    stats_second = load_raw_dir(database_url, raw_dir)
    assert stats_second.rows_upserted == 2
    # Re-running with the SAME raw files changes nothing (SPEC-area.md
    # section 3.3): still exactly 2 rows, not 4.
    assert _row_count(database_url) == 2
    amount, manufacturer, quarter = _fetch_row(database_url, "rec-1")
    assert amount == Decimal("100.50")
    assert manufacturer == "Novo Nordisk"
    assert quarter == "2023Q2"


def test_load_raw_dir_upserts_changed_values_on_reload(tmp_path, database_url):
    ensure_schema(database_url, SQL_PATHS)
    raw_dir = tmp_path / "raw"
    _write_matched_year(raw_dir, 2023, [_sample_row(record_id="rec-1", amount_usd="100.50")])
    load_raw_dir(database_url, raw_dir)

    # Same record_id, corrected amount -- simulates a corrected re-pull.
    _write_matched_year(raw_dir, 2023, [_sample_row(record_id="rec-1", amount_usd="150.00")])
    load_raw_dir(database_url, raw_dir)

    assert _row_count(database_url) == 1
    amount, _, _ = _fetch_row(database_url, "rec-1")
    assert amount == Decimal("150.00")


def test_load_raw_dir_skips_bad_rows_and_reports_them(tmp_path, database_url):
    ensure_schema(database_url, SQL_PATHS)
    raw_dir = tmp_path / "raw"
    good_row = _sample_row(record_id="rec-good")
    bad_row = _sample_row(record_id="rec-bad", manufacturer="")
    _write_matched_year(raw_dir, 2023, [good_row, bad_row])

    stats = load_raw_dir(database_url, raw_dir)

    assert stats.rows_seen == 2
    assert stats.rows_upserted == 1
    assert stats.rows_skipped_bad == 1
    assert len(stats.errors) == 1
    assert "rec-bad" in stats.errors[0]
    assert _row_count(database_url) == 1


def test_load_raw_dir_records_years_loaded(tmp_path, database_url):
    ensure_schema(database_url, SQL_PATHS)
    raw_dir = tmp_path / "raw"
    _write_matched_year(raw_dir, 2022, [_sample_row(record_id="rec-2022", program_year="2022")])
    _write_matched_year(raw_dir, 2023, [_sample_row(record_id="rec-2023", program_year="2023")])

    stats = load_raw_dir(database_url, raw_dir)
    assert stats.years_loaded == [2022, 2023]


def test_load_raw_dir_batch_size_produces_same_result_as_unbatched(tmp_path, database_url):
    # D17: batch_size batches upserts via executemany + periodic commits
    # instead of one execute()-per-row + a single end-of-run commit.
    # Batched and unbatched loads of the same rows must land identically.
    ensure_schema(database_url, SQL_PATHS)
    raw_dir = tmp_path / "raw"
    rows = [_sample_row(record_id=f"rec-batch-{i}", amount_usd=f"{i}.00") for i in range(7)]
    _write_matched_year(raw_dir, 2023, rows)

    stats = load_raw_dir(database_url, raw_dir, batch_size=3)
    assert stats.rows_upserted == 7
    assert _row_count(database_url) == 7

    # Re-running batched is still idempotent (D17 must not weaken D-idempotency).
    stats_again = load_raw_dir(database_url, raw_dir, batch_size=3)
    assert stats_again.rows_upserted == 7
    assert _row_count(database_url) == 7


def test_load_raw_dir_batch_size_skips_bad_rows_same_as_unbatched(tmp_path, database_url):
    ensure_schema(database_url, SQL_PATHS)
    raw_dir = tmp_path / "raw"
    good_rows = [_sample_row(record_id=f"rec-good-{i}") for i in range(3)]
    bad_row = _sample_row(record_id="rec-bad-batch", manufacturer="")
    _write_matched_year(raw_dir, 2023, [*good_rows, bad_row])

    stats = load_raw_dir(database_url, raw_dir, batch_size=2)
    assert stats.rows_seen == 4
    assert stats.rows_upserted == 3
    assert stats.rows_skipped_bad == 1
    assert _row_count(database_url) == 3

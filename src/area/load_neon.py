"""Loads GLP-1-matched CMS Open Payments rows into the Postgres `payments`
table (SPEC-area.md section 3.3).

Reads from `raw/<year>/matched.jsonl.gz` -- the raw, filtered records
pull_open_payments.py wrote -- for every year whose `raw/<year>/manifest.json`
says `"complete": true`. A year with no manifest, no matched file, or an
incomplete pull is skipped with a note on stderr rather than loaded
partially: SPEC-area.md section 1's "full data ... no sampling"
non-negotiable means a partially-loaded year would silently under-count
that year, which is worse than not loading it yet.

Idempotent by upsert on `record_id` (CMS's own primary key, per section
3.3): re-running against the same raw files changes nothing. See
tests/test_load_idempotent.py, which proves this against a real scratch
Postgres (TEST_DATABASE_URL) using sql/001_schema.sql's real DDL, not a
mock.

psycopg is imported lazily inside the functions that need a real database
connection, the same pattern as model-bench's boto3 (see model-bench's
CONTEXT.md decision D10) and this repo's own pyproject.toml note: nothing
in this module needs psycopg installed to be imported or to run the
type-coercion logic offline.
"""

from __future__ import annotations

import gzip
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

UPSERT_SQL = """
INSERT INTO payments (
    record_id, program_year, payment_date, quarter, physician_id,
    physician_specialty, physician_state, manufacturer, product,
    product_generic, nature_of_payment, amount_usd, matched_field,
    source_year_dataset
) VALUES (
    %(record_id)s, %(program_year)s, %(payment_date)s, %(quarter)s, %(physician_id)s,
    %(physician_specialty)s, %(physician_state)s, %(manufacturer)s, %(product)s,
    %(product_generic)s, %(nature_of_payment)s, %(amount_usd)s, %(matched_field)s,
    %(source_year_dataset)s
)
ON CONFLICT (record_id) DO UPDATE SET
    program_year = EXCLUDED.program_year,
    payment_date = EXCLUDED.payment_date,
    quarter = EXCLUDED.quarter,
    physician_id = EXCLUDED.physician_id,
    physician_specialty = EXCLUDED.physician_specialty,
    physician_state = EXCLUDED.physician_state,
    manufacturer = EXCLUDED.manufacturer,
    product = EXCLUDED.product,
    product_generic = EXCLUDED.product_generic,
    nature_of_payment = EXCLUDED.nature_of_payment,
    amount_usd = EXCLUDED.amount_usd,
    matched_field = EXCLUDED.matched_field,
    source_year_dataset = EXCLUDED.source_year_dataset
"""

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y")


def _lazy_psycopg():
    import psycopg  # noqa: PLC0415 -- deliberately lazy, see module docstring

    return psycopg


def _parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def coerce_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Converts one matched.jsonl.gz record (the strings process_row wrote)
    into the payments table's real column types. Raises ValueError, naming
    the record_id, if a column the spec marks NOT NULL (section 3.3) is
    missing -- that is a real data-quality problem worth stopping the load
    on, not a value load_neon.py should invent (BUILD INSTRUCTION: no
    fake/invented numbers, ever)."""
    record_id = raw.get("record_id")
    if not record_id:
        raise ValueError(f"row has no record_id, cannot load: {raw!r}")

    def require(field_name: str) -> str:
        value = raw.get(field_name)
        if not value:
            raise ValueError(f"record_id={record_id}: required field {field_name!r} is missing")
        return value

    try:
        program_year = int(require("program_year"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"record_id={record_id}: program_year is not an int: {exc}") from exc

    try:
        amount_usd = Decimal(require("amount_usd"))
    except InvalidOperation as exc:
        raise ValueError(f"record_id={record_id}: amount_usd is not numeric: {exc}") from exc

    return {
        "record_id": record_id,
        "program_year": program_year,
        "payment_date": _parse_date(raw.get("payment_date")),
        "quarter": raw.get("quarter") or None,
        "physician_id": raw.get("physician_id") or None,
        "physician_specialty": raw.get("physician_specialty") or None,
        "physician_state": raw.get("physician_state") or None,
        "manufacturer": require("manufacturer"),
        "product": require("product"),
        "product_generic": raw.get("product_generic") or None,
        "nature_of_payment": raw.get("nature_of_payment") or None,
        "amount_usd": amount_usd,
        "matched_field": require("matched_field"),
        "source_year_dataset": require("source_year_dataset"),
    }


def iter_matched_rows(raw_dir: Path, years: list[int] | None = None):
    """Yields raw matched-row dicts (process_row's output, as JSON) from
    every complete year under raw_dir, in year order."""
    if not raw_dir.exists():
        return
    for year_dir in sorted(p for p in raw_dir.iterdir() if p.is_dir()):
        try:
            year = int(year_dir.name)
        except ValueError:
            continue
        if years is not None and year not in years:
            continue
        manifest_path = year_dir / "manifest.json"
        matched_path = year_dir / "matched.jsonl.gz"
        if not manifest_path.exists() or not matched_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text())
        if not manifest.get("complete"):
            print(
                f"load_neon: skipping {year_dir.name} -- pull not complete "
                f"({manifest.get('error') or 'no error recorded'})",
                file=sys.stderr,
            )
            continue
        with gzip.open(matched_path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


@dataclass
class LoadStats:
    rows_seen: int = 0
    rows_upserted: int = 0
    rows_skipped_bad: int = 0
    years_loaded: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def ensure_schema(database_url: str, sql_paths: list[Path]) -> None:
    """Applies each schema file in sql_paths, in order. Every statement in
    sql/001_schema.sql and sql/002_views.sql is itself idempotent (CREATE
    TABLE IF NOT EXISTS, CREATE OR REPLACE VIEW, a guarded CREATE ROLE), so
    running this against a database that already has the schema is a safe
    no-op -- this is what makes the idempotency test able to call it on
    every run without first checking whether the schema already exists."""
    psycopg = _lazy_psycopg()
    with psycopg.connect(database_url, autocommit=True) as conn:
        for path in sql_paths:
            conn.execute(path.read_text())


def load_raw_dir(
    database_url: str, raw_dir: Path, years: list[int] | None = None
) -> LoadStats:
    """Loads every complete year's matched rows under raw_dir into the
    payments table via upsert on record_id. Does NOT call ensure_schema --
    callers (the CLI, tests) call that explicitly first so a load never
    silently creates schema the caller didn't ask for."""
    psycopg = _lazy_psycopg()
    stats = LoadStats()
    years_seen: set[int] = set()
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for raw in iter_matched_rows(raw_dir, years=years):
                stats.rows_seen += 1
                try:
                    row = coerce_row(raw)
                except ValueError as exc:
                    stats.rows_skipped_bad += 1
                    stats.errors.append(str(exc))
                    continue
                cur.execute(UPSERT_SQL, row)
                stats.rows_upserted += 1
                years_seen.add(row["program_year"])
        conn.commit()
    stats.years_loaded = sorted(years_seen)
    return stats

"""Tests for area.tools.query_tool (SPEC-area.md section 4.2). Named
test cases from the spec: injection strings, UPDATE/DELETE,
multi-statement, non-allow-listed column, missing LIMIT.

The validator itself (validate_and_normalize) needs no network, no
secrets, and no database -- these are the bulk of the tests here, per
task-split section 10's "no secrets for tests" for W-B2. A second group
of tests proves the tool's full run() path (validate -> execute ->
evidence) against a REAL scratch Postgres (TEST_DATABASE_URL), honestly
skipped with a stated reason when that's unset -- same pattern as
tests/test_load_idempotent.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from area.load_neon import ensure_schema, load_raw_dir
from area.tools.query_tool import QueryRejected, run, validate_and_normalize

REPO_ROOT = Path(__file__).resolve().parent.parent
SQL_PATHS = [REPO_ROOT / "sql" / "001_schema.sql", REPO_ROOT / "sql" / "002_views.sql"]


# --- validator: injection strings ----------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM payments WHERE record_id = '' OR ''=''; DROP TABLE payments;--",
        "SELECT * FROM payments UNION SELECT usename, passwd, NULL FROM pg_shadow",
        "SELECT * FROM payments WHERE 1=1; SELECT * FROM payments",
    ],
)
def test_injection_strings_are_rejected(sql):
    with pytest.raises(QueryRejected):
        validate_and_normalize(sql)


# --- validator: UPDATE/DELETE (and other non-SELECT statement types) ----


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE payments SET amount_usd = 0",
        "DELETE FROM payments",
        "INSERT INTO payments (record_id) VALUES ('x')",
        "DROP TABLE payments",
        "TRUNCATE payments",
        "CREATE TABLE evil (id int)",
    ],
)
def test_non_select_statements_are_rejected(sql):
    with pytest.raises(QueryRejected):
        validate_and_normalize(sql)


# --- validator: multi-statement -------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM payments LIMIT 5; SELECT * FROM q_totals LIMIT 5;",
        "SELECT * FROM payments LIMIT 5; DROP TABLE payments;",
    ],
)
def test_multi_statement_queries_are_rejected(sql):
    with pytest.raises(QueryRejected, match="multiple statements"):
        validate_and_normalize(sql)


def test_single_statement_with_one_trailing_semicolon_is_fine():
    result = validate_and_normalize("SELECT * FROM payments LIMIT 5;")
    assert result == "SELECT * FROM payments LIMIT 5"


# --- validator: non-allow-listed table/column -----------------------------


def test_non_allowlisted_table_is_rejected():
    with pytest.raises(QueryRejected, match="not allow-listed"):
        validate_and_normalize("SELECT * FROM pg_shadow LIMIT 5")


def test_non_allowlisted_column_is_rejected():
    with pytest.raises(QueryRejected, match="not allow-listed"):
        validate_and_normalize("SELECT secret_internal_notes FROM payments LIMIT 5")


def test_allowlisted_columns_across_all_tables_and_views_pass():
    for sql in [
        "SELECT record_id, amount_usd, manufacturer FROM payments LIMIT 5",
        "SELECT quarter, product, manufacturer, total_usd, payment_count FROM q_totals LIMIT 5",
        "SELECT quarter, physician_specialty, total_usd FROM q_by_specialty LIMIT 5",
        "SELECT quarter, physician_state, payment_count FROM q_by_state LIMIT 5",
    ]:
        validate_and_normalize(sql)  # must not raise


# --- validator: missing LIMIT ---------------------------------------------


def test_missing_limit_is_added_not_rejected():
    result = validate_and_normalize("SELECT quarter, amount_usd FROM payments")
    assert result.rstrip().endswith("LIMIT 5000")


def test_existing_limit_is_left_alone():
    result = validate_and_normalize("SELECT * FROM payments LIMIT 10")
    assert result.count("LIMIT") == 1
    assert "LIMIT 10" in result


# --- validator: CTEs and aliases still work (not a named test case, but --
# --- the SQL an LLM writes against this schema realistically uses both) --


def test_cte_and_joins_and_aliases_are_supported():
    sql = (
        "WITH recent AS (SELECT * FROM payments WHERE quarter = '2025Q1') "
        "SELECT p.quarter, p.amount_usd FROM payments p "
        "JOIN q_totals q ON p.quarter = q.quarter "
        "LIMIT 5"
    )
    validate_and_normalize(sql)  # must not raise


# --- validator: FILTER (WHERE ...) and NULLIF -- found via a real run of --
# --- data/facts.md's own query 5 against real Postgres (not a hypothetical:
# --- the validator originally rejected this exact spec-provided SQL) ------


def test_facts_md_query_5_with_filter_and_nullif_is_not_rejected():
    sql = (
        "SELECT ROUND(100.0 * SUM(amount_usd) FILTER "
        "(WHERE nature_of_payment = 'Food and Beverage') / NULLIF(SUM(amount_usd), 0), 2) "
        "AS food_and_beverage_share_pct FROM payments"
    )
    validate_and_normalize(sql)  # must not raise


def test_filter_clause_still_checks_its_column_reference():
    with pytest.raises(QueryRejected, match="not allow-listed"):
        validate_and_normalize(
            "SELECT SUM(amount_usd) FILTER (WHERE secret_internal_notes = 'x') FROM payments"
        )


# --- run(): the rejection path never touches a database -------------------


def test_run_returns_not_ok_with_reason_when_query_is_rejected():
    result = run(
        {"question": "delete everything"}, generate_sql=lambda q, h: "DELETE FROM payments"
    )
    assert result.ok is False
    assert "query rejected" in result.error
    assert result.evidence == []


def test_run_returns_not_ok_when_generate_sql_raises():
    def _boom(question, hint):
        raise RuntimeError("model unavailable")

    result = run({"question": "anything"}, generate_sql=_boom)
    assert result.ok is False
    assert "could not generate SQL" in result.error


def test_run_reports_clearly_when_no_database_is_configured(monkeypatch):
    monkeypatch.delenv("AREA_READER_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = run(
        {"question": "how many rows"},
        generate_sql=lambda q, h: "SELECT COUNT(*) FROM payments",
        database_url=None,
    )
    assert result.ok is False
    assert "no database configured" in result.error


# --- run(): full path against a REAL scratch Postgres ----------------------


def _require_test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL is not set -- skipping the real-Postgres "
            "query_tool execution test. See .env.example."
        )
    return url


@pytest.fixture
def database_url():
    url = _require_test_database_url()
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS payments CASCADE")
    ensure_schema(url, SQL_PATHS)
    yield url
    import psycopg as psycopg2  # noqa: PLC0415 -- teardown, keep import local

    with psycopg2.connect(url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS payments CASCADE")


def _seed_one_row(database_url: str, tmp_path: Path) -> None:
    import gzip
    import json

    raw_dir = tmp_path / "raw"
    year_dir = raw_dir / "2023"
    year_dir.mkdir(parents=True)
    row = {
        "record_id": "rec-1",
        "program_year": "2023",
        "payment_date": "2023-04-15",
        "quarter": "2023Q2",
        "physician_id": "1",
        "physician_specialty": "Endocrinology",
        "physician_state": "CA",
        "manufacturer": "Novo Nordisk",
        "product": "Ozempic",
        "product_generic": "semaglutide",
        "nature_of_payment": "Consulting Fee",
        "amount_usd": "250.00",
        "matched_field": "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1",
        "source_year_dataset": "test-ds",
    }
    with gzip.open(year_dir / "matched.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    (year_dir / "manifest.json").write_text(json.dumps({"complete": True, "rows_matched": 1}))
    load_raw_dir(database_url, raw_dir)


def test_run_executes_a_valid_query_against_real_postgres(database_url, tmp_path):
    _seed_one_row(database_url, tmp_path)

    sql = "SELECT quarter, amount_usd FROM payments WHERE quarter = '2023Q2'"
    result = run(
        {"question": "how much did Novo Nordisk pay in 2023Q2"},
        generate_sql=lambda q, h: sql,
        database_url=database_url,
    )

    assert result.ok is True
    assert result.data["row_count"] == 1
    assert result.data["rows"][0]["amount_usd"] is not None
    assert result.data["sql"].rstrip().endswith("LIMIT 5000")
    assert len(result.evidence) == 1
    assert result.evidence[0].evidence_id.startswith("q_")
    assert result.evidence[0].kind == "sql_rows"
    assert result.evidence[0].payload == result.data["rows"]


def test_run_rejects_before_ever_reaching_the_database(database_url, tmp_path):
    _seed_one_row(database_url, tmp_path)

    result = run(
        {"question": "wipe it"},
        generate_sql=lambda q, h: "DELETE FROM payments",
        database_url=database_url,
    )
    assert result.ok is False
    assert "query rejected" in result.error

    # Prove the row is still there -- the rejected query never executed.
    import psycopg

    with psycopg.connect(database_url) as conn:
        count = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    assert count == 1

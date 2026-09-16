"""Smoke tests for area/cli.py. No real network or database for most
cases: pull/load's own logic is already covered by
test_pull_open_payments.py and test_load_idempotent.py, and
tools-test/forecast's own tool/model logic is already covered by
test_query_tool_guardrails.py, test_forecast_tool.py,
test_citation_checker.py, and test_forecast_holdout.py -- so this file
only checks the CLI wires arguments, defaults, and exit codes correctly,
via monkeypatched stand-ins for the real functions (plus a couple of
real-Postgres end-to-end checks for tools-test, honestly skipped with a
stated reason when TEST_DATABASE_URL is unset -- same pattern as
test_query_tool_guardrails.py).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import area.cli as cli_module
from area.cli import main


def test_causal_not_yet_built_command_returns_1_and_names_the_task(capsys):
    # "causal" (W-B3) has no module yet; "run"/"evals" (W-B4) are the same
    # NOT_YET_BUILT path. tools-test/forecast (W-B2) are built now -- see
    # the tests below.
    exit_code = main(["causal"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "not built yet" in err
    assert "W-B3" in err


def test_load_without_database_url_returns_1(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    exit_code = main(["load"])
    assert exit_code == 1
    assert "DATABASE_URL" in capsys.readouterr().err


def test_pull_success_returns_0_and_reports_each_year(monkeypatch, capsys):
    def fake_pull_all_years(out_dir, years=None, force=False):
        assert isinstance(out_dir, Path)
        return [
            {"year": 2023, "complete": True, "rows_matched": 5, "rows_scanned": 20, "error": None},
            {"year": 2024, "complete": True, "rows_matched": 7, "rows_scanned": 25, "error": None},
        ]

    monkeypatch.setattr(cli_module, "pull_all_years", fake_pull_all_years)
    exit_code = main(["pull"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "2023: OK" in out
    assert "2024: OK" in out


def test_pull_reports_failure_and_returns_1(monkeypatch, capsys):
    def fake_pull_all_years(out_dir, years=None, force=False):
        return [
            {"year": 2023, "complete": False, "error": "boom", "rows_matched": 0, "rows_scanned": 0}
        ]

    monkeypatch.setattr(cli_module, "pull_all_years", fake_pull_all_years)
    exit_code = main(["pull"])
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "2023: FAILED: boom" in out


def test_pull_stream_without_s3_bucket_returns_1(capsys):
    exit_code = main(["pull", "--stream"])
    assert exit_code == 1
    assert "--s3-bucket" in capsys.readouterr().err


def test_pull_stream_calls_pull_all_years_with_stream_kwargs(monkeypatch, capsys):
    calls = []

    def fake_pull_all_years(out_dir, years=None, force=False, stream=False, s3_bucket=None,
                             database_url=None):
        calls.append((stream, s3_bucket, database_url))
        return [
            {
                "year": 2023, "complete": True, "rows_matched": 5, "rows_scanned": 20,
                "error": None, "bytes": 1234, "s3_bucket": s3_bucket,
                "s3_key": "raw/2023/foo.csv",
            }
        ]

    monkeypatch.setattr(cli_module, "pull_all_years", fake_pull_all_years)
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/db")
    exit_code = main(["pull", "--stream", "--s3-bucket", "giggit-area-raw-payments"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert calls == [(True, "giggit-area-raw-payments", "postgresql://example/db")]
    assert "1234 bytes -> s3://giggit-area-raw-payments/raw/2023/foo.csv" in out


def test_load_calls_ensure_schema_then_load_raw_dir(monkeypatch, capsys):
    calls = []

    def fake_ensure_schema(database_url, sql_paths):
        calls.append(("ensure_schema", database_url))

    def fake_load_raw_dir(database_url, raw_dir, years=None, batch_size=None):
        calls.append(("load_raw_dir", database_url, raw_dir, batch_size))

        class Stats:
            rows_upserted = 3
            rows_skipped_bad = 0
            years_loaded = [2023]
            errors: list[str] = []

        return Stats()

    monkeypatch.setattr(cli_module, "ensure_schema", fake_ensure_schema)
    monkeypatch.setattr(cli_module, "load_raw_dir", fake_load_raw_dir)

    exit_code = main(["load", "--database-url", "postgresql://example/db"])

    assert exit_code == 0
    assert calls[0] == ("ensure_schema", "postgresql://example/db")
    assert calls[1][0] == "load_raw_dir"
    assert calls[1][3] is None  # --stream not passed -> batch_size stays None
    out = capsys.readouterr().out
    assert "loaded 3 rows" in out


def test_load_stream_passes_batch_size(monkeypatch, capsys):
    calls = []

    monkeypatch.setattr(cli_module, "ensure_schema", lambda database_url, sql_paths: None)

    def fake_load_raw_dir(database_url, raw_dir, years=None, batch_size=None):
        calls.append(batch_size)

        class Stats:
            rows_upserted = 0
            rows_skipped_bad = 0
            years_loaded: list[int] = []
            errors: list[str] = []

        return Stats()

    monkeypatch.setattr(cli_module, "load_raw_dir", fake_load_raw_dir)
    exit_code = main(
        ["load", "--database-url", "postgresql://example/db", "--stream", "--batch-size", "500"]
    )
    assert exit_code == 0
    assert calls == [500]


# --- tools-test (W-B2) ------------------------------------------------------


def test_tools_test_with_no_database_or_forecast_file_is_honest_not_a_lie(
    monkeypatch, tmp_path, capsys
):
    """citation_checker needs no secrets/database, so it's a real PASS.
    forecast_tool and query_tool (and the 5 facts) both depend on
    preconditions this repo doesn't have yet (a real data pull -- CONTEXT.md
    D11 defers that to Gate 1), so the command must say so plainly rather
    than failing OR silently printing nothing."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AREA_READER_DATABASE_URL", raising=False)
    missing_forecast = tmp_path / "forecast" / "latest.json"

    exit_code = main(["tools-test", "--forecast-path", str(missing_forecast)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "citation_checker: PASS" in out
    assert "forecast_tool: NOT AVAILABLE YET" in out
    assert "query_tool: NOT AVAILABLE" in out
    assert out.count("PENDING -- awaiting real data load") == 5


def _require_test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL is not set -- skipping the real-Postgres "
            "tools-test end-to-end check. See .env.example."
        )
    return url


def test_tools_test_against_real_postgres_prints_real_facts(tmp_path, capsys):
    """Same real-scratch-Postgres pattern as test_query_tool_guardrails.py:
    seeds one real row, then proves `area tools-test` reports query_tool
    PASS and prints an actual computed fact -- never PENDING -- once a
    database is configured."""
    import gzip
    import json

    import psycopg

    from area.load_neon import ensure_schema, load_raw_dir

    database_url = _require_test_database_url()
    sql_paths = [
        Path(cli_module.__file__).resolve().parent.parent.parent / "sql" / "001_schema.sql",
        Path(cli_module.__file__).resolve().parent.parent.parent / "sql" / "002_views.sql",
    ]
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS payments CASCADE")
    ensure_schema(database_url, sql_paths)

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
        "nature_of_payment": "Food and Beverage",
        "amount_usd": "250.00",
        "matched_field": "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1",
        "source_year_dataset": "test-ds",
    }
    with gzip.open(year_dir / "matched.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    (year_dir / "manifest.json").write_text(json.dumps({"complete": True, "rows_matched": 1}))
    load_raw_dir(database_url, raw_dir)

    try:
        exit_code = main(
            [
                "tools-test",
                "--database-url",
                database_url,
                "--forecast-path",
                str(tmp_path / "forecast" / "latest.json"),
            ]
        )
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "query_tool: PASS" in out
        assert "PENDING" not in out
        assert "Total dollars, 2021-2025: {'total_usd': Decimal('250.00')}" in out
        assert "Share of dollars that are Food and Beverage:" in out
        assert "'food_and_beverage_share_pct': Decimal('100.00')" in out
    finally:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS payments CASCADE")


# --- forecast (W-B2) ---------------------------------------------------------


def test_forecast_missing_raw_dir_returns_1(tmp_path, capsys):
    exit_code = main(["forecast", "--raw-dir", str(tmp_path / "does-not-exist")])
    assert exit_code == 1
    assert "does not exist" in capsys.readouterr().err


def test_forecast_reports_computed_and_skipped_series(monkeypatch, tmp_path, capsys):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    out_path = tmp_path / "forecast" / "latest.json"

    def fake_holdout_run(raw_dir_arg, out_path_arg, top_n_specialties=5):
        assert raw_dir_arg == raw_dir
        assert out_path_arg == out_path
        assert top_n_specialties == 3
        return {
            "series": {"national": {"primary_model": "ets"}},
            "skipped": {"specialty:Rare": "only 3 quarters of data, need at least 8"},
        }

    monkeypatch.setattr("area.forecast.holdout.run", fake_holdout_run)

    exit_code = main(
        [
            "forecast",
            "--raw-dir",
            str(raw_dir),
            "--out-path",
            str(out_path),
            "--top-n-specialties",
            "3",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "1 series computed, 1 skipped" in out
    assert "national: primary_model=ets" in out
    assert "specialty:Rare: SKIPPED -- only 3 quarters of data, need at least 8" in out


# --- run / evals (W-B4) -------------------------------------------------


def test_run_prints_accepted_answer_and_exits_0(monkeypatch, capsys):
    from area.agent.loop import AgentTrace

    def fake_run_agent(question, **kwargs):
        assert question == 'q?'
        trace = AgentTrace(question=question, model_id='m', adapter='fake')
        trace.answer_text = 'A cited answer [q_x].'
        trace.accepted = True
        return trace

    monkeypatch.setattr('area.agent.loop.run_agent', fake_run_agent)
    exit_code = main(['run', 'q?'])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert '[ACCEPTED]' in out
    assert 'A cited answer [q_x].' in out


def test_run_exits_1_when_answer_not_accepted(monkeypatch, capsys):
    from area.agent.loop import AgentTrace

    def fake_run_agent(question, **kwargs):
        trace = AgentTrace(question=question, model_id='m', adapter='fake')
        trace.answer_text = 'An uncited $5.'
        trace.accepted = False
        trace.unverified = ['$5']
        return trace

    monkeypatch.setattr('area.agent.loop.run_agent', fake_run_agent)
    exit_code = main(['run', 'q?'])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert 'NOT ACCEPTED' in captured.out
    assert 'Unverified' in captured.err


def test_run_reports_loop_error_and_exits_1(monkeypatch, capsys):
    from area.agent.loop import AgentTrace

    def fake_run_agent(question, **kwargs):
        trace = AgentTrace(question=question, model_id='m', adapter='fake')
        trace.error = 'planner call failed: boom'
        return trace

    monkeypatch.setattr('area.agent.loop.run_agent', fake_run_agent)
    exit_code = main(['run', 'q?'])
    err = capsys.readouterr().err
    assert exit_code == 1
    assert 'planner call failed: boom' in err


def test_evals_reports_pass_count_and_writes_summary(monkeypatch, tmp_path, capsys):
    from area.evals.runner import EvalCaseResult, EvalsSummary

    def fake_run_evals(cases=None, **kwargs):
        assert kwargs['trace_dir'] == tmp_path / 'traces'
        summary = EvalsSummary(model_id='m', adapter='fake')
        summary.results = [
            EvalCaseResult(
                case_id='c1', question='q1', passed=True, accepted=True, error=None,
                answer_text='ok [e1]', input_tokens=10, output_tokens=5,
                latency_ms=12.0, calls=2, trace_path=str(tmp_path / 'traces' / 'c1.json'),
            ),
            EvalCaseResult(
                case_id='c2', question='q2', passed=False, accepted=False,
                error='planner call failed: boom', answer_text=None,
                input_tokens=3, output_tokens=0, latency_ms=4.0, calls=1, trace_path=None,
            ),
        ]
        summary.started_at = 'a'
        summary.finished_at = 'b'
        return summary

    monkeypatch.setattr('area.evals.runner.run_evals', fake_run_evals)
    out_path = tmp_path / 'summary.json'
    exit_code = main(
        [
            'evals',
            '--trace-dir', str(tmp_path / 'traces'),
            '--out-path', str(out_path),
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 1  # not all cases passed
    assert 'c1: PASS' in out
    assert 'c2: FAIL' in out
    assert '1/2 passed' in out
    assert out_path.exists()
    import json as _json
    written = _json.loads(out_path.read_text())
    assert written['passed'] == 1
    assert written['total'] == 2

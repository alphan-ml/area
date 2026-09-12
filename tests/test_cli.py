"""Smoke tests for area/cli.py. No real network or database: pull/load's
own logic is already covered by test_pull_open_payments.py and
test_load_idempotent.py, so this only checks the CLI wires arguments and
exit codes correctly, via monkeypatched stand-ins for the real functions.
"""

from __future__ import annotations

from pathlib import Path

import area.cli as cli_module
from area.cli import main


def test_not_yet_built_command_returns_1_and_names_the_task(capsys):
    exit_code = main(["tools-test"])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "not built yet" in err
    assert "W-B2" in err


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


def test_load_calls_ensure_schema_then_load_raw_dir(monkeypatch, capsys):
    calls = []

    def fake_ensure_schema(database_url, sql_paths):
        calls.append(("ensure_schema", database_url))

    def fake_load_raw_dir(database_url, raw_dir, years=None):
        calls.append(("load_raw_dir", database_url, raw_dir))

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
    out = capsys.readouterr().out
    assert "loaded 3 rows" in out

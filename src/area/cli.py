"""AREA CLI (SPEC-area.md section 2): `area pull | load | tools-test | run
"<question>" | evals | forecast | causal`.

W-B1 wired `pull` (pull_open_payments.py) and `load` (load_neon.py).
W-B2 wires `tools-test` (runs the three tools -- query_tool, forecast_tool,
citation_checker -- and prints the 5 headline facts, per acceptance
criterion 2) and `forecast` (builds quarterly series from raw pulled data
and writes forecast/latest.json, per section 4.3). `run`/`evals`/`causal`
belong to later tasks (W-B4/B3) whose modules don't exist yet; calling
them now prints a plain "not built yet" message and exits 1, rather than
a traceback -- `pyproject.toml` already declares this file as the `area`
console script's entry point, so it has to import cleanly and behave
sanely today even though some subcommands are still to come.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from area.load_neon import ensure_schema, load_raw_dir
from area.pull_open_payments import YEARS, pull_all_years

NOT_YET_BUILT = {
    "run": "W-B4",
    "evals": "W-B4",
    "causal": "W-B3",
}

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SQL_DIR = REPO_ROOT / "sql"
SCHEMA_SQL_PATHS = [SQL_DIR / "001_schema.sql", SQL_DIR / "002_views.sql"]
FORECAST_OUT_PATH = REPO_ROOT / "forecast" / "latest.json"

# The same 5 queries as data/facts.md (SPEC-area.md section 3.4), kept
# deliberately in sync with that file: facts.md is the human-readable,
# hand-verified version with the PENDING/real-value notes; this is the
# executable version `area tools-test` runs live when a database is
# configured. See data/facts.md's own note on why no placeholder number
# is ever printed here instead.
FACTS_QUERIES = [
    (
        "Total dollars, 2021-2025",
        "SELECT SUM(amount_usd) AS total_usd FROM payments "
        "WHERE program_year BETWEEN 2021 AND 2025",
    ),
    (
        "Top specialty by dollars",
        "SELECT physician_specialty, SUM(amount_usd) AS total_usd FROM payments "
        "WHERE physician_specialty IS NOT NULL GROUP BY physician_specialty "
        "ORDER BY total_usd DESC LIMIT 1",
    ),
    (
        "Top state by dollars",
        "SELECT physician_state, SUM(amount_usd) AS total_usd FROM payments "
        "WHERE physician_state IS NOT NULL GROUP BY physician_state "
        "ORDER BY total_usd DESC LIMIT 1",
    ),
    (
        "Dollars per quarter (series, first 4 shown)",
        "SELECT quarter, SUM(amount_usd) AS total_usd FROM payments "
        "WHERE quarter IS NOT NULL GROUP BY quarter ORDER BY quarter LIMIT 4",
    ),
    (
        "Share of dollars that are Food and Beverage",
        "SELECT ROUND(100.0 * SUM(amount_usd) FILTER "
        "(WHERE nature_of_payment = 'Food and Beverage') / NULLIF(SUM(amount_usd), 0), 2) "
        "AS food_and_beverage_share_pct FROM payments",
    ),
]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="area")
    sub = parser.add_subparsers(dest="command", required=True)

    pull_p = sub.add_parser("pull", help="Pull CMS Open Payments data (W-B1)")
    pull_p.add_argument("--out-dir", default="raw")
    pull_p.add_argument("--year", type=int, action="append", dest="years")
    pull_p.add_argument("--force", action="store_true")

    load_p = sub.add_parser("load", help="Load raw pulled data into Postgres (W-B1)")
    load_p.add_argument("--raw-dir", default="raw")
    load_p.add_argument("--year", type=int, action="append", dest="years")
    load_p.add_argument("--database-url", default=None)

    tools_test_p = sub.add_parser(
        "tools-test",
        help="Smoke-test query_tool/forecast_tool/citation_checker, print the 5 facts (W-B2)",
    )
    tools_test_p.add_argument("--database-url", default=None)
    tools_test_p.add_argument("--forecast-path", default=None)

    forecast_p = sub.add_parser(
        "forecast",
        help="Build quarterly series from raw pulled data, write forecast/latest.json (W-B2)",
    )
    forecast_p.add_argument("--raw-dir", default="raw")
    forecast_p.add_argument("--out-path", default=None)
    forecast_p.add_argument("--top-n-specialties", type=int, default=5)

    for name, task in sorted(NOT_YET_BUILT.items()):
        sub.add_parser(name, help=f"(not built yet -- see SPEC-area.md task {task})")

    return parser


def _run_pull(args: argparse.Namespace) -> int:
    manifests = pull_all_years(Path(args.out_dir), years=args.years or YEARS, force=args.force)
    for m in manifests:
        status = "OK" if m["complete"] else f"FAILED: {m['error']}"
        print(
            f"{m['year']}: {status} -- "
            f"{m.get('rows_matched', 0)} matched / {m.get('rows_scanned', 0)} scanned"
        )
    return 0 if all(m["complete"] for m in manifests) else 1


def _run_load(args: argparse.Namespace) -> int:
    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "area load: no --database-url given and DATABASE_URL is not set.",
            file=sys.stderr,
        )
        return 1
    ensure_schema(database_url, SCHEMA_SQL_PATHS)
    stats = load_raw_dir(database_url, Path(args.raw_dir), years=args.years)
    print(
        f"loaded {stats.rows_upserted} rows ({stats.rows_skipped_bad} skipped) "
        f"across years {stats.years_loaded}"
    )
    if stats.errors:
        print(f"{len(stats.errors)} row-level error(s), e.g.: {stats.errors[0]}", file=sys.stderr)
    return 0


def _run_tools_test(args: argparse.Namespace) -> int:
    """Acceptance criterion 2: 'area tools-test runs all three [tools]
    ... and prints the 5 facts.' citation_checker needs no secrets or
    database, so its result below is a real PASS/FAIL. forecast_tool and
    query_tool (and the 5 facts) each depend on a precondition this repo
    doesn't have yet -- forecast/latest.json (needs `area forecast` run
    against real pulled data) and a configured database -- both deferred
    to Gate 1 by Leon's decision D11. Rather than skip them silently or
    fake a result, this prints a plain "NOT AVAILABLE" line stating why,
    and only fails the command (non-zero exit) on an actual tool failure,
    never on an unmet Gate-1 precondition."""
    from area.tools import registry
    from area.tools.forecast_tool import run as forecast_tool_run
    from area.tools.query_tool import run as query_tool_run

    tools = registry()
    overall_ok = True

    cc_result = tools["citation_checker"].fn(
        {
            "answer_text": "Total was $5.00 [self_test].",
            "evidence": [
                {"evidence_id": "self_test", "kind": "sql_rows", "payload": [{"total_usd": 5.0}]}
            ],
        }
    )
    cc_ok = cc_result.ok and cc_result.data["accepted"] is True
    overall_ok = overall_ok and cc_ok
    print(f"citation_checker: {'PASS' if cc_ok else 'FAIL'} (self-contained smoke test)")

    forecast_path = Path(args.forecast_path) if args.forecast_path else FORECAST_OUT_PATH
    if not forecast_path.exists():
        print(
            f"forecast_tool: NOT AVAILABLE YET -- {forecast_path} does not exist "
            "(run `area forecast` after a real data pull; see CONTEXT.md D11)"
        )
    else:
        ft_result = forecast_tool_run({"series": "national"}, path=forecast_path)
        overall_ok = overall_ok and ft_result.ok
        status = "PASS" if ft_result.ok else "FAIL"
        detail = ft_result.error or "national series read OK"
        print(f"forecast_tool: {status} ({detail})")

    database_url = (
        args.database_url
        or os.environ.get("AREA_READER_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    print()
    print("5 headline facts (data/facts.md):")
    if not database_url:
        print(
            "query_tool: NOT AVAILABLE -- no database configured "
            "(set --database-url, AREA_READER_DATABASE_URL, or DATABASE_URL; "
            "real pull/load is deferred to Gate 1 per CONTEXT.md D11)"
        )
        for name, _sql in FACTS_QUERIES:
            print(f"  {name}: PENDING -- awaiting real data load")
        return 0 if overall_ok else 1

    qt_result = query_tool_run(
        {"question": "smoke test"},
        generate_sql=lambda q, h: "SELECT 1 AS smoke_test",
        database_url=database_url,
    )
    overall_ok = overall_ok and qt_result.ok
    qt_detail = qt_result.error or "smoke query OK"
    print(f"query_tool: {'PASS' if qt_result.ok else 'FAIL'} ({qt_detail})")

    for name, sql in FACTS_QUERIES:
        fact_result = query_tool_run(
            {"question": name},
            generate_sql=lambda q, h, _sql=sql: _sql,
            database_url=database_url,
        )
        if not fact_result.ok:
            overall_ok = False
            print(f"  {name}: FAILED -- {fact_result.error}")
            continue
        rows = fact_result.data["rows"]
        print(f"  {name}: {rows[0] if rows else '(no rows)'}")

    return 0 if overall_ok else 1


def _run_forecast(args: argparse.Namespace) -> int:
    from area.forecast.holdout import run as holdout_run

    raw_dir = Path(args.raw_dir)
    out_path = Path(args.out_path) if args.out_path else FORECAST_OUT_PATH
    if not raw_dir.exists():
        print(
            f"area forecast: {raw_dir} does not exist -- run `area pull` first "
            "(or pass --raw-dir).",
            file=sys.stderr,
        )
        return 1

    result = holdout_run(raw_dir, out_path, top_n_specialties=args.top_n_specialties)
    computed = sorted(result["series"])
    skipped = result["skipped"]
    print(f"wrote {out_path} -- {len(computed)} series computed, {len(skipped)} skipped")
    for key in computed:
        print(f"  {key}: primary_model={result['series'][key]['primary_model']}")
    for key, reason in skipped.items():
        print(f"  {key}: SKIPPED -- {reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command in NOT_YET_BUILT:
        task = NOT_YET_BUILT[args.command]
        print(
            f"`area {args.command}` is not built yet -- see SPEC-area.md's task "
            f"split (section 10), task {task}.",
            file=sys.stderr,
        )
        return 1

    if args.command == "pull":
        return _run_pull(args)
    if args.command == "load":
        return _run_load(args)
    if args.command == "tools-test":
        return _run_tools_test(args)
    if args.command == "forecast":
        return _run_forecast(args)

    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover -- parser.error() already exits


if __name__ == "__main__":
    sys.exit(main())

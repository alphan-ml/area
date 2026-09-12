"""AREA CLI (SPEC-area.md section 2): `area pull | load | tools-test | run
"<question>" | evals | forecast | causal`.

W-B1 wires the two subcommands this task's own modules implement --
`pull` (pull_open_payments.py) and `load` (load_neon.py). The rest
(`tools-test`, `run`, `evals`, `forecast`, `causal`) belong to later tasks
(W-B2/B4/B3) whose modules don't exist yet; calling them now prints a
plain "not built yet" message and exits 1, rather than a traceback --
`pyproject.toml` already declares this file as the `area` console script's
entry point, so it has to import cleanly and behave sanely today even
though most subcommands are still to come.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from area.load_neon import ensure_schema, load_raw_dir
from area.pull_open_payments import YEARS, pull_all_years

NOT_YET_BUILT = {
    "tools-test": "W-B2",
    "run": "W-B4",
    "evals": "W-B4",
    "forecast": "W-B2",
    "causal": "W-B3",
}

SQL_DIR = Path(__file__).resolve().parent.parent.parent / "sql"
SCHEMA_SQL_PATHS = [SQL_DIR / "001_schema.sql", SQL_DIR / "002_views.sql"]


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

    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover -- parser.error() already exits


if __name__ == "__main__":
    sys.exit(main())

"""Live Eval canary (giggitai.com's Live Eval tab): calls the deployed
`POST /api/area-ask` endpoint once per `canary/rows.json` row, computes
each row's gold number(s) by running that row's own gold SQL against the
real, currently-loaded database, scores the endpoint's live answer
against that freshly-computed gold value, and prints one JSON ledger
record to stdout.

No fallback numbers: a database error computing gold, or a network/HTTP
error calling the endpoint, is recorded as an error for that row --
never a stored or guessed number standing in for a real one.

Why gold is computed at run time instead of read from a stored metrics
file: `canary/rows.json`'s 8 rows are `area.evals.cases.CASES`'s
question set -- written before any real data was loaded (CONTEXT.md) and
never used to prompt-engineer `web/api/ask.js`'s SQL-writing model call,
so it is this system's held-out eval split (tests/test_canary.py proves
every row id is one of those case ids). `RECORDED` is always the literal
8, not a number read back from a training run: the metric is a
definition -- all 8 rows are specified to be answerable, or correctly
abstainable, every time -- not a measured baseline (see README's Live
Eval canary section).

psycopg is imported lazily, same pattern as area.load_neon/query_tool:
nothing here needs it importable unless a row's gold SQL is actually
run.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from area.tools.query_tool import validate_and_normalize

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ROWS_PATH = REPO_ROOT / "canary" / "rows.json"

SYSTEM = "area"
KIND = "canary"
METRIC = "correct"
RECORDED = 8.0  # a definition, not a measured value -- see module docstring
COUNT_TOLERANCE = 0.0  # observed must equal RECORDED exactly for match=true
NUMBER_TOLERANCE = 0.005  # 0.5%, per-number tolerance within one row's answer

# Same small, explicit, case-insensitive abstention phrase list as
# area.evals.scoring.ABSTENTION_PHRASES, kept separate on purpose: this
# module scores the live web endpoint's answer text, not an
# area.agent.loop trace, and the two should be free to diverge if either
# surface's honest-abstention wording changes independently.
ABSTENTION_PHRASES: tuple[str, ...] = (
    "no matching data",
    "no data available",
    "not available yet",
)

# A digit glued to a letter or hyphen (the 1 in GLP-1, the 1 in Q1) is part of a name,
# not a stated number; years stand alone and are dropped below.
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9-])-?\d[\d,]*(?:\.\d+)?")


def _extract_numbers(text: str | None) -> list[float]:
    out: list[float] = []
    for raw in _NUMBER_RE.findall(text or ""):
        bare = raw.replace(",", "")
        if bare.lstrip("-").isdigit() and len(bare.lstrip("-")) == 4 and 1900 <= int(bare) <= 2099:
            continue  # a bare year, not a claim
        try:
            out.append(float(bare))
        except ValueError:
            continue
    return out


def is_abstention(answer_text: str | None) -> bool:
    lowered = (answer_text or "").lower()
    return any(phrase in lowered for phrase in ABSTENTION_PHRASES)


def _lazy_psycopg():
    import psycopg  # noqa: PLC0415 -- deliberately lazy, see module docstring

    return psycopg


def fetch_gold_numbers(database_url: str, gold_sql: str) -> list[float]:
    """Runs one row's gold SQL for real and flattens every non-null numeric
    column of every returned row into a list of gold numbers. An empty
    list means "no gold number exists" (most commonly: the years this row
    asks about are not loaded yet) -- callers must then require an
    abstention, not a number match, to call the row correct.

    Reuses area.tools.query_tool's own guardrail (validate_and_normalize)
    as defense-in-depth before ever opening a real connection: canary
    rows are committed and reviewed, but this still refuses anything that
    is not a single allow-listed SELECT.
    """
    sql = validate_and_normalize(gold_sql)
    psycopg = _lazy_psycopg()
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d.name for d in cur.description] if cur.description else []
            rows = [dict(zip(columns, r)) for r in cur.fetchall()]
    numbers: list[float] = []
    for row in rows:
        for value in row.values():
            if value is None or isinstance(value, bool):
                continue
            try:
                numbers.append(float(value))
            except (TypeError, ValueError):
                continue
    return numbers


def call_endpoint(
    endpoint: str, question: str, timeout: float = 30.0
) -> tuple[dict[str, Any], float, str | None]:
    """POSTs {"question": question} to the live endpoint. Returns
    (response_json, latency_ms, error) -- a network/HTTP/JSON failure
    comes back as ({}, latency_ms, "<reason>"), never a fabricated
    response."""
    body = json.dumps({"question": question}).encode()
    req = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        latency_ms = (time.perf_counter() - start) * 1000
        return json.loads(raw), latency_ms, None
    except urllib.error.HTTPError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return {}, latency_ms, f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 -- any network/JSON failure is a recorded row error
        latency_ms = (time.perf_counter() - start) * 1000
        return {}, latency_ms, str(exc)


def score_row(
    gold_numbers: list[float], response: dict[str, Any]
) -> tuple[bool, bool | None, bool]:
    """Returns (correct, cited, abstained) for one row.

    With real gold numbers: correct means every number the answer states
    is within NUMBER_TOLERANCE of some gold number, AND the citation
    check passed (`accepted` true and `unverified` empty, per
    web/api/ask.js's own response shape).

    With no gold numbers (the years this row needs are not loaded yet):
    correct means the answer reads as an honest abstention -- per the
    issue spec, an abstention always counts as correct for that row,
    regardless of the citation fields.
    """
    answer = response.get("answer") or ""
    abstained = is_abstention(answer)
    if not gold_numbers:
        return abstained, None, abstained

    cited = bool(response.get("accepted")) and not response.get("unverified")
    answer_numbers = _extract_numbers(answer)
    if not answer_numbers:
        return False, cited, abstained

    def _matches(n: float) -> bool:
        return any(abs(n - g) <= abs(g) * NUMBER_TOLERANCE + 1e-9 for g in gold_numbers)

    all_match = all(_matches(n) for n in answer_numbers)
    return bool(all_match and cited), cited, abstained


def retrieval_ok(response_sql: str | None, expected_columns: list[str]) -> bool | None:
    """A plain case-insensitive substring check of the SQL the live agent
    actually executed (`response["sql"]`, per web/api/ask.js) for every
    column this row's question needs -- same disclosed scope limit as
    area.evals.scoring.score_retrieval_ok: catches "didn't touch the
    right column at all", not a guarantee the query's logic is correct.
    `None` when there is no executed SQL to check (e.g. the call errored
    before a query ran)."""
    if not response_sql:
        return None
    sql_lower = response_sql.lower()
    return all(col.lower() in sql_lower for col in expected_columns)


def get_release_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _status(correct: bool, error: str | None) -> str:
    if error:
        return "ERR"
    return "OK" if correct else "MISS"


def run_canary(
    rows_path: Path = ROWS_PATH,
    database_url: str | None = None,
    endpoint: str | None = None,
    fetch_gold: Callable[[str, str], list[float]] = fetch_gold_numbers,
    call_endpoint_fn: Callable[
        [str, str], tuple[dict[str, Any], float, str | None]
    ] = call_endpoint,
) -> dict[str, Any]:
    """Runs the full canary once and returns the ledger record (schema:
    giggitai.com's Live Eval tab). `fetch_gold`/`call_endpoint_fn` are
    injectable so tests can exercise this without a real database or
    network call, same dependency-injection pattern as
    area.tools.query_tool's `generate_sql`."""
    config = json.loads(rows_path.read_text())
    rows = config["rows"]
    endpoint = endpoint or config.get("endpoint")
    database_url = database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set -- cannot compute gold values")

    started = time.perf_counter()
    latencies: list[float] = []
    errors = 0
    correct_count = 0
    row_statuses: list[str] = []
    extra_rows: list[dict[str, Any]] = []

    for row in rows:
        row_error: str | None = None
        gold_numbers: list[float] = []
        try:
            gold_numbers = fetch_gold(database_url, row["gold_sql"])
        except Exception as exc:
            row_error = f"gold query failed: {exc}"

        response, latency_ms, call_error = call_endpoint_fn(endpoint, row["question"])
        latencies.append(latency_ms)

        error = row_error or call_error
        if error is None and not response.get("ok", False):
            error = response.get("error") or "endpoint returned ok=false"

        if error:
            errors += 1
            correct, cited, abstained = False, None, False
        else:
            correct, cited, abstained = score_row(gold_numbers, response)

        if correct:
            correct_count += 1

        row_statuses.append(f"{row['id']} {_status(correct, error)}")
        extra_rows.append(
            {
                "id": row["id"],
                "cited": cited,
                "retrieval_ok": retrieval_ok(response.get("sql"), row.get("expected_columns", [])),
                "abstained": abstained,
                "latency_ms": round(latency_ms),
                "error": error,
            }
        )

    duration_s = time.perf_counter() - started
    latencies_sorted = sorted(latencies)
    p50 = latencies_sorted[len(latencies_sorted) // 2] if latencies_sorted else 0.0
    p95_idx = min(len(latencies_sorted) - 1, int(round(len(latencies_sorted) * 0.95 - 0.5)))
    p95 = latencies_sorted[max(p95_idx, 0)] if latencies_sorted else 0.0

    observed = float(correct_count)
    match = abs(observed - RECORDED) <= COUNT_TOLERANCE

    lines = [f"$ area canary --n {len(rows)} --endpoint {endpoint}"]
    lines.append(f"release {get_release_sha()} · {len(rows)} rows · {errors} errors")
    for i in range(0, len(row_statuses), 2):
        lines.append(" · ".join(row_statuses[i : i + 2]))
    lines.append(
        f"{METRIC} {correct_count}/{len(rows)} · recorded {int(RECORDED)} · "
        f"tolerance {COUNT_TOLERANCE:g} · {'MATCH' if match else 'MISMATCH'}"
    )
    lines.append(f"p50 {p50:.0f} ms · p95 {p95:.0f} ms · {duration_s:.1f}s total")

    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "system": SYSTEM,
        "kind": KIND,
        "release": get_release_sha(),
        "endpoint": endpoint,
        "n": len(rows),
        "metric": METRIC,
        "recorded": RECORDED,
        "observed": observed,
        "tolerance": COUNT_TOLERANCE,
        "match": match,
        "p50_ms": int(round(p50)),
        "p95_ms": int(round(p95)),
        "errors": errors,
        "duration_s": round(duration_s, 1),
        "extra": {"rows": extra_rows},
        "lines": lines[:8],
    }


def main(argv: list[str] | None = None) -> int:
    record = run_canary()
    print(json.dumps(record))
    return 0 if record["match"] else 1


if __name__ == "__main__":
    sys.exit(main())

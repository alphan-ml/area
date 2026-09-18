"""Tests for area.canary (Live Eval canary). Zero database, zero
network: fetch_gold/call_endpoint_fn are injected fakes, same
dependency-injection pattern as tests/test_query_tool_guardrails.py uses
for generate_sql. The one thing this suite must prove that no other test
in the repo does: canary/rows.json's 8 ids are exactly
area.evals.cases.CASES' ids -- the held-out question set the issue
requires the canary set be drawn from (see canary.py's module docstring
for why that set counts as "held out").
"""

from __future__ import annotations

import json
from pathlib import Path

from area.canary import (
    ROWS_PATH,
    is_abstention,
    retrieval_ok,
    run_canary,
    score_row,
)
from area.evals.cases import CASES
from area.tools.query_tool import validate_and_normalize

ROWS = json.loads(ROWS_PATH.read_text())["rows"]


def test_rows_file_has_eight_rows():
    assert len(ROWS) == 8


def test_every_row_id_is_in_the_holdout_case_set():
    case_ids = {c.id for c in CASES}
    case_questions = {c.id: c.question for c in CASES}
    seen_ids = set()
    for row in ROWS:
        assert row["id"] in case_ids, f"{row['id']} is not one of area.evals.cases.CASES"
        assert row["question"] == case_questions[row["id"]], (
            f"{row['id']}: canary question text must match the held-out case verbatim"
        )
        seen_ids.add(row["id"])
    assert seen_ids == case_ids, "canary/rows.json must cover every held-out case, no more, no less"


def test_gold_sql_is_a_single_allow_listed_select():
    for row in ROWS:
        validate_and_normalize(row["gold_sql"])  # raises QueryRejected on failure


def test_is_abstention():
    assert is_abstention("No matching data is available yet.")
    assert not is_abstention("The total was $1,234.56 [q_web].")
    assert not is_abstention(None)


def test_score_row_correct_number_within_tolerance_and_cited():
    response = {"answer": "The total was $1,000.02 [q_web].", "accepted": True, "unverified": []}
    correct, cited, abstained = score_row([1000.0], response)
    assert correct is True
    assert cited is True
    assert abstained is False


def test_score_row_wrong_number_is_not_correct():
    response = {"answer": "The total was $500.00 [q_web].", "accepted": True, "unverified": []}
    correct, cited, abstained = score_row([1000.0], response)
    assert correct is False
    assert cited is True


def test_score_row_uncited_number_is_not_correct():
    response = {"answer": "The total was $1,000.00.", "accepted": False, "unverified": ["1000.00"]}
    correct, cited, abstained = score_row([1000.0], response)
    assert correct is False
    assert cited is False


def test_score_row_abstention_counts_as_correct_when_no_gold():
    response = {"answer": "No matching data is available yet.", "accepted": True, "unverified": []}
    correct, cited, abstained = score_row([], response)
    assert correct is True
    assert abstained is True
    assert cited is None


def test_score_row_wrong_answer_when_no_gold_and_not_abstained():
    response = {"answer": "The total was $1,000.00 [q_web].", "accepted": True, "unverified": []}
    correct, cited, abstained = score_row([], response)
    assert correct is False
    assert abstained is False


def test_retrieval_ok_checks_expected_columns():
    sql = "SELECT SUM(amount_usd) FROM payments LIMIT 5000"
    assert retrieval_ok(sql, ["amount_usd"]) is True
    assert retrieval_ok(sql, ["physician_state"]) is False
    assert retrieval_ok(None, ["amount_usd"]) is None


def _fake_gold(gold_values: dict[str, list[float]]):
    def fetch_gold(database_url: str, gold_sql: str) -> list[float]:
        for row in ROWS:
            if row["gold_sql"] == gold_sql:
                return gold_values.get(row["id"], [])
        raise AssertionError(f"unexpected gold_sql: {gold_sql}")

    return fetch_gold


def _fake_endpoint(answers: dict[str, str]):
    def call_endpoint(endpoint: str, question: str):
        for row in ROWS:
            if row["question"] == question:
                answer = answers[row["id"]]
                response = {
                    "ok": True,
                    "sql": "SELECT amount_usd FROM payments LIMIT 5000",
                    "answer": answer,
                    "accepted": True,
                    "unverified": [],
                }
                return response, 42.0, None
        raise AssertionError(f"unexpected question: {question}")

    return call_endpoint


def test_run_canary_all_correct_matches_recorded(tmp_path: Path):
    gold_values = {row["id"]: [1000.0] for row in ROWS}
    answers = {row["id"]: "$1,000.00 [q_web]" for row in ROWS}
    record = run_canary(
        rows_path=ROWS_PATH,
        database_url="postgres://fake",
        fetch_gold=_fake_gold(gold_values),
        call_endpoint_fn=_fake_endpoint(answers),
    )
    assert record["system"] == "area"
    assert record["kind"] == "canary"
    assert record["n"] == 8
    assert record["metric"] == "correct"
    assert record["recorded"] == 8.0
    assert record["observed"] == 8.0
    assert record["tolerance"] == 0.0
    assert record["match"] is True
    assert record["errors"] == 0
    assert 1 <= len(record["lines"]) <= 8
    assert len(record["extra"]["rows"]) == 8


def test_run_canary_wrong_answers_do_not_match_recorded():
    gold_values = {row["id"]: [1000.0] for row in ROWS}
    answers = {row["id"]: "$1.00 [q_web]" for row in ROWS}
    record = run_canary(
        rows_path=ROWS_PATH,
        database_url="postgres://fake",
        fetch_gold=_fake_gold(gold_values),
        call_endpoint_fn=_fake_endpoint(answers),
    )
    assert record["observed"] == 0.0
    assert record["match"] is False


def test_run_canary_endpoint_error_is_recorded_not_faked():
    gold_values = {row["id"]: [1000.0] for row in ROWS}

    def erroring_endpoint(endpoint: str, question: str):
        return {}, 10.0, "connection refused"

    record = run_canary(
        rows_path=ROWS_PATH,
        database_url="postgres://fake",
        fetch_gold=_fake_gold(gold_values),
        call_endpoint_fn=erroring_endpoint,
    )
    assert record["errors"] == 8
    assert record["observed"] == 0.0
    assert record["match"] is False
    assert all(r["error"] == "connection refused" for r in record["extra"]["rows"])

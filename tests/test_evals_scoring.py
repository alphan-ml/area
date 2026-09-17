'''Tests for area.evals.scoring and the rewritten area.evals.runner
scoring pass (task A1 part 1). Every trace here is hand-built or produced
by a ScriptedProvider over a fake tool registry (same pattern as
tests/test_agent_loop.py) -- zero database, zero network, matching this
task's own "new tests must use mocked traces" constraint.
'''

from __future__ import annotations

import json
from pathlib import Path

from area.cli import main
from area.evals.cases import EvalCase
from area.evals.runner import run_evals, score_from_traces
from area.evals.scoring import (
    find_executed_sql,
    is_abstention,
    score_case,
    score_complete,
    score_correct,
    score_retrieval_ok,
)
from area.providers.base import CallResult
from area.tools import Evidence, Tool, ToolResult

GOLD = {
    'value': 100.0,
    'unit': 'usd',
    'tolerance': 0.01,  # 1%
    'expected_table': 'payments',
    'expected_columns': ['amount_usd'],
}


def _successful_query_step(sql: str) -> dict:
    return {
        'kind': 'tool_call',
        'detail': {
            'tool': 'query_tool',
            'args': {'question': 'q'},
            'ok': True,
            'error': None,
            'data': {
                'sql': sql,
                'row_count': 1,
                'execution_ms': 1.0,
                'rows': [{'total_usd': 100.5}],
            },
        },
    }


def _failed_query_step() -> dict:
    return {
        'kind': 'tool_call',
        'detail': {
            'tool': 'query_tool',
            'args': {'question': 'q'},
            'ok': False,
            'error': 'query execution failed: relation "payments" does not exist',
            'data': None,
        },
    }


def _trace(answer_text: str | None, steps: list | None = None, **extra) -> dict:
    base = {
        'question': 'q?',
        'model_id': 'm',
        'adapter': 'fake',
        'steps': steps or [],
        'calls': [],
        'evidence': [],
        'answer_text': answer_text,
        'accepted': True,
        'unverified': [],
        'error': None,
        'input_tokens': 10,
        'output_tokens': 5,
    }
    base.update(extra)
    return base


# --- is_abstention ---------------------------------------------------------


def test_is_abstention_matches_the_real_no_data_phrasing():
    assert is_abstention('No matching data is available yet.') is True
    assert is_abstention('No data available for this year.') is True
    assert is_abstention('That series is not available yet.') is True


def test_is_abstention_false_for_a_real_answer():
    assert is_abstention('The total was $100.50 [q_abc].') is False
    assert is_abstention(None) is False
    assert is_abstention('') is False


# --- score_correct / score_complete -----------------------------------------


def test_score_correct_is_none_without_a_verified_gold_value():
    assert score_correct('The total was $100.50 [q_abc].', None) is None
    assert score_correct('The total was $100.50 [q_abc].', {'value': None}) is None


def test_score_correct_true_within_tolerance_false_outside_it():
    assert score_correct('The total was $100.50 [q_abc].', GOLD) is True
    assert score_correct('The total was $250.00 [q_abc].', GOLD) is False


def test_score_correct_false_when_answer_has_no_number_at_all():
    assert score_correct('No matching data is available yet.', GOLD) is False


def test_score_complete_none_with_no_gold_and_no_parts():
    assert score_complete('anything', None, []) is None
    assert score_complete('anything', None, None) is None


def test_score_complete_checks_named_parts_even_without_a_gold_value():
    # `parts` comes from the question itself (named products/categories),
    # not from the true answer -- so it's checkable even while gold=None.
    parts = ['semaglutide', 'tirzepatide']
    assert score_complete('Semaglutide beat Tirzepatide this year.', None, parts) is True
    assert score_complete('Semaglutide did well.', None, parts) is False


def test_score_complete_requires_a_number_when_gold_is_numeric():
    assert score_complete('The total was $100.50 [q_abc].', GOLD, []) is True
    assert score_complete('No matching data is available yet.', GOLD, []) is False


# --- find_executed_sql / score_retrieval_ok ---------------------------------


def test_find_executed_sql_reads_a_successful_query_tool_step():
    trace = _trace('x', steps=[_successful_query_step('SELECT SUM(amount_usd) FROM payments')])
    assert find_executed_sql(trace) == 'SELECT SUM(amount_usd) FROM payments'


def test_find_executed_sql_none_when_every_call_failed():
    # Matches the real shape of all 8 committed evals/traces/*.json today:
    # every query_tool attempt errored out, so no SQL was ever recorded.
    trace = _trace('x', steps=[_failed_query_step(), _failed_query_step()])
    assert find_executed_sql(trace) is None


def test_score_retrieval_ok_none_without_an_expected_table():
    trace = _trace('x', steps=[_successful_query_step('SELECT SUM(amount_usd) FROM payments')])
    assert score_retrieval_ok(trace, None) is None
    assert score_retrieval_ok(trace, {'value': 1.0}) is None  # no expected_table key


def test_score_retrieval_ok_none_when_trace_has_no_sql():
    trace = _trace('x', steps=[_failed_query_step()])
    assert score_retrieval_ok(trace, GOLD) is None


def test_score_retrieval_ok_true_when_table_and_columns_present():
    trace = _trace('x', steps=[_successful_query_step('SELECT SUM(amount_usd) FROM payments')])
    assert score_retrieval_ok(trace, GOLD) is True


def test_score_retrieval_ok_false_when_wrong_table():
    trace = _trace('x', steps=[_successful_query_step('SELECT total_usd FROM q_totals')])
    assert score_retrieval_ok(trace, GOLD) is False


def test_score_retrieval_ok_false_when_an_expected_column_is_missing():
    trace = _trace('x', steps=[_successful_query_step('SELECT record_id FROM payments')])
    assert score_retrieval_ok(trace, GOLD) is False


# --- score_case: the new pass rule, task A1's own 4 required scenarios -----


def test_abstention_against_a_gold_question_fails():
    case = EvalCase('c', 'q?', gold=GOLD, expect_abstain=False)
    trace = _trace('No matching data is available yet.', steps=[_failed_query_step()])
    score = score_case(trace, case)
    assert score.abstained is True
    assert score.passed is False


def test_abstention_against_expect_abstain_passes():
    case = EvalCase('c', 'q?', gold=None, expect_abstain=True, status='expected abstention')
    trace = _trace('No matching data is available yet.', steps=[_failed_query_step()])
    score = score_case(trace, case)
    assert score.abstained is True
    assert score.passed is True


def test_gold_null_question_is_not_scored():
    case = EvalCase('c', 'q?', gold=None, expect_abstain=False, status='needs gold value')
    trace = _trace('No matching data is available yet.', steps=[_failed_query_step()])
    score = score_case(trace, case)
    assert score.correct is None
    assert score.passed is None


def test_gold_null_question_never_reports_passed_true():
    # Even an answer that happens to contain a plausible-looking number
    # and a successful-looking query must not be scored True while the
    # gold value is unverified -- "passed: null, never true".
    case = EvalCase('c', 'q?', gold=None, expect_abstain=False, status='needs gold value')
    trace = _trace(
        'The total was $100.50 [q_abc].',
        steps=[_successful_query_step('SELECT SUM(amount_usd) FROM payments')],
    )
    score = score_case(trace, case)
    assert score.passed is None


def test_correct_numeric_answer_passes_when_retrieval_ok_is_true():
    case = EvalCase('c', 'q?', gold=GOLD, expect_abstain=False)
    trace = _trace(
        'The total was $100.50 [q_abc].',
        steps=[_successful_query_step('SELECT SUM(amount_usd) FROM payments')],
    )
    score = score_case(trace, case)
    assert score.correct is True
    assert score.retrieval_ok is True
    assert score.passed is True


def test_correct_numeric_answer_not_scored_when_retrieval_is_unknown():
    # Same correct number, but the trace never recorded a successful
    # query -- retrieval_ok is unknown, so the case is not scored a pass.
    case = EvalCase('c', 'q?', gold=GOLD, expect_abstain=False)
    trace = _trace('The total was $100.50 [q_abc].', steps=[_failed_query_step()])
    score = score_case(trace, case)
    assert score.correct is True
    assert score.retrieval_ok is None
    assert score.passed is None


def test_wrong_numeric_answer_fails_even_with_good_retrieval():
    case = EvalCase('c', 'q?', gold=GOLD, expect_abstain=False)
    trace = _trace(
        'The total was $500.00 [q_abc].',
        steps=[_successful_query_step('SELECT SUM(amount_usd) FROM payments')],
    )
    score = score_case(trace, case)
    assert score.correct is False
    assert score.passed is False


# --- run_evals: end-to-end scoring wiring, mocked provider + tools ----------


class _ScriptedProvider:
    def __init__(self, replies: list[str]):
        self._replies = list(replies)

    def call(self, model_id, prompt, max_tokens, temperature):
        text = self._replies.pop(0) if self._replies else '{"action": "answer"}'
        return CallResult(
            text=text,
            input_tokens=len(prompt.split()),
            output_tokens=len(text.split()),
            latency_ms=1.0,
            adapter='scripted',
            http_status=200,
            retries=0,
            error=None,
        )


def _fake_query_tool_registry(sql: str) -> dict:
    def fn(args, **kwargs):
        return ToolResult(
            ok=True,
            data={'sql': sql, 'row_count': 1, 'execution_ms': 1.0, 'rows': [{'total_usd': 100.5}]},
            evidence=[Evidence('q_abc', 'sql_rows', [{'total_usd': 100.5}], {'sql': sql})],
        )

    tool = Tool(
        name='query_tool',
        description='test stub',
        input_schema={'type': 'object', 'properties': {}},
        fn=fn,
    )
    return {'query_tool': tool}


def test_run_evals_scores_a_correct_retrieved_answer_as_passed(tmp_path):
    case = EvalCase('c1', 'What was the total?', gold=GOLD, expect_abstain=False)
    replies = [
        json.dumps({'action': 'tool_call', 'tool': 'query_tool', 'args': {'question': 'q'}}),
        json.dumps({'action': 'answer'}),
        'The total was $100.50 [q_abc].',
    ]
    provider = _ScriptedProvider(replies)
    tools = _fake_query_tool_registry('SELECT SUM(amount_usd) AS total_usd FROM payments')

    summary = run_evals(
        cases=[case], provider=provider, model_id='m', adapter='scripted',
        tools=tools, trace_dir=tmp_path,
    )

    assert summary.total == 1
    result = summary.results[0]
    assert result.correct is True
    assert result.retrieval_ok is True
    assert result.passed is True
    assert summary.passed == 1
    assert summary.not_scored == 0
    assert (tmp_path / 'c1.json').exists()


def test_run_evals_scores_an_abstention_against_a_gold_case_as_failed(tmp_path):
    case = EvalCase('c1', 'What was the total?', gold=GOLD, expect_abstain=False)
    replies = [
        json.dumps({'action': 'answer'}),
        'No matching data is available yet.',
    ]
    provider = _ScriptedProvider(replies)

    summary = run_evals(
        cases=[case], provider=provider, model_id='m', adapter='scripted',
        tools={}, trace_dir=tmp_path,
    )

    result = summary.results[0]
    assert result.abstained is True
    assert result.passed is False
    assert summary.failed == 1


# --- score_from_traces: regenerating a summary with no agent/DB call -------


def test_score_from_traces_reads_a_written_trace_and_scores_it(tmp_path):
    case = EvalCase('c1', 'q?', gold=GOLD, expect_abstain=False)
    (tmp_path / 'c1.json').write_text(
        json.dumps(
            _trace(
                'The total was $100.50 [q_abc].',
                steps=[_successful_query_step('SELECT SUM(amount_usd) FROM payments')],
            )
        )
    )

    summary = score_from_traces(cases=[case], trace_dir=tmp_path)

    assert summary.total == 1
    result = summary.results[0]
    assert result.passed is True
    assert result.retrieval_ok is True


def test_score_from_traces_reports_a_missing_trace_file_as_not_scored(tmp_path):
    case = EvalCase('missing', 'q?', gold=None, expect_abstain=False, status='needs gold value')

    summary = score_from_traces(cases=[case], trace_dir=tmp_path)

    result = summary.results[0]
    assert result.passed is None
    assert 'no trace file' in result.error


def test_score_from_traces_against_the_real_committed_traces_reports_not_scored():
    # Regression guard for task A1 step 6's expected regeneration result:
    # every one of the 8 real CASES has gold=None right now (see
    # cases.py), so none can be scored a pass yet.
    from area.evals.cases import CASES

    repo_root = Path(__file__).resolve().parent.parent
    summary = score_from_traces(cases=CASES, trace_dir=repo_root / 'evals' / 'traces')

    assert summary.total == 8
    assert summary.passed == 0
    assert summary.not_scored == 8


# --- CLI: `area evals --from-traces` never touches the agent loop ----------


def test_cli_evals_from_traces_scores_without_calling_run_evals(tmp_path, monkeypatch, capsys):
    trace_dir = tmp_path / 'traces'
    trace_dir.mkdir()
    (trace_dir / 'c1.json').write_text(json.dumps(_trace('No matching data is available yet.')))

    case = EvalCase('c1', 'q?', gold=None, expect_abstain=False, status='needs gold value')
    monkeypatch.setattr('area.evals.runner.CASES', [case])

    def _boom(*args, **kwargs):  # pragma: no cover -- must never run
        raise AssertionError('--from-traces must not call run_evals')

    monkeypatch.setattr('area.evals.runner.run_evals', _boom)

    out_path = tmp_path / 'summary.json'
    exit_code = main(
        [
            'evals', '--from-traces',
            '--trace-dir', str(trace_dir),
            '--out-path', str(out_path),
        ]
    )
    out = capsys.readouterr().out

    assert 'NOT SCORED' in out
    assert exit_code == 1  # nothing passed
    written = json.loads(out_path.read_text())
    assert written['not_scored'] == 1
    assert written['passed'] == 0

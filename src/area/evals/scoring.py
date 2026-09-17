'''Per-question eval scoring (task A1 part 1).

Until today, `area.evals.runner` scored a case "passed" whenever the
agent loop hit no error and `citation_checker` accepted the composed
answer text (`trace.error is None and trace.accepted`) -- true even for
a question with a known correct number, if the agent abstained instead
of answering it. That made every one of the 8 committed cases in
evals/summary.json report "passed" with the honest-but-wrong answer "No
matching data is available yet.", because an uncontested abstention is
just as citation-clean as a correct cited number.

This module scores four things independently per case, then combines
them into the new pass rule:

- `correct`   -- does the answer contain a number within `gold`'s
                 tolerance of the true value? `None` (not evaluable)
                 when there is no `gold` value yet.
- `complete`  -- does the answer look like it addressed everything the
                 question asked for (a number, when one was expected,
                 and every named `parts` keyword)? See `score_complete`'s
                 own docstring for exactly what this does and does not
                 check -- it is a simple, documented heuristic, not a
                 real answer-completeness grader.
- `retrieval_ok` -- did the query the agent actually ran touch the
                 expected table and columns? `None` when there's no
                 expected table to check against (no `gold`), or when
                 the trace does not record any successfully-executed SQL
                 to check (see `find_executed_sql`).
- `abstained` -- does the answer read as a "no data available" style
                 abstention (see `ABSTENTION_PHRASES`)? Always a concrete
                 `True`/`False` -- never unknown.

Pass rule (task A1's own spec):

    passed = (correct and retrieval_ok) or (abstained and expect_abstain)

`correct`/`retrieval_ok`/`complete` can each be `None` ("unknown", not
`True` or `False`) when there isn't enough information to compute them
yet -- most commonly because `gold` is `None` (the true value hasn't
been verified against real data, see cases.py). Evaluating the pass rule
with a plain Python `and`/`or` would silently treat `None` as falsy and
report an unscored case as a hard failure; that is not what "passed:
null (not scored), never true" (task A1) asks for. So this module
combines the two terms with three-valued (Kleene) logic instead:
`_and3`/`_or3` propagate `None` ("unknown") the same way SQL's NULL does
-- `None or True is True`, `None and False is False`, and only
`None or False` / `None and True` (genuinely can't tell) come out as
`None`. `abstained` and `expect_abstain` are always concrete booleans,
so the only way the whole expression comes out `None` is when neither
term can prove the case passed: no verified gold value AND no
expect_abstain match. That is exactly "not scored."
'''

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Abstention detection: a small, explicit, case-insensitive substring list,
# kept in this one place so every caller (the runner, tests, a future
# report) agrees on what counts as "the agent said there's no data" rather
# than each re-deriving its own heuristic. Extend this list, don't
# duplicate the check, if a real run surfaces another honest-abstention
# phrasing that isn't already covered.
ABSTENTION_PHRASES: tuple[str, ...] = (
    'no matching data',
    'no data available',
    'not available yet',
)


def is_abstention(answer_text: str | None) -> bool:
    '''True if `answer_text` reads as a "no data available" style
    abstention (see ABSTENTION_PHRASES). Always a concrete bool -- an
    empty/None answer is not an abstention, just missing.'''
    if not answer_text:
        return False
    lowered = answer_text.lower()
    return any(phrase in lowered for phrase in ABSTENTION_PHRASES)


# A permissive numeric-token matcher for the answer text: currency
# ($1,234.56), comma-grouped (1,234), or plain (42, 3.14, 24.7%). Unlike
# citation_checker's own _NUMBER_PATTERN, this one does not need to
# exclude bare years or numbers glued to a marker id -- it is used only to
# find candidate numbers to compare against a known gold value, not to
# enforce a citation contract.
_NUMBER_RE = re.compile(r'\$?-?\d[\d,]*(?:\.\d+)?%?')


def _extract_numbers(text: str) -> list[float]:
    out: list[float] = []
    for raw in _NUMBER_RE.findall(text):
        cleaned = raw.replace('$', '').replace(',', '').replace('%', '')
        try:
            out.append(float(cleaned))
        except ValueError:
            continue
    return out


def score_correct(answer_text: str | None, gold: dict | None) -> bool | None:
    '''True/False only when `gold` has a verified numeric `value` to check
    against; `None` (not evaluable) when `gold` is `None` or has no
    `value` yet. A `None`/empty answer_text against a real gold value is
    `False`, not unknown -- there is a right answer and this isn't it.'''
    if not gold or gold.get('value') is None:
        return None
    try:
        target = float(gold['value'])
    except (TypeError, ValueError):
        return None
    if not answer_text:
        return False
    tolerance = float(gold.get('tolerance') or 0.0)
    allowed = abs(target) * tolerance
    if allowed <= 0:
        allowed = 1e-9  # tolerance 0 (or missing) still allows float rounding noise
    return any(abs(candidate - target) <= allowed for candidate in _extract_numbers(answer_text))


def score_complete(
    answer_text: str | None, gold: dict | None, parts: list[str] | None
) -> bool | None:
    '''A deliberately simple, documented completeness heuristic -- NOT a
    real answer grader. It checks two things, each only when there is
    something to check:

    1. If `gold` has a numeric `value`, the answer must contain at least
       one number (any number -- `score_correct` is what checks it's the
       *right* one; this only checks the answer didn't just talk around
       the question with no figure at all).
    2. Every string in `parts` (case-insensitive substring match) must
       appear in the answer text. `parts` is the question's own known,
       named things to cover (e.g. both product names in a comparison
       question) -- it does not depend on `gold` being known, since it
       comes from the question itself, not from the true answer.

    Returns `None` only when there is truly nothing to check (no gold
    value and no parts) -- e.g. a bare `expect_abstain` question, where a
    short abstention is itself the complete answer and there is no
    number or named part to look for.
    '''
    parts = parts or []
    has_numeric_gold = bool(gold and isinstance(gold.get('value'), (int, float)))
    if not has_numeric_gold and not parts:
        return None
    if not answer_text:
        return False
    ok = True
    if has_numeric_gold:
        ok = ok and bool(_extract_numbers(answer_text))
    lowered = answer_text.lower()
    for part in parts:
        ok = ok and part.lower() in lowered
    return ok


def find_executed_sql(trace: dict) -> str | None:
    '''Looks for the first successfully-executed `query_tool` call in a
    serialized trace (`AgentTrace.to_dict()`'s own shape, or the
    equivalent dict read back from a written trace file) and returns its
    SQL text.

    This depends entirely on `query_tool.run`'s own success-path wire
    format (`src/area/tools/query_tool.py`, read-only -- not modified by
    this task): on success, `ToolResult.data == {"sql": ..., "row_count":
    ..., "execution_ms": ..., "rows": ...}`, and `area.agent.loop.run_agent`
    (also read-only) records that whole `data` dict, unmodified, on the
    matching `tool_call` step (`AgentStep('tool_call', {..., "data":
    _summarize(result.data)})`). A failed call's `data` is `None`, so a
    trace where every query attempt errored out (as in every one of the 8
    currently-committed traces under evals/traces/ -- the `payments`
    table did not exist when they ran) carries no SQL text anywhere, and
    this correctly returns `None` rather than guessing at the *attempted*
    (never-run) SQL from a planner step.
    '''
    for step in trace.get('steps', []):
        if step.get('kind') != 'tool_call':
            continue
        detail = step.get('detail') or {}
        if detail.get('tool') != 'query_tool' or not detail.get('ok'):
            continue
        data = detail.get('data') or {}
        sql = data.get('sql')
        if sql:
            return sql
    return None


def score_retrieval_ok(trace: dict, gold: dict | None) -> bool | None:
    '''True/False only when both (a) `gold` names an `expected_table`
    (and optionally `expected_columns`) to check against, and (b) the
    trace records a successfully-executed query's real SQL text
    (`find_executed_sql`). Otherwise `None` -- there is nothing to check
    the retrieval against, or nothing to check it with.

    The check itself is a plain case-insensitive substring search over
    the executed SQL text for the expected table name and every expected
    column name -- not a real SQL parse. This mirrors the rest of this
    codebase's own disclosed scope limit for text-level SQL checks (see
    `query_tool.validate_and_normalize`'s CONTEXT.md decision D15): good
    enough to catch "the query didn't touch the right table/columns at
    all," not a guarantee the query's *logic* (joins, filters, grouping)
    is correct -- `correct` (comparing the stated answer to the gold
    value) is what actually catches a wrong computation over the right
    table.
    '''
    if not gold or not gold.get('expected_table'):
        return None
    sql = find_executed_sql(trace)
    if sql is None:
        return None
    sql_lower = sql.lower()
    if str(gold['expected_table']).lower() not in sql_lower:
        return False
    for column in gold.get('expected_columns') or []:
        if str(column).lower() not in sql_lower:
            return False
    return True


def _and3(a: bool | None, b: bool | None) -> bool | None:
    '''Three-valued AND (Kleene logic): a known False beats an unknown
    (there's no way the AND can still be true); otherwise an unknown
    beats a known True (we can't confirm it); only two known truths give
    a known True.'''
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return bool(a) and bool(b)


def _or3(a: bool | None, b: bool | None) -> bool | None:
    '''Three-valued OR (Kleene logic): a known True beats an unknown
    (there's already a reason it's true); otherwise an unknown beats a
    known False (we still can't rule it out); only two known falsehoods
    give a known False.'''
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return bool(a) or bool(b)


@dataclass
class CaseScore:
    correct: bool | None
    complete: bool | None
    retrieval_ok: bool | None
    abstained: bool
    passed: bool | None


def score_case(trace: dict, case: Any) -> CaseScore:
    '''Scores one case's serialized trace against its question-set
    metadata (an `area.evals.cases.EvalCase`, or anything with the same
    `gold`/`expect_abstain`/`parts` attributes) and combines the four
    scores into `passed`, per this module's own docstring.'''
    answer_text = trace.get('answer_text')
    gold = case.gold
    abstained = is_abstention(answer_text)

    correct = score_correct(answer_text, gold)
    complete = score_complete(answer_text, gold, case.parts)
    retrieval_ok = score_retrieval_ok(trace, gold)

    term_correct_and_retrieval = _and3(correct, retrieval_ok)
    term_abstain_expected = bool(abstained) and bool(case.expect_abstain)
    passed = _or3(term_correct_and_retrieval, term_abstain_expected)

    return CaseScore(
        correct=correct,
        complete=complete,
        retrieval_ok=retrieval_ok,
        abstained=abstained,
        passed=passed,
    )

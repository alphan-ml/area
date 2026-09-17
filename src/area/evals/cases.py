'''Fixed eval question set (SPEC-area.md section 5.4, task W-B4; scoring
schema extended in task A1 part 1).

Every question mirrors data/facts.md's 5 headline queries plus 3 more,
deliberately real research questions about the loaded GLP-1 manufacturer
payments data -- not synthetic/toy prompts. Whatever years are actually
loaded when `area evals` runs (see CONTEXT.md's pull-progress notes) is
what these get answered against; a case that honestly reports "no data
available yet" for an unloaded year is a real result of the loop's own
no-fabrication rule, not a broken eval.

Each case now also carries the scoring metadata `area.evals.scoring`
needs (task A1 part 1):

- `gold`: either `None` (the true value isn't known yet -- never invent
  one) or a dict `{value, unit, tolerance, expected_table,
  expected_columns}`:
    - `value`/`unit`: the correct numeric answer and its unit.
    - `tolerance`: a fraction (e.g. 0.005 = 0.5%) `score_correct` allows
      the answer to be off by, relative to `value`.
    - `expected_table`/`expected_columns`: the table and column names a
      correct query for this question must reference -- what
      `score_retrieval_ok` looks for in the trace's executed SQL.
  As of this task, no gold value has been verified for any of the 8
  cases below (see CONTEXT.md: the real CMS pull hasn't loaded rows
  yet) -- every one is `gold=None`, `status="needs gold value"`, per the
  BUILD INSTRUCTION's "no invented numbers, ever" rule. Filling these in
  is a follow-up task once the real pull loads rows and the true answers
  can be read from the database and verified by hand.
- `expect_abstain`: True for a question whose correct answer IS an
  abstention (mutually exclusive with `gold` -- SPEC-area.md doesn't
  currently have one of these; all 8 below are ordinary "there is a real
  number" questions, so all are False). Kept per-case (not global) since
  a future case set may mix both kinds.
- `status`: a short human-readable note on why `gold` is what it is
  (e.g. "needs gold value") -- carried into evals/summary.json so a
  reader doesn't have to guess why a case reports `passed: null`.
- `parts`: names/keywords `score_complete` checks for verbatim
  (case-insensitively) in the answer text, for questions that name
  specific things the answer must mention to be a complete answer (e.g.
  both product names in a comparison question). Left empty for
  single-number questions where the only completeness signal is "the
  answer contains a number" -- see `area.evals.scoring.score_complete`'s
  own docstring for exactly what this does and does not check.
'''

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvalCase:
    id: str
    question: str
    gold: dict | None = None
    expect_abstain: bool = False
    status: str | None = None
    parts: list[str] = field(default_factory=list)


CASES = [
    EvalCase(
        'total-dollars',
        'What was the total dollar amount of GLP-1 manufacturer payments '
        'in the data, across all program years loaded so far?',
        gold=None,
        status='needs gold value',
    ),
    EvalCase(
        'top-specialty',
        'Which physician specialty received the most GLP-1 manufacturer '
        'payment dollars?',
        gold=None,
        status='needs gold value',
    ),
    EvalCase(
        'top-state',
        'Which US state had physicians receiving the most GLP-1 '
        'manufacturer payment dollars?',
        gold=None,
        status='needs gold value',
    ),
    EvalCase(
        'quarterly-2021-q1',
        'What were total GLP-1 manufacturer payment dollars in the first '
        'quarter of 2021?',
        gold=None,
        status='needs gold value',
    ),
    EvalCase(
        'food-beverage-share',
        'What share of GLP-1 manufacturer payment dollars were '
        'categorized as Food and Beverage?',
        gold=None,
        status='needs gold value',
        parts=['food and beverage'],
    ),
    EvalCase(
        'semaglutide-vs-tirzepatide',
        'How do total payment dollars for semaglutide products compare '
        'to tirzepatide products?',
        gold=None,
        status='needs gold value',
        parts=['semaglutide', 'tirzepatide'],
    ),
    EvalCase(
        'novo-lilly-share',
        'What percentage of matched GLP-1 payment dollars went to '
        'manufacturers other than Novo Nordisk and Eli Lilly?',
        gold=None,
        status='needs gold value',
        parts=['novo nordisk', 'eli lilly'],
    ),
    EvalCase(
        'row-count-2021',
        'How many individual GLP-1 manufacturer payment records are in '
        'the data for program year 2021?',
        gold=None,
        status='needs gold value',
    ),
]

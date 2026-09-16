'''Fixed eval question set (SPEC-area.md section 5.4, task W-B4).

Every question mirrors data/facts.md.s 5 headline queries plus 3 more,
deliberately real research questions about the loaded GLP-1 manufacturer
payments data -- not synthetic/toy prompts. Whatever years are actually
loaded when `area evals` runs (see CONTEXT.md.s pull-progress notes) is
what these get answered against; a case that honestly reports .no data
available yet. for an unloaded year is a real result of the loop.s own
no-fabrication rule, not a broken eval.
'''

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EvalCase:
    id: str
    question: str


CASES = [
    EvalCase(
        'total-dollars',
        'What was the total dollar amount of GLP-1 manufacturer payments '
        'in the data, across all program years loaded so far?',
    ),
    EvalCase(
        'top-specialty',
        'Which physician specialty received the most GLP-1 manufacturer '
        'payment dollars?',
    ),
    EvalCase(
        'top-state',
        'Which US state had physicians receiving the most GLP-1 '
        'manufacturer payment dollars?',
    ),
    EvalCase(
        'quarterly-2021-q1',
        'What were total GLP-1 manufacturer payment dollars in the first '
        'quarter of 2021?',
    ),
    EvalCase(
        'food-beverage-share',
        'What share of GLP-1 manufacturer payment dollars were '
        'categorized as Food and Beverage?',
    ),
    EvalCase(
        'semaglutide-vs-tirzepatide',
        'How do total payment dollars for semaglutide products compare '
        'to tirzepatide products?',
    ),
    EvalCase(
        'novo-lilly-share',
        'What percentage of matched GLP-1 payment dollars went to '
        'manufacturers other than Novo Nordisk and Eli Lilly?',
    ),
    EvalCase(
        'row-count-2021',
        'How many individual GLP-1 manufacturer payment records are in '
        'the data for program year 2021?',
    ),
]

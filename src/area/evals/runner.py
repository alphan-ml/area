'''Evals runner (task W-B4; scoring rewritten in task A1 part 1).

`run_evals()` runs CASES through area.agent.run_agent for real -- real
provider (Bedrock by default), real tool registry, real database when
configured -- writes one trace file per case via area.evals.traces.
write_trace, and reports pass/fail plus real token/cost/latency totals.

Scoring (task A1 part 1): a case no longer passes just because the loop
hit no error and citation_checker accepted the composed answer text
(that old rule -- `trace.error is None and trace.accepted` -- let an
honest-but-wrong abstention pass every one of the 8 committed cases in
evals/summary.json, even though 7 of the 8 have never been checked
against a real gold value). Each case is now scored on four independent
axes by area.evals.scoring.score_case (correct/complete/retrieval_ok/
abstained) and combined into `passed` there -- see that module's own
docstring for the exact rule and why an unscoreable case reports
`passed: None` rather than True or False.

`score_from_traces()` is the same scoring pass with no agent loop / model
provider / database call at all: it reads already-written trace files
back off disk (area.evals.traces.read_trace) and re-scores them under the
current question set -- e.g. `area evals --from-traces` (task A1 step 6),
used to regenerate evals/summary.json under the new schema from the
traces that are already committed, without needing a live database or
model access to do it.

Cost is reported only when a price table is supplied (input_price_per_1m/
output_price_per_1m) -- see model-bench's data/prices.json (this repo's
sibling) for the source of those numbers; this module never hardcodes or
guesses a price.
'''

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path

from area.agent.loop import run_agent
from area.evals.cases import CASES
from area.evals.scoring import score_case
from area.evals.traces import read_trace, write_trace


@dataclass
class EvalCaseResult:
    case_id: str
    question: str
    passed: bool | None
    accepted: bool
    error: str | None
    answer_text: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: float
    calls: int
    trace_path: str | None
    # Task A1 part 1: the four independent scores behind `passed`, plus
    # the question-set metadata that drove them -- all optional/defaulted
    # so existing callers that construct an EvalCaseResult without them
    # (e.g. tests/test_cli.py's monkeypatched run_evals) keep working.
    correct: bool | None = None
    complete: bool | None = None
    retrieval_ok: bool | None = None
    abstained: bool = False
    expect_abstain: bool = False
    gold: dict | None = None
    status: str | None = None


@dataclass
class EvalsSummary:
    results: list = field(default_factory=list)
    started_at: str = ''
    finished_at: str = ''
    model_id: str = ''
    adapter: str = ''
    input_price_per_1m: float | None = None
    output_price_per_1m: float | None = None

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        '''Count of results definitely scored False -- distinct from
        `not_scored` (unknown, `passed is None`).'''
        return sum(1 for r in self.results if r.passed is False)

    @property
    def not_scored(self) -> int:
        '''Count of results with `passed is None` -- not a pass, not a
        fail, genuinely not scoreable yet (see area.evals.scoring).'''
        return sum(1 for r in self.results if r.passed is None)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.results)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.results)

    @property
    def total_latency_ms(self) -> float:
        return sum(r.latency_ms for r in self.results)

    def total_cost_usd(self) -> float | None:
        if self.input_price_per_1m is None or self.output_price_per_1m is None:
            return None
        return (
            self.total_input_tokens * self.input_price_per_1m
            + self.total_output_tokens * self.output_price_per_1m
        ) / 1_000_000

    def to_dict(self) -> dict:
        return {
            'started_at': self.started_at,
            'finished_at': self.finished_at,
            'model_id': self.model_id,
            'adapter': self.adapter,
            'passed': self.passed,
            'failed': self.failed,
            'not_scored': self.not_scored,
            'total': self.total,
            'total_input_tokens': self.total_input_tokens,
            'total_output_tokens': self.total_output_tokens,
            'total_latency_ms': self.total_latency_ms,
            'total_cost_usd': self.total_cost_usd(),
            'results': [r.__dict__ for r in self.results],
        }


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _display_path(path: Path) -> str:
    '''Best-effort path for storing in summary.json: relative to the
    current working directory when `area` was run from the repo root
    (README's own documented usage), so a committed summary.json doesn't
    bake in one machine's absolute home/scratch directory. Falls back to
    the absolute path if `path` isn't under the cwd.'''
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def run_evals(
    cases: list | None = None,
    *,
    provider=None,
    model_id: str | None = None,
    adapter: str | None = None,
    tools: dict | None = None,
    database_url: str | None = None,
    trace_dir: Path | None = None,
    input_price_per_1m: float | None = None,
    output_price_per_1m: float | None = None,
) -> EvalsSummary:
    '''`tools` (default None, same as area.agent.loop.run_agent's own
    default -- the real registry) is threaded straight through so tests
    can run this end-to-end with a fake tool registry and a scripted
    provider, with zero network/database, same pattern as
    tests/test_agent_loop.py.'''
    cases = cases if cases is not None else CASES
    summary = EvalsSummary(
        input_price_per_1m=input_price_per_1m, output_price_per_1m=output_price_per_1m
    )
    summary.started_at = _now_iso()
    trace_dir = Path(trace_dir) if trace_dir is not None else None

    for case in cases:
        trace = run_agent(
            case.question,
            provider=provider,
            model_id=model_id,
            adapter=adapter,
            tools=tools,
            database_url=database_url,
        )
        summary.model_id = trace.model_id
        summary.adapter = trace.adapter
        score = score_case(trace.to_dict(), case)
        trace_path = None
        if trace_dir is not None:
            written = write_trace(trace, trace_dir / (case.id + '.json'))
            trace_path = _display_path(written)
        summary.results.append(
            EvalCaseResult(
                case_id=case.id,
                question=case.question,
                passed=score.passed,
                accepted=trace.accepted,
                error=trace.error,
                answer_text=trace.answer_text,
                input_tokens=trace.input_tokens,
                output_tokens=trace.output_tokens,
                latency_ms=sum(c.latency_ms for c in trace.calls),
                calls=len(trace.calls),
                trace_path=trace_path,
                correct=score.correct,
                complete=score.complete,
                retrieval_ok=score.retrieval_ok,
                abstained=score.abstained,
                expect_abstain=case.expect_abstain,
                gold=case.gold,
                status=case.status,
            )
        )
    summary.finished_at = _now_iso()
    return summary


def score_from_traces(
    cases: list | None = None,
    *,
    trace_dir: Path,
) -> EvalsSummary:
    '''Re-scores already-written trace files under `trace_dir` (one
    `<case.id>.json` per case, area.evals.traces.read_trace's own file
    layout) against the current question set, with no agent loop / model
    provider / database call at all -- `area evals --from-traces` (task
    A1 step 6).

    A case whose trace file doesn't exist on disk is reported with
    `passed=None` and a stated error, rather than raising and losing
    every other case's result.
    '''
    cases = cases if cases is not None else CASES
    trace_dir = Path(trace_dir)
    summary = EvalsSummary()
    summary.started_at = _now_iso()

    for case in cases:
        trace_path = trace_dir / (case.id + '.json')
        if not trace_path.exists():
            summary.results.append(
                EvalCaseResult(
                    case_id=case.id,
                    question=case.question,
                    passed=None,
                    accepted=False,
                    error=f'no trace file at {trace_path}',
                    answer_text=None,
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=0.0,
                    calls=0,
                    trace_path=None,
                    correct=None,
                    complete=None,
                    retrieval_ok=None,
                    abstained=False,
                    expect_abstain=case.expect_abstain,
                    gold=case.gold,
                    status=case.status,
                )
            )
            continue

        trace_dict = read_trace(trace_path)
        summary.model_id = trace_dict.get('model_id', summary.model_id)
        summary.adapter = trace_dict.get('adapter', summary.adapter)
        score = score_case(trace_dict, case)
        calls = trace_dict.get('calls', [])
        summary.results.append(
            EvalCaseResult(
                case_id=case.id,
                question=case.question,
                passed=score.passed,
                accepted=bool(trace_dict.get('accepted', False)),
                error=trace_dict.get('error'),
                answer_text=trace_dict.get('answer_text'),
                input_tokens=trace_dict.get('input_tokens', 0),
                output_tokens=trace_dict.get('output_tokens', 0),
                latency_ms=sum(c.get('latency_ms', 0.0) for c in calls),
                calls=len(calls),
                trace_path=_display_path(trace_path),
                correct=score.correct,
                complete=score.complete,
                retrieval_ok=score.retrieval_ok,
                abstained=score.abstained,
                expect_abstain=case.expect_abstain,
                gold=case.gold,
                status=case.status,
            )
        )
    summary.finished_at = _now_iso()
    return summary

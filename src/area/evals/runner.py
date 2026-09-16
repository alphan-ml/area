'''Evals runner (task W-B4): runs CASES through area.agent.run_agent for
real -- real provider (Bedrock by default), real tool registry, real
database when configured -- writes one trace file per case via
area.evals.traces.write_trace, and reports pass/fail plus real token/
cost/latency totals. Never invents a pass: a case is scored passed only
when the loop hit no error and citation_checker accepted the composed
answer (area.agent.loop.AgentTrace.accepted) -- an honest "no data
available yet" answer (no numbers claimed) passes on the same basis any
other uncontested answer does; a genuinely uncited/mismatched number is
the only thing that fails a case.

Cost is reported only when a price table is supplied (input_price_per_1m/
output_price_per_1m) -- see model-bench.s data/prices.json (this repo.s
sibling) for the source of those numbers; this module never hardcodes or
guesses a price.
'''

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path

from area.agent.loop import run_agent
from area.evals.cases import CASES
from area.evals.traces import write_trace


@dataclass
class EvalCaseResult:
    case_id: str
    question: str
    passed: bool
    accepted: bool
    error: str | None
    answer_text: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: float
    calls: int
    trace_path: str | None


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
            'total': self.total,
            'total_input_tokens': self.total_input_tokens,
            'total_output_tokens': self.total_output_tokens,
            'total_latency_ms': self.total_latency_ms,
            'total_cost_usd': self.total_cost_usd(),
            'results': [r.__dict__ for r in self.results],
        }


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def run_evals(
    cases: list | None = None,
    *,
    provider=None,
    model_id: str | None = None,
    adapter: str | None = None,
    database_url: str | None = None,
    trace_dir: Path | None = None,
    input_price_per_1m: float | None = None,
    output_price_per_1m: float | None = None,
) -> EvalsSummary:
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
            database_url=database_url,
        )
        summary.model_id = trace.model_id
        summary.adapter = trace.adapter
        passed = trace.error is None and trace.accepted
        trace_path = None
        if trace_dir is not None:
            written = write_trace(trace, trace_dir / (case.id + '.json'))
            trace_path = str(written)
        summary.results.append(
            EvalCaseResult(
                case_id=case.id,
                question=case.question,
                passed=passed,
                accepted=trace.accepted,
                error=trace.error,
                answer_text=trace.answer_text,
                input_tokens=trace.input_tokens,
                output_tokens=trace.output_tokens,
                latency_ms=sum(c.latency_ms for c in trace.calls),
                calls=len(trace.calls),
                trace_path=trace_path,
            )
        )
    summary.finished_at = _now_iso()
    return summary

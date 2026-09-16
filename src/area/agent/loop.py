'''Agent loop (SPEC-area.md section 5, task W-B4): planner -> actor ->
composer -> verifier, iterated until an accepted, citation-checked
answer or MAX_STEPS is reached.

Planner: asks the configured model provider (area.providers, same layer
model-bench uses) to pick the single next action, given the question and
the tool-call observations gathered so far. The prompt lists the tool
registry.s own name/description/input_schema verbatim (area.tools.registry) --
never a hand-duplicated copy that could drift from what actor actually
calls. Reply format is one JSON object: either a tool call or a signal
that enough evidence exists to answer.

Actor: executes exactly the tool call the planner asked for, via the
registry, unmodified args -- query_tool additionally gets database_url
threaded through when the caller supplied one (run_agent.s own
database_url kwarg), since that tool.s own default only falls back to
$DATABASE_URL / $AREA_READER_DATABASE_URL.

Composer: once the planner signals it.s ready (or max_steps is hit),
asks the model to draft the final answer text with inline citation
markers over the evidence collected so far (CONTEXT.md D12.s wire
format: a number immediately followed by e.g. [q_ab12cd34]). The prompt
hands the model the raw evidence payloads so it never has to invent a
number that is not already present in them, and explicitly allows an
honest no-data answer (no numbers, no citations) when evidence is empty
or null.

Verifier: runs area.tools.citation_checker.check() (the pure function,
not the registry ToolResult wrapper, since the evidence is already in
hand as dataclasses here) against the composed answer; AgentTrace.accepted
records the verdict. Per SPEC-area.md section 1.s hard gate (.an answer
with an unverified number is not shown.), a caller (CLI, evals runner,
/api/ask) must check .accepted before displaying answer_text to a
visitor -- this module does not retry past max_steps.s single compose
attempt; ordering a second compose pass on a failed verify is a later
refinement, not required for this task.s v0 loop.

Every model call goes through area.providers.get_provider() and its
CallResult (input/output tokens, latency, adapter, http_status, error) is
appended to AgentTrace.calls, so a caller can report exact real cost --
no estimate, no fabricated number, matching the rest of this codebase.s
D10/D11 discipline.

CONTEXT.md D18: Claude Haiku 4.5 on Bedrock is blocked for this AWS
account as of 2026-09-16 (Converse raises ResourceNotFoundException,
.Model use case details have not been submitted for this account. --
verified live from this same run, matching model-bench.s own
data/prices.json finding for the identical model id/account). Amazon
Nova Lite (us.amazon.nova-lite-v1:0) is the real, currently-invocable
Bedrock model this loop defaults to; override via model_id= or the
$AREA_MODEL_ID env var once the account.s Anthropic use-case form
clears and Haiku/Sonnet become callable again.
'''

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from area.providers import get_provider
from area.tools import registry as default_registry
from area.tools.citation_checker import check as citation_check

DEFAULT_MODEL_ID = 'us.amazon.nova-lite-v1:0'
MAX_STEPS = 4
PLANNER_MAX_TOKENS = 400
COMPOSER_MAX_TOKENS = 500


@dataclass
class AgentStep:
    kind: str  # 'plan' | 'tool_call' | 'compose' | 'verify'
    detail: dict


@dataclass
class AgentTrace:
    question: str
    model_id: str
    adapter: str
    steps: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    answer_text: str | None = None
    accepted: bool = False
    unverified: list = field(default_factory=list)
    error: str | None = None

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    def to_dict(self) -> dict:
        return {
            'question': self.question,
            'model_id': self.model_id,
            'adapter': self.adapter,
            'steps': [{'kind': s.kind, 'detail': s.detail} for s in self.steps],
            'calls': [
                {
                    'adapter': c.adapter,
                    'input_tokens': c.input_tokens,
                    'output_tokens': c.output_tokens,
                    'latency_ms': c.latency_ms,
                    'http_status': c.http_status,
                    'retries': c.retries,
                    'error': c.error,
                }
                for c in self.calls
            ],
            'evidence': [
                {'evidence_id': e.evidence_id, 'kind': e.kind, 'payload': e.payload}
                for e in self.evidence
            ],
            'answer_text': self.answer_text,
            'accepted': self.accepted,
            'unverified': self.unverified,
            'error': self.error,
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
        }


def _tool_menu(tools: dict) -> str:
    lines = []
    for name, tool in tools.items():
        lines.append(name + ': ' + tool.description)
        lines.append('  input_schema: ' + json.dumps(tool.input_schema))
    return chr(10).join(lines)


def _extract_json(text: str) -> dict:
    start = text.find('{')
    if start == -1:
        raise ValueError('no JSON object found in model reply')
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError('unbalanced JSON object in model reply')


def _summarize(data: Any, max_rows: int = 20) -> Any:
    if isinstance(data, dict) and 'rows' in data and isinstance(data['rows'], list):
        return {**data, 'rows': data['rows'][:max_rows]}
    return data


def _build_planner_prompt(question: str, tools: dict, observations: list) -> str:
    menu = _tool_menu(tools)
    obs = chr(10).join('- ' + o for o in observations) if observations else '(none yet)'
    return (
        'You are the planning step of a research agent answering questions about '
        'GLP-1 manufacturer payments, from CMS Open Payments data.' + chr(10) + chr(10) +
        'Question: ' + question + chr(10) + chr(10) +
        'Available tools:' + chr(10) + menu + chr(10) + chr(10) +
        'Observations so far:' + chr(10) + obs + chr(10) + chr(10) +
        'Decide the single next action. Reply with ONLY one JSON object, ' + chr(10) +
        'no prose, no markdown fences.' + chr(10) +
        'To call a tool: {"action": "tool_call", "tool": "<name>", ' + chr(10) +
        '"args": {...matching its input_schema...}}' + chr(10) +
        'If enough evidence exists to answer, or no tool call would help: ' + chr(10) +
        '{"action": "answer"}'
    )


def _build_composer_prompt(question: str, evidence_by_id: dict, observations: list) -> str:
    ev_payload = {
        eid: {'kind': ev.kind, 'payload': ev.payload} for eid, ev in evidence_by_id.items()
    }
    ev_json = json.dumps(ev_payload, default=str)[:6000]
    obs = chr(10).join(observations) if observations else '(none)'
    return (
        'Write a short, plain-English answer to this question, using ONLY the '
        'evidence given below -- never invent, round, or estimate a number that is '
        'not directly present in the evidence.' + chr(10) + chr(10) +
        'Question: ' + question + chr(10) + chr(10) +
        'Evidence (evidence_id -> kind/payload):' + chr(10) + ev_json + chr(10) + chr(10) +
        'Tool observations:' + chr(10) + obs + chr(10) + chr(10) +
        'Rules:' + chr(10) +
        '- Every number you state (except a bare year) must be ' + chr(10) +
        'immediately followed by its citation marker, e.g. ' + chr(10) +
        '$1,234.56 [q_ab12cd34] or 24.7% [d_share1], where the id ' + chr(10) +
        'matches one of the evidence_ids above.' + chr(10) +
        '- If the evidence is empty, or every relevant total is ' + chr(10) +
        'null/zero-row, say plainly that no matching data is ' + chr(10) +
        'available yet -- do not invent a number or a citation.' + chr(10) +
        '- Answer in 2-4 sentences. No markdown.'
    )


def run_agent(
    question: str,
    *,
    provider=None,
    model_id: str | None = None,
    adapter: str | None = None,
    tools: dict | None = None,
    database_url: str | None = None,
    max_steps: int = MAX_STEPS,
) -> AgentTrace:
    '''Runs the planner/actor/composer/verifier loop for one question.
    Every argument has a real default (env-driven adapter/model, the real
    tool registry) so a bare run_agent(question) is a real, non-fake run;
    tests override provider/tools to stay offline.'''
    adapter = adapter or os.environ.get('AREA_ADAPTER', 'bedrock')
    model_id = model_id or os.environ.get('AREA_MODEL_ID', DEFAULT_MODEL_ID)
    provider = provider or get_provider(adapter)
    tool_registry = tools if tools is not None else default_registry()
    trace = AgentTrace(question=question, model_id=model_id, adapter=adapter)

    evidence_by_id: dict = {}
    observations: list = []

    for _ in range(max_steps):
        prompt = _build_planner_prompt(question, tool_registry, observations)
        call = provider.call(model_id, prompt, PLANNER_MAX_TOKENS, 0.0)
        trace.calls.append(call)
        if call.error:
            trace.error = 'planner call failed: ' + call.error
            trace.steps.append(AgentStep('plan', {'error': call.error}))
            break
        try:
            plan = _extract_json(call.text)
        except ValueError as exc:
            trace.steps.append(AgentStep('plan', {'raw': call.text, 'parse_error': str(exc)}))
            observations.append(
                'Your last reply could not be parsed as JSON (' + str(exc) + '). '
                'Reply with exactly one JSON object, nothing else.'
            )
            continue

        trace.steps.append(AgentStep('plan', plan))
        action = plan.get('action')

        if action == 'tool_call':
            tool_name = plan.get('tool')
            args = plan.get('args') or {}
            tool = tool_registry.get(tool_name)
            if tool is None:
                trace.steps.append(
                    AgentStep('tool_call', {'tool': tool_name, 'error': 'unknown tool'})
                )
                observations.append(
                    'Unknown tool ' + repr(tool_name) + '. Choose one of: ' +
                    ', '.join(tool_registry) + '.'
                )
                continue
            if tool_name == 'query_tool' and database_url:
                result = tool.fn(args, database_url=database_url)
            else:
                result = tool.fn(args)
            trace.steps.append(
                AgentStep(
                    'tool_call',
                    {
                        'tool': tool_name,
                        'args': args,
                        'ok': result.ok,
                        'error': result.error,
                        'data': _summarize(result.data),
                    },
                )
            )
            for ev in result.evidence:
                evidence_by_id[ev.evidence_id] = ev
            if result.ok:
                observations.append(
                    tool_name + ' succeeded: ' +
                    json.dumps(_summarize(result.data), default=str)[:800]
                )
            else:
                observations.append(tool_name + ' failed: ' + str(result.error))
            continue

        if action == 'answer':
            break

        observations.append(
            'Unrecognized action ' + repr(action) + 
            '. Reply with {"action": "tool_call", ...} or {"action": "answer"}.'
        )

    compose_prompt = _build_composer_prompt(question, evidence_by_id, observations)
    call = provider.call(model_id, compose_prompt, COMPOSER_MAX_TOKENS, 0.0)
    trace.calls.append(call)
    trace.evidence = list(evidence_by_id.values())
    if call.error:
        trace.error = (
            (trace.error + '; ' if trace.error else '') + 'composer call failed: ' + call.error
        )
        trace.steps.append(AgentStep('compose', {'error': call.error}))
        return trace

    trace.answer_text = call.text.strip()
    trace.steps.append(AgentStep('compose', {'answer_text': trace.answer_text}))

    check_result = citation_check(trace.answer_text, list(evidence_by_id.values()), [])
    trace.accepted = check_result.accepted
    trace.unverified = check_result.unverified
    trace.steps.append(
        AgentStep(
            'verify',
            {'accepted': check_result.accepted, 'unverified': check_result.unverified},
        )
    )
    return trace

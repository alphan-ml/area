'''Offline tests for area.agent.loop -- zero network, zero secrets. A
ScriptedProvider stands in for area.providers.Provider and returns a
fixed sequence of replies (one per call), so the planner/actor/composer/
verifier wiring is exercised deterministically. Real-Bedrock behavior is
exercised separately by area evals (task W-B4.s own real run, reported
in CONTEXT.md, not re-run here -- these tests must pass with no network).
'''

from __future__ import annotations

import json

from area.agent.loop import AgentTrace, run_agent
from area.providers.base import CallResult
from area.tools import Evidence, Tool, ToolResult


class ScriptedProvider:
    def __init__(self, replies: list):
        self._replies = list(replies)
        self.calls = 0

    def call(self, model_id, prompt, max_tokens, temperature):
        self.calls += 1
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


def _fake_tools_with_one_number():
    def fn(args):
        return ToolResult(
            ok=True,
            data={'rows': [{'total_usd': 42.0}]},
            evidence=[
                Evidence('q_test1234', 'sql_rows', [{'total_usd': 42.0}], {'sql': 'SELECT 42'})
            ],
        )

    tool = Tool(
        name='query_tool',
        description='test stub',
        input_schema={'type': 'object', 'properties': {}},
        fn=fn,
    )
    return {'query_tool': tool}


def test_agent_loop_calls_tool_then_composes_accepted_answer():
    tools = _fake_tools_with_one_number()
    replies = [
        json.dumps({'action': 'tool_call', 'tool': 'query_tool', 'args': {'question': 'q'}}),
        json.dumps({'action': 'answer'}),
        'The total was $42.00 [q_test1234].',
    ]
    provider = ScriptedProvider(replies)
    trace = run_agent('What was the total?', provider=provider, model_id='m', tools=tools)

    assert isinstance(trace, AgentTrace)
    assert trace.error is None
    assert trace.accepted is True
    assert trace.answer_text == 'The total was $42.00 [q_test1234].'
    assert len(trace.evidence) == 1
    assert trace.evidence[0].evidence_id == 'q_test1234'
    assert trace.input_tokens > 0
    assert trace.output_tokens > 0
    tool_call_steps = [s for s in trace.steps if s.kind == 'tool_call']
    assert len(tool_call_steps) == 1
    assert tool_call_steps[0].detail['ok'] is True


def test_agent_loop_rejects_uncited_number():
    tools = _fake_tools_with_one_number()
    replies = [
        json.dumps({'action': 'answer'}),
        'The total was $99.00 with no citation at all.',
    ]
    provider = ScriptedProvider(replies)
    trace = run_agent('What was the total?', provider=provider, model_id='m', tools=tools)

    assert trace.accepted is False
    assert trace.unverified


def test_agent_loop_recovers_from_unparseable_planner_reply():
    tools = _fake_tools_with_one_number()
    replies = [
        'not json at all',
        json.dumps({'action': 'answer'}),
        'No matching data is available yet.',
    ]
    provider = ScriptedProvider(replies)
    trace = run_agent('What was the total?', provider=provider, model_id='m', tools=tools)

    assert trace.error is None
    plan_steps = [s for s in trace.steps if s.kind == 'plan']
    assert any('parse_error' in s.detail for s in plan_steps)
    assert trace.accepted is True  # no numbers claimed -> nothing to fail verification


def test_agent_loop_reports_planner_call_error_without_crashing():
    class FailingProvider:
        def call(self, model_id, prompt, max_tokens, temperature):
            return CallResult(
                text='',
                input_tokens=0,
                output_tokens=0,
                latency_ms=0.0,
                adapter='scripted',
                http_status=500,
                retries=3,
                error='boom',
            )

    trace = run_agent('q', provider=FailingProvider(), model_id='m', tools={})
    assert trace.error is not None
    assert 'planner call failed' in trace.error


def test_agent_loop_stops_at_max_steps_and_still_composes():
    tools = _fake_tools_with_one_number()
    # Planner never says 'answer' -- loop must stop after max_steps and
    # still attempt to compose rather than looping forever.
    replies = [json.dumps({'action': 'tool_call', 'tool': 'query_tool', 'args': {}})] * 10
    replies.append('No matching data is available yet.')
    provider = ScriptedProvider(replies)
    trace = run_agent('q', provider=provider, model_id='m', tools=tools, max_steps=2)

    tool_call_steps = [s for s in trace.steps if s.kind == 'tool_call']
    assert len(tool_call_steps) == 2
    assert trace.answer_text is not None

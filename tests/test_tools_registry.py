"""Tests for area.tools.registry() (SPEC-area.md section 4.1)."""

from __future__ import annotations

from area.tools import Tool, registry


def test_registry_returns_exactly_the_three_spec_tools():
    tools = registry()
    assert set(tools) == {"query_tool", "forecast_tool", "citation_checker"}
    for name, tool in tools.items():
        assert isinstance(tool, Tool)
        assert tool.name == name
        assert tool.description  # plain English for the planner, non-empty
        assert isinstance(tool.input_schema, dict)
        assert callable(tool.fn)


def test_registry_input_schemas_declare_their_required_fields():
    tools = registry()
    assert "question" in tools["query_tool"].input_schema["required"]
    assert "series" in tools["forecast_tool"].input_schema["required"]
    assert "answer_text" in tools["citation_checker"].input_schema["required"]


def test_citation_checker_tool_runs_via_the_registry():
    tools = registry()
    result = tools["citation_checker"].fn(
        {
            "answer_text": "Total was $5 [q_1].",
            "evidence": [{"evidence_id": "q_1", "kind": "sql_rows", "payload": [{"total": 5}]}],
        }
    )
    assert result.ok is True
    assert result.data["accepted"] is True

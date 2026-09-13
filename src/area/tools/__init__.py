"""Tool registry (SPEC-area.md section 4.1).

`registry()` returns the three tools defined for AREA v0: query_tool,
forecast_tool, citation_checker. Adding a fourth tool means adding one
file under src/area/tools/ and one line in registry() below -- this is
the "connect to anything" seam the spec asks to be documented (README
covers the how; this docstring covers the why: a Tool is just a name, a
plain-English description for the planner, a JSON input schema, and a
function from args-dict to ToolResult -- nothing about the agent loop
(planner/actor/verifier, task B4) needs to change to add one).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Evidence:
    """One piece of evidence a tool call produced. `evidence_id` is what
    the composer cites inline (e.g. "[q_ab12cd34]") and what
    citation_checker.py looks up to verify a number. `kind` is one of
    "sql_rows" | "forecast" | "doc". `payload` is the kind-specific data
    (e.g. the row list for "sql_rows"). `provenance` is a small dict of
    how-to-reproduce-this info (e.g. the SQL text and execution time for
    "sql_rows") -- never itself a source of truth for a number, just an
    audit trail.
    """

    evidence_id: str
    kind: str
    payload: Any
    provenance: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    ok: bool
    data: Any
    evidence: list[Evidence]
    error: str | None = None


@dataclass
class Tool:
    name: str
    description: str  # plain English for the planner
    input_schema: dict  # JSON schema
    fn: Callable[[dict], ToolResult]


def registry() -> dict[str, Tool]:
    """Returns {tool_name: Tool}, built fresh each call so tests can swap
    in fakes (e.g. a fake `generate_sql`) without import-order tricks.
    Imports each tool module lazily inside this function, not at package
    import time, so importing `area.tools` never requires every tool's
    own dependencies (e.g. sqlparse for query_tool) to be importable --
    mirrors the lazy-adapter-import pattern in area.providers.
    """
    from area.tools.citation_checker import TOOL as citation_checker_tool
    from area.tools.forecast_tool import TOOL as forecast_tool_tool
    from area.tools.query_tool import TOOL as query_tool_tool

    tools = [query_tool_tool, forecast_tool_tool, citation_checker_tool]
    return {t.name: t for t in tools}


__all__ = ["Evidence", "ToolResult", "Tool", "registry"]

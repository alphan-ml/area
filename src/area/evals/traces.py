'''Trace writer/reader for agent runs (task W-B4). One JSON file per run
(area.agent.loop.AgentTrace.to_dict()) -- the full plan/tool_call/
compose/verify step log, every model CallResult, and all evidence
collected, so a run can be audited or replayed without re-calling the
model or the database.'''

from __future__ import annotations

import json
from pathlib import Path

from area.agent.loop import AgentTrace


def write_trace(trace: AgentTrace, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trace.to_dict(), indent=2, default=str))
    return path


def read_trace(path: Path) -> dict:
    return json.loads(Path(path).read_text())

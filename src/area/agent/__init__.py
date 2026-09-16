"""Agent loop package (SPEC-area.md section 5, task W-B4).

run_agent() is the public entry point: planner -> actor -> composer ->
verifier, iterated until an accepted, citation-checked answer or
MAX_STEPS is reached. See loop.py's module docstring for the full
design.
"""

from area.agent.loop import DEFAULT_MODEL_ID, AgentStep, AgentTrace, run_agent

__all__ = ["run_agent", "AgentTrace", "AgentStep", "DEFAULT_MODEL_ID"]

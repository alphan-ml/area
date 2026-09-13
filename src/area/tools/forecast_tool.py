"""Forecast tool (SPEC-area.md section 4.3). Reads the precomputed
`forecast/latest.json` (written by `area forecast`, i.e.
area.forecast.holdout.run() -- see that module's docstring for exactly
what it contains and why) and returns the point forecast, 80% interval,
and holdout MAE/coverage for both models, plus the training window.

This tool never computes a forecast itself -- it is a read-only lookup,
per the spec's own wording ("Reads forecast/latest.json..."). If that
file doesn't exist yet (true right now: no real data has been pulled,
D11 defers that to Gate 1) or doesn't have the requested series, it
returns ok=False with a plain-English reason rather than inventing
numbers -- section 1's non-negotiable applies to every number this
codebase surfaces, not just data/facts.md's.
"""

from __future__ import annotations

import json
from pathlib import Path

from area.tools import Evidence, Tool, ToolResult

DEFAULT_FORECAST_PATH = Path("forecast/latest.json")


def _evidence_id(series_key: str) -> str:
    safe = series_key.replace(":", "_").replace(" ", "_")
    return f"f_{safe}"


def run(args: dict, *, path: Path | None = None) -> ToolResult:
    series_key = args.get("series")
    horizon = args.get("horizon", 4)
    if not series_key:
        return ToolResult(ok=False, data=None, evidence=[], error="'series' is required")

    forecast_path = path or DEFAULT_FORECAST_PATH
    if not forecast_path.exists():
        return ToolResult(
            ok=False,
            data=None,
            evidence=[],
            error=(
                f"{forecast_path} does not exist -- run `area forecast` after a real "
                "data pull and load (see CONTEXT.md's open items on the real pull)"
            ),
        )

    try:
        latest = json.loads(forecast_path.read_text())
    except json.JSONDecodeError as exc:
        return ToolResult(
            ok=False, data=None, evidence=[], error=f"{forecast_path} is not valid JSON: {exc}"
        )

    if series_key in latest.get("skipped", {}):
        return ToolResult(
            ok=False, data=None, evidence=[],
            error=f"series {series_key!r} has no forecast: {latest['skipped'][series_key]}",
        )

    series_data = latest.get("series", {}).get(series_key)
    if series_data is None:
        known = sorted(latest.get("series", {}))
        return ToolResult(
            ok=False, data=None, evidence=[],
            error=f"unknown series {series_key!r}; known series: {known}",
        )

    forecast = series_data["forecast"]
    available_horizon = len(forecast["quarters"])
    if horizon > available_horizon:
        return ToolResult(
            ok=False, data=None, evidence=[],
            error=(
                f"requested horizon {horizon} exceeds the precomputed horizon "
                f"{available_horizon} for series {series_key!r}; re-run `area forecast` "
                "with a longer horizon to get more quarters"
            ),
        )

    data = {
        "series": series_key,
        "primary_model": series_data["primary_model"],
        "training_window": series_data["training_window"],
        "quarters": forecast["quarters"][:horizon],
        "point": forecast["point"][:horizon],
        "lower_80": forecast["lower_80"][:horizon],
        "upper_80": forecast["upper_80"][:horizon],
        "holdout": series_data["holdout"],
    }
    evidence = [
        Evidence(
            evidence_id=_evidence_id(series_key),
            kind="forecast",
            payload=data,
            provenance={"source_file": str(forecast_path)},
        )
    ]
    return ToolResult(ok=True, data=data, evidence=evidence, error=None)


TOOL = Tool(
    name="forecast_tool",
    description=(
        "Returns the precomputed quarterly forecast (point estimate, 80% interval) "
        "and holdout accuracy (MAE, 80% coverage) for both forecast models, for the "
        "national series or one specialty's series. Never computes a new forecast; "
        "run `area forecast` to refresh forecast/latest.json first."
    ),
    input_schema={
        "type": "object",
        "required": ["series"],
        "properties": {
            "series": {"type": "string", "description": '"national" or "specialty:<name>"'},
            "horizon": {"type": "integer", "minimum": 1, "default": 4},
        },
    },
    fn=run,
)

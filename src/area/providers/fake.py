"""A fake Provider for local, secret-free tests only.

Vendored from model-bench's `src/modelbench/providers/fake.py`, adapted:
model-bench's FakeProvider echoes back an "intent" classification shaped
response (it reads an allowed-intent list out of the prompt). AREA's
tools don't classify intent, so this fake instead echoes back a plain
JSON-wrapped SQL SELECT stub -- useful for exercising query_tool.py's
plumbing end to end without a real model call. It is never a real
adapter choice for a production run (not documented in .env.example's
AREA_ADAPTER values); tests that need to exercise the guardrail
validator itself inject their own `generate_sql` callable directly
instead of going through this class, since the whole point of the
guardrail tests is controlling exactly what SQL text arrives.
"""

from __future__ import annotations

import json
import random
import time

from area.providers.base import CallResult


class FakeProvider:
    """Always succeeds -- exists to test pipeline plumbing (that a Provider
    gets called and its CallResult flows through), not SQL-writing
    accuracy. Returns a syntactically valid, trivially safe SELECT."""

    def call(self, model_id: str, prompt: str, max_tokens: int, temperature: float) -> CallResult:
        start = time.perf_counter()
        text = json.dumps({"sql": "SELECT 1 AS smoke_test"})
        latency_ms = (time.perf_counter() - start) * 1000 + random.uniform(5, 20)
        return CallResult(
            text=text,
            input_tokens=len(prompt.split()),
            output_tokens=len(text.split()),
            latency_ms=latency_ms,
            adapter="fake",
            http_status=200,
            retries=0,
            error=None,
        )

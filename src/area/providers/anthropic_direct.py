"""Fallback adapter: calls the Anthropic API directly, bypassing Bedrock.

Vendored from model-bench's `src/modelbench/providers/anthropic_direct.py`
(see providers/base.py's module docstring for why). Used only if Bedrock
access isn't granted for the Anthropic-vendor model keys -- a run is still
all one adapter, this is a manual per-model-key override, never automatic.
Implemented with the standard library (urllib) rather than the `anthropic`
SDK, to keep this fallback-only path dependency-free.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from area.providers.base import CallResult, RetryableError, call_with_retries

_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class AnthropicDirectProvider:
    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    def call(self, model_id: str, prompt: str, max_tokens: int, temperature: float) -> CallResult:
        if not self._api_key:
            return CallResult(
                text="",
                input_tokens=0,
                output_tokens=0,
                latency_ms=0.0,
                adapter="anthropic_direct",
                http_status=0,
                retries=0,
                error="ANTHROPIC_API_KEY is not set",
            )

        body = json.dumps(
            {
                "model": model_id,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode("utf-8")

        def attempt():
            start = time.perf_counter()
            req = urllib.request.Request(
                _API_URL,
                data=body,
                method="POST",
                headers={
                    "content-type": "application/json",
                    "x-api-key": self._api_key,
                    "anthropic-version": _ANTHROPIC_VERSION,
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    payload = json.loads(resp.read())
                    status = resp.status
            except urllib.error.HTTPError as exc:
                status = exc.code
                if status in _RETRYABLE_STATUS:
                    raise RetryableError(
                        f"HTTP {status}: {exc.reason}", http_status=status
                    ) from exc
                raise RuntimeError(f"HTTP {status}: {exc.reason}") from exc
            elapsed_ms = (time.perf_counter() - start) * 1000
            return payload, status, elapsed_ms

        outcome, retries, error, http_status = call_with_retries(attempt)

        if outcome is None:
            return CallResult(
                text="",
                input_tokens=0,
                output_tokens=0,
                latency_ms=0.0,
                adapter="anthropic_direct",
                http_status=http_status,
                retries=retries,
                error=error,
            )

        payload, status, elapsed_ms = outcome
        content = payload.get("content", [])
        text = content[0]["text"] if content else ""
        usage = payload.get("usage", {})

        return CallResult(
            text=text,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            latency_ms=elapsed_ms,
            adapter="anthropic_direct",
            http_status=status,
            retries=retries,
            error=None,
        )

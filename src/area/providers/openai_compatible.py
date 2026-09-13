"""Fallback adapter for any OpenAI-compatible hosted chat-completions
endpoint.

Vendored from model-bench's `src/modelbench/providers/openai_compatible.py`
(see providers/base.py's module docstring for why). Used only if Bedrock
isn't available for the non-Anthropic model keys and a direct vendor path
is needed. Implemented with the standard library (urllib), same as
anthropic_direct.py, to keep this fallback-only path dependency-free.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from area.providers.base import CallResult, RetryableError, call_with_retries

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class OpenAICompatibleProvider:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self._base_url = (
            base_url or os.environ.get("OPENAI_COMPATIBLE_BASE_URL") or ""
        ).rstrip("/")
        self._api_key = api_key or os.environ.get("OPENAI_COMPATIBLE_API_KEY")

    def call(self, model_id: str, prompt: str, max_tokens: int, temperature: float) -> CallResult:
        if not self._base_url or not self._api_key:
            return CallResult(
                text="",
                input_tokens=0,
                output_tokens=0,
                latency_ms=0.0,
                adapter="openai_compatible",
                http_status=0,
                retries=0,
                error="OPENAI_COMPATIBLE_BASE_URL / OPENAI_COMPATIBLE_API_KEY not set",
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
                f"{self._base_url}/chat/completions",
                data=body,
                method="POST",
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {self._api_key}",
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
                adapter="openai_compatible",
                http_status=http_status,
                retries=retries,
                error=error,
            )

        payload, status, elapsed_ms = outcome
        choice = payload.get("choices", [{}])[0]
        text = choice.get("message", {}).get("content", "")
        usage = payload.get("usage", {})

        return CallResult(
            text=text,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=elapsed_ms,
            adapter="openai_compatible",
            http_status=status,
            retries=retries,
            error=None,
        )

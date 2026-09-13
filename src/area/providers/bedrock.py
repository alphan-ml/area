"""Bedrock adapter using the Converse API (boto3 bedrock-runtime).

Vendored from model-bench's `src/modelbench/providers/bedrock.py` (see
providers/base.py's module docstring for why). Token counts come back
from the service (the Converse API's usage block), region comes from
AWS_REGION, and retries happen only on throttling and 5xx (up to 5
attempts, exponential backoff with jitter -- see providers/base.py's
call_with_retries).
"""

from __future__ import annotations

import os
import time

from area.providers.base import CallResult, RetryableError, call_with_retries

_RETRYABLE_ERROR_CODES = {
    "ThrottlingException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelTimeoutException",
}


class BedrockProvider:
    def __init__(self, region: str | None = None):
        self._region = region or os.environ.get("AWS_REGION")
        self._client = None  # created lazily so import-time never touches AWS

    def _get_client(self):
        if self._client is None:
            import boto3  # imported lazily so tests never need it installed

            if not self._region:
                raise RuntimeError("AWS_REGION is not set")
            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    def call(self, model_id: str, prompt: str, max_tokens: int, temperature: float) -> CallResult:
        try:
            client = self._get_client()
        except Exception as exc:
            # Missing AWS_REGION, boto3 not installed, or client construction
            # otherwise failing -- report it as a failed call, same as the
            # other adapters do for missing config, rather than crashing the
            # whole run.
            return CallResult(
                text="",
                input_tokens=0,
                output_tokens=0,
                latency_ms=0.0,
                adapter="bedrock",
                http_status=0,
                retries=0,
                error=str(exc),
            )

        def attempt():
            start = time.perf_counter()
            try:
                response = client.converse(
                    modelId=model_id,
                    messages=[{"role": "user", "content": [{"text": prompt}]}],
                    inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
                )
            except Exception as exc:  # botocore ClientError and friends
                error_code = None
                http_status = 0
                response_attr = getattr(exc, "response", None)
                if isinstance(response_attr, dict):
                    error_code = response_attr.get("Error", {}).get("Code")
                    http_status = response_attr.get("ResponseMetadata", {}).get(
                        "HTTPStatusCode", 0
                    )
                if error_code in _RETRYABLE_ERROR_CODES or (500 <= http_status < 600):
                    raise RetryableError(str(exc), http_status=http_status) from exc
                raise
            elapsed_ms = (time.perf_counter() - start) * 1000
            return response, elapsed_ms

        outcome, retries, error, http_status = call_with_retries(attempt)

        if outcome is None:
            return CallResult(
                text="",
                input_tokens=0,
                output_tokens=0,
                latency_ms=0.0,
                adapter="bedrock",
                http_status=http_status,
                retries=retries,
                error=error,
            )

        response, elapsed_ms = outcome
        usage = response.get("usage", {})
        content = response.get("output", {}).get("message", {}).get("content", [])
        text = content[0]["text"] if content else ""
        http_status = response.get("ResponseMetadata", {}).get("HTTPStatusCode", 200)

        return CallResult(
            text=text,
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            latency_ms=elapsed_ms,
            adapter="bedrock",
            http_status=http_status,
            retries=retries,
            error=None,
        )

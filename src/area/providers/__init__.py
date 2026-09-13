"""Provider factory. get_provider(name) -> Provider. Adapter modules are
imported lazily inside each branch so importing this package never
requires every adapter's dependencies to be installed.

Vendored from model-bench's `src/modelbench/providers/__init__.py` (see
providers/base.py's module docstring for why); "fake" is renamed in
intent (not name) for area -- see fake.py's docstring.
"""

from __future__ import annotations

from area.providers.base import CallResult, Provider

_KNOWN_ADAPTERS = ("bedrock", "anthropic_direct", "openai_compatible", "fake")


def get_provider(name: str) -> Provider:
    if name == "bedrock":
        from area.providers.bedrock import BedrockProvider

        return BedrockProvider()
    if name == "anthropic_direct":
        from area.providers.anthropic_direct import AnthropicDirectProvider

        return AnthropicDirectProvider()
    if name == "openai_compatible":
        from area.providers.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider()
    if name == "fake":
        # Local smoke tests only. Not a valid choice for a real run: it is
        # not documented in .env.example's AREA_ADAPTER values.
        from area.providers.fake import FakeProvider

        return FakeProvider()
    raise ValueError(
        f"unknown adapter {name!r}; expected one of: {', '.join(_KNOWN_ADAPTERS)} "
        "('fake' is for local smoke tests only)"
    )


__all__ = ["CallResult", "Provider", "get_provider"]

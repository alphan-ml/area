"""Loads .env into the environment for local test runs, without overriding
any variable already set. CI sets TEST_DATABASE_URL directly against a
postgres:16 service container (see .env.example) -- this file only matters
on a developer's own machine. Stdlib-only, matching pyproject.toml's
zero-required-runtime-dependency design (no python-dotenv).
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(_ENV_PATH)

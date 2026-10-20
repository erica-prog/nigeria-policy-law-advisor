"""Repository-level pytest configuration.

Most tests mock Claude and run offline. A few end in `_real_call` and need a
real ANTHROPIC_API_KEY; without one they are skipped rather than failed. When
no key is configured at all, a placeholder is exported so modules that build a
client at import or construction time still load."""

import os

import pytest

_PLACEHOLDER_KEY = "test-placeholder-not-a-real-key"


def _real_key_configured() -> bool:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return bool(key) and key != _PLACEHOLDER_KEY


if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
    os.environ["ANTHROPIC_API_KEY"] = _PLACEHOLDER_KEY


def pytest_collection_modifyitems(config, items):
    if _real_key_configured():
        return
    skip = pytest.mark.skip(reason="needs a real ANTHROPIC_API_KEY")
    for item in items:
        if item.name.endswith("_real_call") or "_real_call[" in item.name:
            item.add_marker(skip)

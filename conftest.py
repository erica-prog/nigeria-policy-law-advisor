"""Lets the suite run without an Anthropic key (locally, or on fork PRs, which get no secrets).

Settings requires ANTHROPIC_API_KEY even for tests that mock the client, so a
placeholder is supplied when neither the environment nor .env has one. Tests
named `*_real_call` hit the live API and are skipped in that case instead of
failing with a confusing validation error.
"""

import os

import pytest
from pydantic import ValidationError

from policy_advisor.config import Settings

_PLACEHOLDER_KEY = "test-placeholder-not-a-real-key"


def _real_key_configured() -> bool:
    try:
        Settings()
    except ValidationError:
        return False
    return True


REAL_KEY_CONFIGURED = _real_key_configured()

if not REAL_KEY_CONFIGURED:
    os.environ["ANTHROPIC_API_KEY"] = _PLACEHOLDER_KEY


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if REAL_KEY_CONFIGURED:
        return
    skip = pytest.mark.skip(reason="needs a real ANTHROPIC_API_KEY (environment or .env)")
    for item in items:
        if item.name.endswith("_real_call"):
            item.add_marker(skip)

"""Every test runs against a throwaway database file, never data/advisor.db.

The session-wide default is set here at import time, before any test module
imports policy_advisor, because Settings is read once and cached. That protects
the real file - which holds lawyers' matters and chats - even from tests that
never ask for a database. Tests that need a clean one use `fresh_db`.
"""

import os
import tempfile
from pathlib import Path

import pytest

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp(prefix="policy_advisor_tests_")) / "session.db")


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """An empty, migrated database private to this test."""
    from policy_advisor import db

    path = tmp_path / "test.db"
    monkeypatch.setattr(db, "database_path", lambda: path)
    return path

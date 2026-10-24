"""Fixtures for the web API tests. Everything runs against temporary
directories (credentials and matter metadata) so no test can touch
data/ or a real document. The language model is never called: tests either
inject fake chains or run with the key unset."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from policy_advisor import config
from policy_advisor.api.credentials import create_user
from policy_advisor.api.main import create_app
from policy_advisor.config import get_settings
from policy_advisor.db import connect, database_url
from policy_advisor.ingestion import matter_store
from tests.conftest import _refuse_unsafe

USERS = {"alice": "alice-pass-123", "bob": "bob-pass-456"}


def _create_users() -> None:
    # Same scheme as scripts/add_user.py (bcrypt); low cost for test speed.
    for name, pw in USERS.items():
        create_user(name, name.title(), pw, rounds=4)


@dataclass
class ApiHarness:
    app: object
    client: TestClient
    root: Path

    def login(self, username: str, password: str | None = None) -> TestClient:
        client = TestClient(self.app, raise_server_exceptions=False)  # type: ignore[arg-type]
        response = client.post(
            "/api/auth/login",
            json={"username": username, "password": password or USERS[username]},
        )
        assert response.status_code == 200, response.text
        return client

    @property
    def services(self):
        return self.app.state.services  # type: ignore[attr-defined]


def _build_harness(
    tmp_path: Path, monkeypatch, llm_key: str, shared_key: bool = False
) -> ApiHarness:
    monkeypatch.setenv("ANTHROPIC_API_KEY", llm_key)
    monkeypatch.setenv("ALLOW_SHARED_ANTHROPIC_KEY", "true" if shared_key else "false")
    monkeypatch.setenv("TRANSLATE_ON_INGEST", "false")
    monkeypatch.setenv("AUTH_COOKIE_KEY", "unit-test-cookie-key")
    monkeypatch.setenv("ORIGINALS_DIR", str(tmp_path / "originals"))
    # Settings ignore empty environment values (env_ignore_empty), so point the
    # .env lookup at a file that does not exist; a developer's real .env must
    # never leak into the "no model key" fixture.
    monkeypatch.setitem(config.Settings.model_config, "env_file", str(tmp_path / "absent.env"))
    get_settings.cache_clear()
    # Ownership and documents live in Postgres now. Each test gets an empty
    # database, the same way the file store used to get an empty temp directory.
    _refuse_unsafe(database_url())
    with connect() as conn:
        conn.execute(
            "TRUNCATE chat_exchanges, chunks, documents, conversations, user_keys, users, matters, "
            "index_meta RESTART IDENTITY CASCADE"
        )
    monkeypatch.setattr(config, "AUTH_DIR", tmp_path / "auth")
    monkeypatch.setattr(matter_store, "MATTERS_DIR", tmp_path / "matters")
    _create_users()
    app = create_app(serve_static=False)
    client = TestClient(app, raise_server_exceptions=False)
    return ApiHarness(app=app, client=client, root=tmp_path)


@pytest.fixture
def api(tmp_path, monkeypatch):
    """API with no server key and no user keys: ask/analyze/chat return
    403 claude_key_required until a user stores a key."""
    harness = _build_harness(tmp_path, monkeypatch, llm_key="")
    yield harness
    get_settings.cache_clear()


@pytest.fixture
def api_with_llm(tmp_path, monkeypatch):
    """API with a server key that users are allowed to share
    (ALLOW_SHARED_ANTHROPIC_KEY=true); tests must inject fake chains."""
    harness = _build_harness(tmp_path, monkeypatch, llm_key="fake-key-for-tests", shared_key=True)
    yield harness
    get_settings.cache_clear()


@pytest.fixture
def api_byok(tmp_path, monkeypatch):
    """Bring-your-own-key: the server has a key but sharing is OFF (the
    default), so every user needs their own key."""
    harness = _build_harness(tmp_path, monkeypatch, llm_key="server-key-never-shared")
    yield harness
    get_settings.cache_clear()

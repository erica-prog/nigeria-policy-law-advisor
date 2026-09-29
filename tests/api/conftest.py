"""Fixtures for the web API tests. Everything runs against temporary
directories (credentials, matter metadata, Chroma index) so no test can touch
data/ or a real document. The language model is never called: tests either
inject fake chains or run with the key unset."""

from dataclasses import dataclass
from pathlib import Path

import bcrypt
import pytest
import yaml
from fastapi.testclient import TestClient

from policy_advisor import config
from policy_advisor.api.main import create_app
from policy_advisor.config import get_settings
from policy_advisor.ingestion import matter_store
from policy_advisor.retrieval import vector_store

USERS = {"alice": "alice-pass-123", "bob": "bob-pass-456"}


def _write_credentials(auth_dir: Path) -> None:
    auth_dir.mkdir(parents=True, exist_ok=True)
    usernames = {
        name: {
            "name": name.title(),
            "email": "",
            # Same scheme as scripts/add_user.py (bcrypt); low cost for test speed.
            "password": bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode(),
        }
        for name, pw in USERS.items()
    }
    (auth_dir / "credentials.yaml").write_text(yaml.safe_dump({"usernames": usernames}))


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


def _build_harness(tmp_path: Path, monkeypatch, llm_key: str) -> ApiHarness:
    monkeypatch.setenv("ANTHROPIC_API_KEY", llm_key)
    monkeypatch.setenv("TRANSLATE_ON_INGEST", "false")
    monkeypatch.setenv("AUTH_COOKIE_KEY", "unit-test-cookie-key")
    # Settings ignore empty environment values (env_ignore_empty), so point the
    # .env lookup at a file that does not exist; a developer's real .env must
    # never leak into the "no model key" fixture.
    monkeypatch.setitem(config.Settings.model_config, "env_file", str(tmp_path / "absent.env"))
    get_settings.cache_clear()
    monkeypatch.setattr(config, "AUTH_DIR", tmp_path / "auth")
    monkeypatch.setattr(matter_store, "MATTERS_DIR", tmp_path / "matters")
    monkeypatch.setattr(vector_store, "INDEX_DIR", tmp_path / "index")
    _write_credentials(tmp_path / "auth")
    app = create_app(serve_static=False)
    client = TestClient(app, raise_server_exceptions=False)
    return ApiHarness(app=app, client=client, root=tmp_path)


@pytest.fixture
def api(tmp_path, monkeypatch):
    """API with no model key configured (ask/analyze return 503)."""
    harness = _build_harness(tmp_path, monkeypatch, llm_key="")
    yield harness
    get_settings.cache_clear()


@pytest.fixture
def api_with_llm(tmp_path, monkeypatch):
    """API that believes a model key exists; tests must inject fake chains."""
    harness = _build_harness(tmp_path, monkeypatch, llm_key="fake-key-for-tests")
    yield harness
    get_settings.cache_clear()

"""The credential store (policy_advisor.api.credentials): file format,
concurrent-safe writes, and compatibility with hashes written by the
Streamlit-era prototype."""

import shutil
from pathlib import Path

import pytest

from policy_advisor import config
from policy_advisor.api import credentials

FIXTURES = Path(__file__).parent / "fixtures"
LEGACY_PASSWORD = "legacy-pass-123"


@pytest.fixture
def auth_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(config, "AUTH_DIR", tmp_path / "auth")
    return tmp_path / "auth"


def test_load_credentials_returns_empty_usernames_when_file_missing(auth_dir):
    assert credentials.load_credentials() == {"usernames": {}}


def test_load_credentials_round_trips_a_written_file(auth_dir):
    auth_dir.mkdir()
    (auth_dir / "credentials.yaml").write_text(
        "usernames:\n  jdoe:\n    name: Jane Doe\n    password: some-bcrypt-hash\n    email: ''\n",
        encoding="utf-8",
    )
    loaded = credentials.load_credentials()
    assert loaded["usernames"]["jdoe"]["name"] == "Jane Doe"
    assert loaded["usernames"]["jdoe"]["password"] == "some-bcrypt-hash"


def test_save_credentials_merges_without_dropping_existing_accounts(auth_dir):
    credentials.save_credentials(
        {"usernames": {"first": {"name": "First", "password": "h1", "email": ""}}}
    )
    credentials.save_credentials(
        {"usernames": {"second": {"name": "Second", "password": "h2", "email": ""}}}
    )
    loaded = credentials.load_credentials()
    assert set(loaded["usernames"]) == {"first", "second"}
    assert not (auth_dir / "credentials.yaml.tmp").exists()


def test_hash_password_output_verifies_and_is_bcrypt(auth_dir):
    hashed = credentials.hash_password("s3cret-pass")
    assert hashed.startswith("$2b$")
    credentials.save_credentials(
        {"usernames": {"u": {"name": "U", "password": hashed, "email": ""}}}
    )
    assert credentials.verify_credentials("u", "s3cret-pass") is not None
    assert credentials.verify_credentials("u", "wrong") is None


def test_legacy_streamlit_authenticator_hash_still_verifies(auth_dir):
    # AC-3 in docs/20: an account created by the old UI must still log in.
    auth_dir.mkdir()
    shutil.copy(FIXTURES / "legacy_credentials.yaml", auth_dir / "credentials.yaml")
    record = credentials.verify_credentials("legacy", LEGACY_PASSWORD)
    assert record is not None
    assert record.display_name == "Legacy Lawyer"
    assert credentials.verify_credentials("legacy", LEGACY_PASSWORD + "x") is None

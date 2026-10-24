"""Accounts in the `users` table (docs/22 slice P1-C2, AC-8): creation,
verification, and compatibility with the bcrypt hashes the prototype wrote."""

from pathlib import Path

import pytest
import yaml

from policy_advisor.api import credentials

FIXTURES = Path(__file__).parent / "fixtures"
LEGACY_PASSWORD = "legacy-pass-123"


def test_unknown_user_does_not_verify(fresh_db):
    assert credentials.verify_credentials("nobody", "anything") is None
    assert credentials.get_user("nobody") is None


def test_create_then_verify_and_reject_a_wrong_password(fresh_db):
    created = credentials.create_user("jdoe", "Jane Doe", "s3cret-pass", rounds=4)
    assert created.display_name == "Jane Doe"
    record = credentials.verify_credentials("jdoe", "s3cret-pass")
    assert record is not None and record.display_name == "Jane Doe"
    assert credentials.verify_credentials("jdoe", "wrong") is None
    assert credentials.get_user("jdoe") == record
    assert credentials.list_usernames() == ["jdoe"]


def test_duplicate_username_is_rejected_without_overwriting_the_hash(fresh_db):
    credentials.create_user("jdoe", "Jane Doe", "first-pass", rounds=4)
    with pytest.raises(credentials.UserExists):
        credentials.create_user("jdoe", "Impostor", "second-pass", rounds=4)
    assert credentials.verify_credentials("jdoe", "first-pass") is not None
    assert credentials.verify_credentials("jdoe", "second-pass") is None


def test_empty_password_is_rejected(fresh_db):
    with pytest.raises(ValueError):
        credentials.create_user("jdoe", "Jane Doe", "", rounds=4)


def test_legacy_streamlit_authenticator_hash_still_verifies(fresh_db):
    # docs/20 AC-3 carried forward: an account created by the old UI, imported
    # with its hash untouched, must still log in.
    entry = yaml.safe_load((FIXTURES / "legacy_credentials.yaml").read_text())["usernames"][
        "legacy"
    ]
    assert credentials.import_user("legacy", entry["name"], entry["password"], entry["email"])
    record = credentials.verify_credentials("legacy", LEGACY_PASSWORD)
    assert record is not None and record.display_name == "Legacy Lawyer"
    assert credentials.verify_credentials("legacy", LEGACY_PASSWORD + "x") is None
    assert credentials.import_user("legacy", "Someone Else", "$2b$12$notahash", "") is False

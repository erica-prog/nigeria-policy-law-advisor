"""The one-off import of credentials.yaml and user_keys.json into the
database (scripts/migrate_auth_to_postgres.py)."""

import json

import yaml

from policy_advisor.api import credentials, user_keys
from scripts.migrate_auth_to_postgres import migrate

LEGACY_HASH = "$2b$12$j3et6VsRJAzTVcKZOvKjouGCWoVMfvJMYPmJcmb9f.3LrHDYdjAjO"  # "legacy-pass-123"


def _legacy_files(auth_dir):
    auth_dir.mkdir(parents=True)
    (auth_dir / "credentials.yaml").write_text(
        yaml.safe_dump(
            {
                "usernames": {
                    "legacy": {"name": "Legacy Lawyer", "email": "", "password": LEGACY_HASH},
                    "broken": {"name": "No Hash", "email": "", "password": ""},
                }
            }
        )
    )
    (auth_dir / "user_keys.json").write_text(
        json.dumps(
            {
                "version": 1,
                "users": {
                    "legacy": {
                        "token": "gAAAAAnot-a-real-token",
                        "last4": "wxyz",
                        "updated_at": "2026-10-01T00:00:00+00:00",
                    },
                    "ghost": {"token": "gAAAAAorphan", "last4": "0000", "updated_at": ""},
                },
            }
        )
    )


def test_dry_run_changes_nothing(fresh_db, tmp_path):
    _legacy_files(tmp_path / "auth")
    assert migrate(tmp_path / "auth", dry_run=True) == (0, 0)
    assert credentials.list_usernames() == []


def test_import_brings_accounts_and_keys_and_skips_orphans(fresh_db, tmp_path):
    _legacy_files(tmp_path / "auth")
    assert migrate(tmp_path / "auth", dry_run=False) == (1, 1)
    assert credentials.verify_credentials("legacy", "legacy-pass-123") is not None
    assert credentials.get_user("broken") is None  # no hash, not imported
    info = user_keys.get_user_key_info("legacy")
    assert info is not None and info.last4 == "wxyz"
    assert user_keys.get_user_key_info("ghost") is None  # no account, no key
    # Re-running imports nothing new.
    assert migrate(tmp_path / "auth", dry_run=False) == (0, 0)


def test_missing_files_import_nothing(fresh_db, tmp_path):
    assert migrate(tmp_path / "absent", dry_run=False) == (0, 0)

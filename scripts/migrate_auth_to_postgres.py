"""One-off: import the prototype's file-backed accounts and Claude keys into
the database (migration 0005). Reads data/auth/credentials.yaml (bcrypt
hashes) and data/auth/user_keys.json (Fernet tokens, which stay encrypted
under the same AUTH_COOKIE_KEY) and inserts what is not already there.

Safe to re-run: existing usernames are left alone.

Usage:
  uv run python -m scripts.migrate_auth_to_postgres --dry-run
  uv run python -m scripts.migrate_auth_to_postgres
"""

import argparse
import json
from pathlib import Path

import yaml

from policy_advisor import config
from policy_advisor import tenancy
from policy_advisor.api.credentials import import_user
from policy_advisor.api.user_keys import import_user_key


def legacy_accounts(auth_dir: Path) -> dict[str, dict]:
    path = auth_dir / "credentials.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        u: e
        for u, e in (data.get("usernames") or {}).items()
        if isinstance(e, dict) and e.get("password")
    }


def legacy_keys(auth_dir: Path) -> dict[str, dict]:
    path = auth_dir / "user_keys.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        u: e for u, e in (data.get("users") or {}).items() if isinstance(e, dict) and e.get("token")
    }


def migrate(auth_dir: Path, dry_run: bool) -> tuple[int, int]:
    accounts, keys = legacy_accounts(auth_dir), legacy_keys(auth_dir)
    users_added = keys_added = 0
    for username, entry in sorted(accounts.items()):
        print(f"user {username}: {'would import' if dry_run else 'importing'}")
        if not dry_run and import_user(
            username,
            str(entry.get("name") or username),
            str(entry["password"]),
            str(entry.get("email") or ""),
        ):
            users_added += 1
            tenancy.add_user_to_tenant(username)  # the default tenant, as a lawyer (docs/31)
    for username, entry in sorted(keys.items()):
        if username not in accounts:
            print(f"key for {username}: skipped, no account in credentials.yaml")
            continue
        print(
            f"key for {username}: {'would import' if dry_run else 'importing'} (last4 {entry.get('last4', '')})"
        )
        if not dry_run and import_user_key(
            username,
            str(entry["token"]),
            str(entry.get("last4", "")),
            str(entry.get("updated_at") or ""),
        ):
            keys_added += 1
    return users_added, keys_added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    users, keys = migrate(config.AUTH_DIR, args.dry_run)
    print(f"{'would add' if args.dry_run else 'added'}: {users} users, {keys} keys")


if __name__ == "__main__":
    main()

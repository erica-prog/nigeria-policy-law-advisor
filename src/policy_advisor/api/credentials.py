"""The lawyer credential store: data/auth/credentials.yaml, bcrypt password
hashes, one entry per username. This module is the only owner of the file.
`scripts/add_user.py` writes accounts through `save_credentials`; the web API
verifies logins through `verify_credentials`.

The on-disk format is unchanged from the original prototype
(`usernames: {<username>: {name, password, email}}`), and the hashes are plain
bcrypt, so accounts created before this module owned the file keep working."""

import os
from dataclasses import dataclass
from pathlib import Path

import bcrypt
import yaml
from filelock import FileLock

from policy_advisor import config

# Used when the username is unknown so a failed login costs the same time either way.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(rounds=12))


@dataclass(frozen=True)
class UserRecord:
    username: str
    display_name: str


def credentials_path() -> Path:
    return config.AUTH_DIR / "credentials.yaml"


def _lock_path() -> Path:
    return config.AUTH_DIR / "credentials.yaml.lock"


def load_credentials() -> dict:
    path = credentials_path()
    if not path.exists():
        return {"usernames": {}}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {"usernames": {}}


def hash_password(password: str) -> str:
    """bcrypt, cost 12 - the same scheme every stored hash already uses."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def save_credentials(credentials: dict) -> None:
    """Merge `credentials["usernames"]` into the file.

    Read-modify-write under a file lock, then an atomic replace, so two
    concurrent account creations cannot clobber each other and a reader never
    sees a half-written file."""
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(_lock_path())):
        on_disk = load_credentials()
        on_disk.setdefault("usernames", {}).update(credentials.get("usernames", {}))
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(yaml.safe_dump(on_disk), encoding="utf-8")
        os.replace(tmp, path)


def get_user(username: str) -> UserRecord | None:
    entry = load_credentials().get("usernames", {}).get(username)
    if not entry:
        return None
    return UserRecord(username=username, display_name=entry.get("name") or username)


def verify_credentials(username: str, password: str) -> UserRecord | None:
    entry = load_credentials().get("usernames", {}).get(username)
    hashed = (entry or {}).get("password") if isinstance(entry, dict) else None
    if not hashed:
        bcrypt.checkpw(password.encode(), _DUMMY_HASH)
        return None
    try:
        ok = bcrypt.checkpw(password.encode(), str(hashed).encode())
    except ValueError:
        return None
    if not ok:
        return None
    return UserRecord(username=username, display_name=entry.get("name") or username)

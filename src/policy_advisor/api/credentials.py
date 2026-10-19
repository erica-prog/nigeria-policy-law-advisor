"""Password verification against the prototype's credential store: the same
data/auth/credentials.yaml that scripts/add_user.py and the Streamlit sign-up
tab write (bcrypt hashes via streamlit_authenticator.Hasher). Read-only here;
the file format and location are owned by policy_advisor.auth. Reimplemented
as a few lines rather than imported because auth.py pulls in Streamlit."""

from dataclasses import dataclass
from pathlib import Path

import bcrypt
import yaml

from policy_advisor import config

# Used when the username is unknown so a failed login costs the same time either way.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(rounds=12))


@dataclass(frozen=True)
class UserRecord:
    username: str
    display_name: str


def credentials_path() -> Path:
    return config.AUTH_DIR / "credentials.yaml"


def load_credentials() -> dict:
    path = credentials_path()
    if not path.exists():
        return {"usernames": {}}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {"usernames": {}}


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

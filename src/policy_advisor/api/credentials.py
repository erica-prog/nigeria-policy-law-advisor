"""Lawyer accounts: the `users` table, bcrypt password hashes, one row per
username. This module is the only owner of accounts. `scripts/add_user.py`
creates them through `create_user`; the web API verifies logins through
`verify_credentials`; `scripts/migrate_auth_to_postgres.py` imports the
prototype's credentials.yaml once.

The hashes are plain bcrypt, so accounts created by the prototype's UI keep
working after the import."""

from dataclasses import dataclass

import bcrypt

from policy_advisor.db import connect
from policy_advisor.tenancy import Role

# Used when the username is unknown so a failed login costs the same time either way.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(rounds=12))


class UserExists(ValueError):
    pass


@dataclass(frozen=True)
class UserRecord:
    username: str
    display_name: str
    # Filled by the request dependency from tenant_users (docs/31); None for a
    # record straight from the credentials table.
    tenant_id: str | None = None
    role: Role | None = None


def hash_password(password: str, rounds: int = 12) -> str:
    """bcrypt, cost 12 by default - the scheme every stored hash uses."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def create_user(
    username: str, display_name: str, password: str, email: str = "", *, rounds: int = 12
) -> UserRecord:
    """Add one account. Raises `UserExists` rather than overwriting a hash."""
    if not password:
        raise ValueError("Password cannot be empty.")
    with connect() as conn:
        inserted = conn.execute(
            "INSERT INTO users (username, display_name, email, password_hash) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (username) DO NOTHING",
            (username, display_name, email or None, hash_password(password, rounds)),
        ).rowcount
    if not inserted:
        raise UserExists(f"User '{username}' already exists.")
    return UserRecord(username=username, display_name=display_name)


def import_user(username: str, display_name: str, password_hash: str, email: str = "") -> bool:
    """Insert an account with an existing bcrypt hash (the credentials.yaml
    import). Returns False when the username is already taken."""
    with connect() as conn:
        return bool(
            conn.execute(
                "INSERT INTO users (username, display_name, email, password_hash) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (username) DO NOTHING",
                (username, display_name or username, email or None, password_hash),
            ).rowcount
        )


def list_usernames() -> list[str]:
    with connect() as conn:
        return [
            row["username"] for row in conn.execute("SELECT username FROM users ORDER BY username")
        ]


def _row(username: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT username, display_name, password_hash FROM users WHERE username = %s",
            (username,),
        ).fetchone()
    return dict(row) if row else None


def get_user(username: str) -> UserRecord | None:
    row = _row(username)
    if row is None:
        return None
    return UserRecord(username=username, display_name=row["display_name"] or username)


def verify_credentials(username: str, password: str) -> UserRecord | None:
    row = _row(username)
    if row is None or not row.get("password_hash"):
        bcrypt.checkpw(password.encode(), _DUMMY_HASH)
        return None
    try:
        ok = bcrypt.checkpw(password.encode(), str(row["password_hash"]).encode())
    except ValueError:
        return None
    if not ok:
        return None
    return UserRecord(username=username, display_name=row["display_name"] or username)

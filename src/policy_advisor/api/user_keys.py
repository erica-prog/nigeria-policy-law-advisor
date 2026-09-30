"""Bring-your-own Claude key (docs/17, "BYOK"). Each web user stores their own
Anthropic API key; the server's ANTHROPIC_API_KEY is never spent on a user's
questions unless ALLOW_SHARED_ANTHROPIC_KEY is on.

Storage: `user_keys.json` next to `credentials.yaml` (config.AUTH_DIR), one
Fernet token per user. The Fernet key is derived from AUTH_COOKIE_KEY with
HKDF-SHA256, so the file alone is useless and the plaintext key is never on
disk. Writes go through a file lock. Nothing here logs a key value; callers
get `last4` for display and `resolve_anthropic_key()` for the call itself.

A user key is a user secret and gets the same care as the server key
(AGENTS.md rule 2): it never appears in a response, log line or error."""

import base64
import hashlib
import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import anthropic
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from filelock import FileLock

from policy_advisor import config
from policy_advisor.api.errors import ApiError
from policy_advisor.config import get_settings
from policy_advisor.logging_utils import get_logger, log_event

logger = get_logger(__name__)

KeySource = Literal["user", "shared"]

_HKDF_INFO = b"policy-advisor/user-claude-keys/v1"
_KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,300}$")
_FILE_VERSION = 1

# PUT attempts per user per minute: enough for a typo or two, too few to use
# the endpoint as an oracle for guessing keys.
RATE_LIMIT_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 60.0

INVALID_FORMAT_MESSAGE = (
    "That does not look like an Anthropic API key. It starts with sk-ant- and has no spaces; "
    "copy it again from console.anthropic.com."
)
INVALID_KEY_MESSAGE = (
    "Anthropic did not accept this key. Check that you copied the whole key and that it is "
    "still active under API Keys in your Anthropic console."
)
UNREACHABLE_MESSAGE = (
    "I could not reach Anthropic to check the key. Try again in a moment; nothing was saved."
)
RATE_LIMITED_MESSAGE = "Too many attempts. Wait a minute and try again."
SERVER_NOT_CONFIGURED_MESSAGE = (
    "This server cannot store keys safely yet: the administrator must set AUTH_COOKIE_KEY."
)


@dataclass(frozen=True)
class StoredKeyInfo:
    last4: str
    updated_at: str


def user_keys_path() -> Path:
    # Resolved on every call, like credentials_path(), so tests that relocate
    # config.AUTH_DIR are honoured.
    return config.AUTH_DIR / "user_keys.json"


def _fernet() -> Fernet:
    settings = get_settings()
    if settings.auth_cookie_key_is_insecure_default():
        raise ApiError(503, "server_not_configured", SERVER_NOT_CONFIGURED_MESSAGE)
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(
        settings.auth_cookie_key.get_secret_value().encode("utf-8")
    )
    return Fernet(base64.urlsafe_b64encode(derived))


def _read_file(path: Path) -> dict:
    if not path.exists():
        return {"version": _FILE_VERSION, "users": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": _FILE_VERSION, "users": {}}
    if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
        return {"version": _FILE_VERSION, "users": {}}
    return data


def _write_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _lock(path: Path) -> FileLock:
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path) + ".lock")


def key_fingerprint(api_key: str) -> str:
    """Stable, non-reversible handle for caching per-key resources."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:32]


# ---- read ----


def get_user_key_info(username: str) -> StoredKeyInfo | None:
    entry = _read_file(user_keys_path())["users"].get(username)
    if not isinstance(entry, dict) or "token" not in entry:
        return None
    return StoredKeyInfo(
        last4=str(entry.get("last4", "")), updated_at=str(entry.get("updated_at", ""))
    )


def get_user_key(username: str) -> str | None:
    """Decrypted key for `username`, or None. Never log the result."""
    entry = _read_file(user_keys_path())["users"].get(username)
    if not isinstance(entry, dict) or "token" not in entry:
        return None
    try:
        return _fernet().decrypt(str(entry["token"]).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, ApiError):
        # Wrong AUTH_COOKIE_KEY (rotated) or corrupt token: behave as "no key"
        # rather than failing every request; the user can store it again.
        return None


def key_source_for(username: str) -> KeySource | None:
    if get_user_key_info(username) is not None:
        return "user"
    settings = get_settings()
    if settings.allow_shared_anthropic_key and settings.llm_configured():
        return "shared"
    return None


def resolve_anthropic_key(username: str) -> str | None:
    """The key to think with on behalf of `username`: their own key, else the
    server key only when ALLOW_SHARED_ANTHROPIC_KEY is on, else None."""
    own = get_user_key(username)
    if own:
        return own
    settings = get_settings()
    if settings.allow_shared_anthropic_key and settings.llm_configured():
        return settings.anthropic_api_key.get_secret_value()
    return None


# ---- write ----


def validate_key_format(api_key: str) -> str:
    candidate = api_key.strip()
    if not candidate or any(ch.isspace() for ch in candidate) or not _KEY_RE.match(candidate):
        raise ApiError(400, "invalid_api_key", INVALID_FORMAT_MESSAGE)
    return candidate


def verify_key_with_anthropic(api_key: str) -> None:
    """Cheapest real call that needs authentication: list one model. Errors are
    mapped to our envelope; the key never appears in the message."""
    client = anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=15.0)
    try:
        client.models.list(limit=1)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise ApiError(400, "invalid_api_key", INVALID_KEY_MESSAGE) from exc
    except anthropic.RateLimitError:
        # 429 means Anthropic authenticated the key and then throttled it; the
        # key itself is fine.
        return
    except (anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
        # The browser only gets the generic message, so the operator needs the
        # real cause here. Neither the class, the status nor the root cause
        # carries the key.
        root = exc.__cause__ or exc.__context__
        log_event(
            logger,
            "claude_key_check_failed",
            error=type(exc).__name__,
            status=getattr(exc, "status_code", None),
            cause=f"{type(root).__name__}: {root}"[:300] if root else None,
        )
        raise ApiError(502, "anthropic_unreachable", UNREACHABLE_MESSAGE) from exc


def store_user_key(username: str, api_key: str) -> StoredKeyInfo:
    fernet = _fernet()  # refuses on the insecure default before anything is written
    path = user_keys_path()
    info = StoredKeyInfo(
        last4=api_key[-4:], updated_at=datetime.now(UTC).replace(microsecond=0).isoformat()
    )
    with _lock(path):
        data = _read_file(path)
        data["users"][username] = {
            "token": fernet.encrypt(api_key.encode("utf-8")).decode("ascii"),
            "last4": info.last4,
            "updated_at": info.updated_at,
        }
        _write_file(path, data)
    return info


def delete_user_key(username: str) -> bool:
    path = user_keys_path()
    with _lock(path):
        data = _read_file(path)
        removed = data["users"].pop(username, None) is not None
        if removed:
            _write_file(path, data)
    return removed


# ---- rate limit (in memory, per process) ----


class AttemptLimiter:
    def __init__(
        self, attempts: int = RATE_LIMIT_ATTEMPTS, window: float = RATE_LIMIT_WINDOW_SECONDS
    ):
        self._attempts = attempts
        self._window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, username: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[username]
            while hits and now - hits[0] > self._window:
                hits.popleft()
            if len(hits) >= self._attempts:
                raise ApiError(429, "rate_limited", RATE_LIMITED_MESSAGE)
            hits.append(now)

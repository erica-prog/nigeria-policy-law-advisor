"""Per-lawyer login (CLAUDE-2.md cross-cutting concern: multi-tenancy access
control - flagged early as a requirement once documents are user-uploaded,
not a nice-to-have). Wraps streamlit_authenticator so app.py just calls
require_login() and gets back an identity or the page stops rendering.

Accounts can come from two paths: admin-provisioned via scripts/add_user.py,
or self-registered in the UI's "Sign up" tab. Self-registration has no
employer-domain allowlist - any syntactically valid .com email is accepted,
gated only by the library's built-in captcha (see docs/10-self-service-signup.md
for the trade-off this overrides: accounts were previously admin-only by
deliberate choice)."""

import re

import yaml
import streamlit as st
import streamlit_authenticator as stauth
from filelock import FileLock

from policy_advisor.config import AUTH_DIR, get_settings

CREDENTIALS_PATH = AUTH_DIR / "credentials.yaml"
CREDENTIALS_LOCK_PATH = AUTH_DIR / "credentials.yaml.lock"

# streamlit_authenticator's own `domains` allowlist only does exact-string
# matching against a fixed list - it can't express "any domain, but only
# .com" - so that rule is enforced here instead, after the widget succeeds.
_COM_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.com$", re.IGNORECASE)


def load_credentials() -> dict:
    if not CREDENTIALS_PATH.exists():
        return {"usernames": {}}
    return yaml.safe_load(CREDENTIALS_PATH.read_text(encoding="utf-8")) or {"usernames": {}}


def save_credentials(credentials: dict) -> None:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    # Guards against two concurrent registrations both doing a
    # read-modify-write on the same file and one silently clobbering the
    # other's new account.
    with FileLock(str(CREDENTIALS_LOCK_PATH)):
        on_disk = load_credentials()
        on_disk["usernames"].update(credentials["usernames"])
        CREDENTIALS_PATH.write_text(yaml.safe_dump(on_disk), encoding="utf-8")


def get_authenticator(credentials: dict) -> stauth.Authenticate:
    settings = get_settings()
    return stauth.Authenticate(
        credentials,
        cookie_name="policy_advisor_auth",
        cookie_key=settings.auth_cookie_key.get_secret_value(),
        cookie_expiry_days=7,
        # scripts/add_user.py always stores pre-hashed (bcrypt) passwords -
        # no reliance on the library's own plaintext-detection/auto-hash path.
        auto_hash=False,
    )


def _render_sign_up_tab(authenticator: stauth.Authenticate, credentials: dict) -> None:
    try:
        email, username, _name = authenticator.register_user(location="main", captcha=True)
    except stauth.RegisterError as exc:
        st.error(str(exc))
        return

    if not username:
        return  # form not submitted yet this run

    if email and _COM_EMAIL_RE.match(email):
        # register_user already wrote the new entry into this same
        # in-memory `credentials` dict (shared by reference with the
        # Authenticate instance) - persist it the same way add_user.py does.
        save_credentials(credentials)
        st.success("Account created. Switch to the Log in tab to sign in.")
    else:
        # Roll back: remove the entry register_user just added so a
        # non-.com email is never persisted to disk.
        credentials["usernames"].pop(username, None)
        st.error("Only .com email addresses can self-register.")


def require_login() -> tuple[str, str]:
    """Renders login/sign-up if not already authenticated and halts the
    rest of the page until login succeeds. Returns (username, display_name)
    once it does."""
    credentials = load_credentials()
    authenticator = get_authenticator(credentials)

    tab_login, tab_register = st.tabs(["Log in", "Sign up"])
    with tab_login:
        authenticator.login(location="main")
    with tab_register:
        _render_sign_up_tab(authenticator, credentials)

    status = st.session_state.get("authentication_status")
    if status is False:
        st.error("Username or password is incorrect.")
        st.stop()
    if status is None:
        st.stop()

    username = st.session_state["username"]
    display_name = st.session_state.get("name") or username

    with st.sidebar:
        authenticator.logout("Log out", location="sidebar")

    return username, display_name

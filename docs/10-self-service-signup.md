# 10 — Adding Self-Service Signup to the Streamlit UI

> **Superseded (2026-10-24).** The Streamlit app was removed; the browser client in
> `web/`, served by FastAPI, is the only surface. See [17-web-app-mvp.md](17-web-app-mvp.md).
> Kept for the reasoning it records.

## This reverses a documented decision — read this first
[auth.py](../src/policy_advisor/auth.py)'s own docstring states accounts
are "admin-provisioned only... no self-service sign-up, by deliberate
choice," tied to the multi-tenancy concern in CLAUDE-2.md: once documents
are user-uploaded, *who can even get an identity in the system* is part of
the access-control story. That rationale was about controlling who can
identify as a user of the tool at all — not directly about document
isolation, since matter-scoping (`set_matter_owner`, the per-matter sidebar
picker in `app.py`) already isolates documents by matter regardless of how
the account was created. So opening signup doesn't reintroduce the
matter-isolation bug the original design was guarding against — but it
does remove the one identity check that currently exists: a human admin
deciding who gets an account before they ever see the product. For a tool
giving legal-research output, that's a real trade-off (see "what you give
up" below), not a formality — go in with that decided, not assumed.

## What you get
- Lawyers can start using the tool without waiting on you to run
  `scripts/add_user.py` for each one.
- Removes the only manual, human-in-the-loop step in onboarding.

## What you give up
- **No identity verification.** Right now, every account corresponds to a
  person you (the admin) deliberately vetted. Self-service signup means
  any username/password pair gets an account — relevant for a legal tool
  where you may want to know who is actually using it.
- **Abuse surface.** A public registration form is a target for automated
  signups regardless of intent — needs the captcha `register_user` already
  supports (see below) at minimum, ideally rate-limiting too.
- **Org boundary becomes self-declared.** With admin provisioning, *you*
  decide who's a legitimate user of your firm/org. With self-signup, that
  boundary either disappears (anyone can register) or has to be re-built
  via email-domain allowlisting (`domains=` param below).

If the goal is "save myself from running a CLI command for every new
hire," consider whether an **invite-link flow** (admin generates a one-time
token, new user redeems it to set their own password) gets you the
convenience without fully opening the door. That's a middle ground worth
considering before going straight to open registration.

## Implementation: streamlit_authenticator already has this built in
The installed version (`streamlit-authenticator>=0.4.2`) ships
`Authenticate.register_user(...)` — you're wiring up an existing library
feature, not building registration from scratch. Signature on this
install:

```python
def register_user(
    self,
    location: Literal['main', 'sidebar'] = 'main',
    pre_authorized: Optional[List[str]] = None,
    domains: Optional[List[str]] = None,
    fields: Optional[Dict[str, str]] = None,
    captcha: bool = True,
    roles: Optional[List[str]] = None,
    merge_username_email: bool = False,
    password_hint: bool = True,
    two_factor_auth: bool = False,
    clear_on_submit: bool = False,
    key: str = 'Register user',
    callback: Optional[Callable] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]
```

The parameters that matter most for *this* project, given the trade-offs
above:
- **`captcha=True`** (the default) — keep it on. Don't disable this for a
  public-facing legal tool.
- **`domains=["yourfirm.com"]`** — if there's a known set of email domains
  that should be allowed to self-register (e.g. only people at your firm,
  or a fixed list of partner firms), this re-introduces an org boundary
  without falling back to fully manual provisioning. Worth using even if
  you do open registration.
- **`pre_authorized`** — a list of specific emails allowed to register;
  this is the closest built-in equivalent to an invite list if you want
  the middle-ground flow instead of fully open signup.

### Where it plugs into the existing code
`auth.py`'s `require_login()` currently only calls `authenticator.login(...)`.
The shape of the change:

```python
# auth.py — illustrative, not a finished implementation
def require_login() -> tuple[str, str]:
    authenticator = get_authenticator()

    tab_login, tab_register = st.tabs(["Log in", "Sign up"])
    with tab_login:
        authenticator.login(location="main")
    with tab_register:
        email, username, name = authenticator.register_user(
            location="main",
            domains=["yourfirm.com"],   # decide this value deliberately
            captcha=True,
        )
        if username:
            # register_user already writes the new entry into the
            # credentials dict passed into Authenticate() — persist it
            # the same way add_user.py does today:
            AUTH_DIR.mkdir(parents=True, exist_ok=True)
            CREDENTIALS_PATH.write_text(
                yaml.safe_dump(load_credentials()), encoding="utf-8"
            )
            st.success(f"Account created for {name}. You can log in now.")

    status = st.session_state.get("authentication_status")
    ...  # rest unchanged
```

Things to get right that aren't obvious from the library docs:
1. **Persist after registration.** `register_user` mutates the in-memory
   credentials dict you passed to `Authenticate(...)`, but `auth.py`
   currently builds that dict fresh from disk on every call via
   `get_authenticator()` → `load_credentials()`. You must explicitly
   re-serialize and write `CREDENTIALS_PATH` after a successful
   registration, the same way `scripts/add_user.py` does, or the new
   account vanishes on the next process restart.
2. **Concurrent writes.** Streamlit can run multiple sessions against the
   same process; two simultaneous registrations both reading-then-writing
   `credentials.yaml` is a lost-update race. At minimum, take a file lock
   around read-modify-write here (`filelock` package, or move credentials
   to a real datastore — see point 4).
3. **New users need a matter.** Today, `app.py`'s `list_matters_for_user`
   assumes a user already has a matter assigned (admin-provisioned users
   presumably got one set up alongside their account). A self-registered
   user has none — decide whether they get an auto-created empty matter on
   first login, or land on an explicit "create your first matter" screen.
4. **YAML-on-disk won't survive this for long.** A flat-file credential
   store was fine for admin-provisioned, low-volume accounts. Once
   registration is self-service, move to a real table (SQLite at minimum,
   Postgres if you're already running one) — both for the concurrency
   problem in point 2 and because you'll want to query "who signed up
   when" for abuse review.

## Before shipping this
- Decide the `domains`/`pre_authorized` boundary explicitly — don't ship
  fully open registration without that conversation having happened.
- Add the new-user-matter-bootstrap behavior from point 3 above; an
  account with no matter is a broken first-run experience.
- Add a CI test asserting two concurrent registrations with the same
  username don't both succeed (race condition from point 2).

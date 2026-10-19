---
name: verify-policy-advisor
description: >-
  Drive the Law & Policy Advisor web app (the browser client and the /api
  routes it calls) on an isolated local server. Use when proving login,
  cases, logout, uploads, or any other user-visible behavior of the web MVP.
---

# Verify the Law & Policy Advisor web app

The primary surface is the browser client at `/` (`web/index.html`). It talks only to the JSON API under `/api`. A Streamlit prototype also exists on port 8501; this skill does not drive it.

The scripted harness is `pa-verify.py` plus `curl`. The page and the curl commands call the same routes. There is no Playwright suite in this repo.

## Launch

From the repository root:

```bash
uv run python .cursor/skills/verify-policy-advisor/pa-verify.py launch
```

Ready is two lines you can check, not just an open port:

- `GET /api/health` returns `200` and `{"status":"ok",...}`.
- `GET /` returns `200` and the HTML contains `id="login-form"`.

Launch prints `url=http://127.0.0.1:8765`, `data=<path>`, `pid=<pid>`, and `pa-verify ready`. The data directory is `.cursor/skills/verify-policy-advisor/run/data`, created empty except for one seeded account. It is not the repository `data/` tree. The server listens only on `127.0.0.1:8765`.

Seeded account, in that disposable directory only:

- username `verify`
- password `verify-pass-123`
- display name `Verify User`

The process is started with `start_new_session`, `AUTH_COOKIE_KEY` set to a verification-only value, `TRANSLATE_ON_INGEST=false`, and `ALLOW_SHARED_ANTHROPIC_KEY=false`. It does not use `--reload`, because a reload would import the app again and bind the real `data/` paths.

Do not start a second copy. If launch says one is already running, run doctor or cleanup. If port 8765 is taken by any other process, launch exits and does not kill it. Never point this harness at port 8000 or at `data/`.

## Doctor

```bash
uv run python .cursor/skills/verify-policy-advisor/pa-verify.py doctor
```

Run this first whenever a request fails. It checks that the recorded pid is alive, that the process command is `pa-verify.py serve`, that this pid owns port 8765, that the data path is the disposable directory, that `/api/health` is ok, and that the login form is being served. Exit 0 prints `pa-verify doctor ok`.

## Drive

Read `.cursor/skills/verify-policy-advisor/features/README.md` and the feature file you are proving. Use the curl commands in that file. Keep the cookie jar at `.cursor/skills/verify-policy-advisor/run/cookies.txt`.

Stable handles in the page, when a browser is available: the login form is `#login-form`, the submit button is named `Let's go`, the username and password inputs are named `Username` and `Password`, the error is `#login-error`, the cases button is `#drawer-toggle` (`Your cases`), the new-case button is `#new-case` (`+ Start a new case`), and logout is `#logout-btn` (`Log out`).

A curl proof is enough when it hits the same route the button hits and a second read shows the stored result. A screenshot of the final screen is not enough on its own.

Do not call `/ask`, `/analyze`, or `/chat` in a verification run. Those routes can spend the Anthropic key loaded from the repository `.env`. An empty `ANTHROPIC_API_KEY` in the environment does not override a value in `.env`.

## Evidence

Write proof under `.cursor/skills/verify-policy-advisor/evidence/<feature-id>/`. Cleanup must not delete that directory.

For each proof keep:

- the request (method, path, body with no passwords in the saved file when you can avoid it) and the response status and body
- a second read of the resulting state: `GET /api/me` or `GET /api/session` after login, `GET /api/matters/{id}` plus `meta.json` after creating a case
- the feature id and the entry point (`browser` button name, or `POST /api/...`)

`meta.json` for a case lives at `<data>/index/matters/<id>/meta.json`.

## Cleanup

```bash
uv run python .cursor/skills/verify-policy-advisor/pa-verify.py cleanup
```

This sends `SIGTERM` to the process group recorded at launch, and `SIGKILL` only if that same pid is still alive. It deletes `run/data`, `run/state.json`, and `run/server.log`. It does not delete `evidence/`. It will not kill a pid whose command line is not `pa-verify.py serve`, and it will not delete the repository `data/` tree.

## Helpers

`pa-verify.py` is the only helper. Invoke it with `uv run python` as shown above. `serve` is the child process launch starts; do not run `serve` yourself.

#!/usr/bin/env python3
"""Isolated server for verifying the Law & Policy Advisor web app.

The repo's data directories are fixed at import time, and port 8000 is often
already a developer's server. This script patches those directories before
the app imports them, listens on 127.0.0.1:8765, and refuses any other port
or the repository data/ tree.

    uv run python .claude/skills/verify-policy-advisor/pa-verify.py launch
    uv run python .claude/skills/verify-policy-advisor/pa-verify.py doctor
    uv run python .claude/skills/verify-policy-advisor/pa-verify.py cleanup
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
REPO = SKILL_DIR.parents[2]
RUN_DIR = SKILL_DIR / "run"
DATA_DIR = RUN_DIR / "data"
STATE_PATH = RUN_DIR / "state.json"
LOG_PATH = RUN_DIR / "server.log"
PORT = 8765
HOST = "127.0.0.1"
URL = f"http://{HOST}:{PORT}"
VERIFY_USER = "verify"
VERIFY_PASSWORD = "verify-pass-123"
VERIFY_NAME = "Verify User"
COOKIE_KEY = "verify-session-key-not-the-insecure-default"

_REPO_DATA = (REPO / "data").resolve()


def _die(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _command(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _ours(pid: int) -> bool:
    command = _command(pid)
    return "pa-verify.py" in command and " serve" in f" {command} "


def _listeners(port: int) -> list[int]:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        check=False,
        capture_output=True,
        text=True,
    )
    pids = []
    for line in result.stdout.split():
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def _read_state() -> dict | None:
    if not STATE_PATH.exists():
        return None
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _write_credentials(auth_dir: Path) -> None:
    import bcrypt
    import yaml

    auth_dir.mkdir(parents=True, exist_ok=True)
    password = bcrypt.hashpw(VERIFY_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    payload = {
        "usernames": {
            VERIFY_USER: {"name": VERIFY_NAME, "email": "", "password": password},
        }
    }
    (auth_dir / "credentials.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")


def _get(path: str, timeout: float = 2) -> tuple[int, bytes]:
    request = urllib.request.Request(URL + path)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def launch() -> None:
    if DATA_DIR.resolve() == _REPO_DATA or _REPO_DATA in DATA_DIR.resolve().parents:
        _die(f"refusing to use the repository data directory {DATA_DIR}")
    state = _read_state()
    if state and _pid_alive(int(state["pid"])):
        _die(f"already running pid={state['pid']} url={state['url']}; run cleanup first")
    listeners = _listeners(PORT)
    if listeners:
        _die(
            f"port {PORT} is in use by pid {listeners}; not killing it. "
            "Stop that process yourself, or leave the developer's server on port 8000 alone."
        )
    if RUN_DIR.exists():
        subprocess.run(["rm", "-rf", str(DATA_DIR)], check=True)
    (DATA_DIR / "auth").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "index" / "matters").mkdir(parents=True, exist_ok=True)
    _write_credentials(DATA_DIR / "auth")
    log = LOG_PATH.open("w", encoding="utf-8")
    env = os.environ.copy()
    env["AUTH_COOKIE_KEY"] = COOKIE_KEY
    env["PA_VERIFY_DATA"] = str(DATA_DIR)
    env["PA_VERIFY_PORT"] = str(PORT)
    env["TRANSLATE_ON_INGEST"] = "false"
    env["ALLOW_SHARED_ANTHROPIC_KEY"] = "false"
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "serve"],
        cwd=REPO,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    STATE_PATH.write_text(
        json.dumps(
            {
                "pid": process.pid,
                "pgid": os.getpgid(process.pid),
                "port": PORT,
                "url": URL,
                "data": str(DATA_DIR),
                "log": str(LOG_PATH),
            }
        ),
        encoding="utf-8",
    )
    deadline = time.monotonic() + 60
    last = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _die(f"server exited {process.returncode}; see {LOG_PATH}")
        try:
            status, body = _get("/api/health")
            page_status, page = _get("/")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = str(exc)
            time.sleep(0.3)
            continue
        if status == 200 and page_status == 200 and b'id="login-form"' in page:
            print(f"url={URL}")
            print(f"data={DATA_DIR}")
            print(f"pid={process.pid}")
            print("pa-verify ready")
            return
        last = f"health={status} page={page_status} body={body[:120]!r}"
        time.sleep(0.3)
    if process.poll() is None:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    _die(f"server did not become usable within 60s ({last}); see {LOG_PATH}")


def doctor() -> None:
    state = _read_state()
    if not state:
        _die("no verification server state; run launch")
    pid = int(state["pid"])
    data = Path(state["data"]).resolve()
    if data == _REPO_DATA or _REPO_DATA in data.parents:
        _die(f"state points at the repository data directory: {data}")
    if not _pid_alive(pid):
        _die(f"pid {pid} is not running")
    if not _ours(pid):
        _die(f"pid {pid} is not pa-verify.py serve: {_command(pid)}")
    listeners = _listeners(int(state["port"]))
    if pid not in listeners:
        _die(f"pid {pid} is not listening on port {state['port']} (listeners={listeners})")
    status, body = _get("/api/health")
    if status != 200:
        _die(f"/api/health returned {status}: {body[:200]!r}")
    health = json.loads(body)
    if health.get("status") != "ok":
        _die(f"/api/health is not ok: {health}")
    page_status, page = _get("/")
    if page_status != 200 or b'id="login-form"' not in page:
        _die("login page is not being served")
    print(f"url={state['url']}")
    print(f"data={state['data']}")
    print(f"pid={pid}")
    print("pa-verify doctor ok")


def cleanup() -> None:
    state = _read_state()
    if state:
        pid = int(state["pid"])
        if _pid_alive(pid):
            if not _ours(pid):
                _die(f"refusing to kill pid {pid}; it is not pa-verify.py serve")
            pgid = int(state.get("pgid") or pid)
            os.killpg(pgid, signal.SIGTERM)
            for _ in range(30):
                if not _pid_alive(pid):
                    break
                time.sleep(0.1)
            if _pid_alive(pid):
                os.killpg(pgid, signal.SIGKILL)
        print(f"stopped pid={pid}")
    else:
        print("no verification server state")
    if DATA_DIR.exists():
        subprocess.run(["rm", "-rf", str(DATA_DIR)], check=True)
    if STATE_PATH.exists():
        STATE_PATH.unlink()
    if LOG_PATH.exists():
        LOG_PATH.unlink()
    print("pa-verify cleanup ok")
    print(f"evidence kept at {SKILL_DIR / 'evidence'}")


def _patch_and_serve() -> None:
    data = Path(os.environ["PA_VERIFY_DATA"]).resolve()
    port = int(os.environ["PA_VERIFY_PORT"])
    if data == _REPO_DATA or _REPO_DATA in data.parents:
        _die(f"refusing to serve repository data at {data}")
    if port != PORT:
        _die(f"refusing to serve on port {port}")
    os.environ["AUTH_COOKIE_KEY"] = COOKIE_KEY
    import policy_advisor.config as config

    config.DATA_DIR = data
    config.INDEX_DIR = data / "index"
    config.MATTERS_DIR = config.INDEX_DIR / "matters"
    config.AUTH_DIR = data / "auth"
    config.MATTERS_DIR.mkdir(parents=True, exist_ok=True)
    config.AUTH_DIR.mkdir(parents=True, exist_ok=True)

    from policy_advisor.api.main import app
    from policy_advisor.ingestion import matter_store

    if matter_store.MATTERS_DIR != config.MATTERS_DIR:
        _die(
            "matter_store bound MATTERS_DIR before the patch "
            f"({matter_store.MATTERS_DIR} != {config.MATTERS_DIR})"
        )
    import uvicorn

    print(f"pa-verify listening url={URL} data={data}", flush=True)
    uvicorn.run(app, host=HOST, port=port, log_level="info", access_log=False)


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "launch":
        launch()
    elif command == "doctor":
        doctor()
    elif command == "cleanup":
        cleanup()
    elif command == "serve":
        _patch_and_serve()
    else:
        _die("usage: pa-verify.py launch|doctor|cleanup|serve")


if __name__ == "__main__":
    main()

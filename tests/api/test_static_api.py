"""The same app serves the browser client and the avatar masters from
assets/avatar (mounted, not copied)."""

from policy_advisor.api.main import create_app
from fastapi.testclient import TestClient


def test_client_and_avatar_frames_are_served(api):
    client = TestClient(create_app(serve_static=True))

    index = client.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert 'id="advisor-caption" aria-live="polite"' in index.text
    assert "script-src 'self'" in index.headers["content-security-policy"]

    for name in ["idle", "listening", "no-results", "verified-source"]:
        frame = client.get(f"/avatar/advisor-{name}.png")
        assert frame.status_code == 200, name
        assert frame.headers["content-type"] == "image/png"

    assert client.get("/js/avatar.js").status_code == 200
    assert client.get("/avatar/../pyproject.toml").status_code in (400, 404)

from policy_advisor.api.sessions import SESSION_COOKIE


def test_health_is_public_and_reports_model_state(api):
    response = api.client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "llm_configured": False, "shared_key_allowed": False}


def test_login_success_sets_httponly_cookie_and_returns_user(api):
    response = api.client.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-pass-123"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "username": "alice",
        "display_name": "Alice",
        "advisor_ready": False,
        "key_source": None,
    }
    cookie_header = response.headers["set-cookie"].lower()
    assert SESSION_COOKIE in cookie_header
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header


def test_wrong_password_and_unknown_user_get_the_same_error(api):
    wrong = api.client.post("/api/auth/login", json={"username": "alice", "password": "nope"})
    unknown = api.client.post("/api/auth/login", json={"username": "mallory", "password": "x"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"]["code"] == "invalid_credentials"


def test_unauthenticated_requests_are_rejected(api):
    for method, path in [
        ("get", "/api/me"),
        ("get", "/api/matters"),
        ("post", "/api/matters"),
        ("get", "/api/matters/phase1-demo/documents"),
        ("post", "/api/matters/phase1-demo/ask"),
        ("post", "/api/matters/phase1-demo/analyze"),
        ("post", "/api/matters/phase1-demo/chat/jobs"),
        ("get", "/api/matters/phase1-demo/chat/jobs/abc"),
        ("get", "/api/me/claude-key"),
        ("put", "/api/me/claude-key"),
        ("delete", "/api/me/claude-key"),
    ]:
        response = api.client.request(method.upper(), path, json={})
        assert response.status_code == 401, (method, path, response.text)
        assert response.json()["error"]["code"] == "unauthenticated"


def test_tampered_cookie_is_rejected(api):
    api.client.cookies.set(SESSION_COOKIE, "eyJ1IjogImFsaWNlIn0.forged.signature")
    assert api.client.get("/api/me").status_code == 401


def test_me_and_logout_round_trip(api):
    client = api.login("alice")
    assert client.get("/api/me").json()["username"] == "alice"
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/me").status_code == 401

"""The error envelope never leaks exception text, paths or secrets."""

from fastapi import APIRouter

SECRET_MARKER = "sk-ant-super-secret-value"


def test_unhandled_exception_is_masked(api):
    router = APIRouter()

    @router.get("/api/boom")
    def boom():
        raise RuntimeError(f"database at /srv/private failed with key {SECRET_MARKER}")

    api.app.include_router(router)  # type: ignore[attr-defined]
    response = api.client.get("/api/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Something went wrong on the server."}
    }
    assert SECRET_MARKER not in response.text
    assert "/srv/private" not in response.text
    assert "RuntimeError" not in response.text


def test_unknown_route_uses_the_same_envelope(api):
    response = api.client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not found."}}


def test_validation_error_does_not_echo_the_submitted_value(api):
    response = api.client.post("/api/auth/login", json={"username": SECRET_MARKER, "password": ""})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert SECRET_MARKER not in response.text


def test_api_responses_carry_security_headers(api):
    response = api.client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]

"""GET /api/usage and the request span (docs/32)."""

from policy_advisor import telemetry, tenancy
from policy_advisor.api.credentials import create_user


def _seed(username, matter_id, tokens):
    with telemetry.attributed(username=username, matter_id=matter_id):
        telemetry.record_cost("model", model="m", input_tokens=tokens, output_tokens=tokens // 10)


def test_usage_is_the_callers_own_unless_an_admin_asks_for_the_firm(api):
    _seed("alice", "case-a", 1000)
    _seed("bob", "case-b", 100)
    alice = api.login("alice")
    body = alice.get("/api/usage?days=7").json()
    assert body["scope"] == "me" and body["days"] == 7
    assert body["by_kind"] == [
        {
            "kind": "model",
            "matter_id": None,
            "calls": 1,
            "input_tokens": 1000,
            "output_tokens": 100,
            "estimated_usd": None,
        }
    ]
    assert [m["matter_id"] for m in body["by_matter"]] == ["case-a"]
    forbidden = alice.get("/api/usage?scope=tenant")
    assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "role_forbidden"

    create_user("ada", "Ada Admin", "ada-pass-123", rounds=4)
    tenancy.add_user_to_tenant("ada", "default", "admin")
    ada = api.login("ada", "ada-pass-123")
    firm = ada.get("/api/usage?scope=tenant").json()
    assert firm["scope"] == "tenant" and firm["by_kind"][0]["calls"] == 2
    assert sorted(m["matter_id"] for m in firm["by_matter"]) == ["case-a", "case-b"]
    assert ada.get("/api/usage?days=0").json()["days"] == 1  # clamped


def test_every_request_leaves_a_span_with_status_and_no_content(api):
    exporter = telemetry.memory_exporter()
    assert exporter is not None
    exporter.clear()
    alice = api.login("alice")
    response = alice.post("/api/matters", json={"id": "case-span", "title": "Secret client facts"})
    assert response.status_code == 201
    spans = [
        s
        for s in exporter.get_finished_spans()
        if s.name == "http.request" and s.attributes.get("path") == "/api/matters"
    ]
    assert spans, "the middleware should record a request span"
    attrs = dict(spans[-1].attributes)
    assert (
        attrs["status_code"] == 201
        and attrs["method"] == "POST"
        and attrs["tenant_id"] == "default"
    )
    assert not any("Secret" in str(v) for v in attrs.values())
    assert set(attrs) <= {"method", "path", "tenant_id", "status_code"}

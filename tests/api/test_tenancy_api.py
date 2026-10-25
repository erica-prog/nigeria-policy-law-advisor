"""Tenancy through the API (docs/30 slice P3-A, AC-1 and D2): another firm's
matter is invisible, sharing stays inside the firm, reviewers never write,
and the user record names the firm and role."""

from policy_advisor import tenancy
from policy_advisor.api.credentials import create_user
from policy_advisor.config import PHASE1_DEMO_MATTER_ID


def _firm_b_user(api, name="ben"):
    tenancy.create_tenant("firm-b", "Firm B")
    create_user(name, name.title(), f"{name}-pass-123", rounds=4)
    tenancy.add_user_to_tenant(name, "firm-b", "lawyer")
    return api.login(name, f"{name}-pass-123")


def test_me_carries_tenant_and_role_and_login_needs_a_firm(api):
    alice = api.login("alice")
    me = alice.get("/api/me").json()
    assert me["tenant_id"] == "default" and me["role"] == "lawyer"
    create_user("nomad", "No Firm", "nomad-pass-123", rounds=4)
    response = api.client.post(
        "/api/auth/login", json={"username": "nomad", "password": "nomad-pass-123"}
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "no_tenant"


def test_another_firms_matter_is_invisible_through_the_api(api):
    alice = api.login("alice")
    assert alice.post("/api/matters", json={"id": "alice-v-acme"}).status_code == 201
    ben = _firm_b_user(api)
    assert ben.get("/api/matters/alice-v-acme").status_code == 404
    assert [m["id"] for m in ben.get("/api/matters").json()["matters"]] == [PHASE1_DEMO_MATTER_ID]
    assert ben.post("/api/matters/alice-v-acme/chat", json={"message": "hi"}).status_code in (
        403,
        404,
    )
    # A share across firms is refused: the target is not in Alice's firm.
    response = alice.post("/api/matters/alice-v-acme/share", json={"username": "ben"})
    assert response.status_code == 404 and response.json()["error"]["code"] == "user_not_found"


def test_sharing_within_a_firm_and_the_reviewer_role(api):
    alice, bob = api.login("alice"), api.login("bob")
    assert alice.post("/api/matters", json={"id": "alice-v-acme"}).status_code == 201
    assert bob.get("/api/matters/alice-v-acme").status_code == 404

    shared = alice.post("/api/matters/alice-v-acme/share", json={"username": "bob"})
    assert shared.status_code == 200 and shared.json()["shared_with"] == ["bob"]
    assert bob.get("/api/matters/alice-v-acme").json()["read_only"] is True
    assert "alice-v-acme" in [m["id"] for m in bob.get("/api/matters").json()["matters"]]
    assert (
        bob.post("/api/matters/alice-v-acme/share", json={"username": "alice"}).status_code == 403
    )
    assert bob.delete("/api/matters/alice-v-acme").status_code == 403

    alice.post("/api/matters/alice-v-acme/share", json={"username": "bob", "can_write": True})
    assert bob.get("/api/matters/alice-v-acme").json()["read_only"] is False
    assert alice.delete("/api/matters/alice-v-acme/share/bob").status_code == 204
    assert bob.get("/api/matters/alice-v-acme").status_code == 404

    # A reviewer never writes, even on a writable share.
    create_user("rita", "Rita Reviewer", "rita-pass-123", rounds=4)
    tenancy.add_user_to_tenant("rita", "default", "reviewer")
    rita = api.login("rita", "rita-pass-123")
    assert rita.get("/api/me").json()["role"] == "reviewer"
    forbidden = rita.post("/api/matters", json={"id": "rita-v-x"})
    assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "role_forbidden"
    alice.post("/api/matters/alice-v-acme/share", json={"username": "rita", "can_write": True})
    assert rita.get("/api/matters/alice-v-acme").json()["read_only"] is True

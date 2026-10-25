"""Tenant settings, holds and retention events through the API (docs/37)."""

from policy_advisor import tenancy
from policy_advisor.api.credentials import create_user
from policy_advisor.retention import run_retention


def _admin(api, name="ada"):
    create_user(name, "Ada Admin", "ada-pass-123", rounds=4)
    tenancy.add_user_to_tenant(name, "default", "admin")
    return api.login(name, "ada-pass-123")


def test_tenant_settings_are_read_by_members_and_changed_by_admins(api, monkeypatch):
    import policy_advisor.config as config

    patched = config.get_settings().model_copy(
        update={"database_residency": "us", "retention_min_days": 30}
    )
    monkeypatch.setattr(config, "get_settings", lambda: patched)
    alice = api.login("alice")
    body = alice.get("/api/tenant").json()
    assert (
        body["tenant_id"] == "default"
        and body["deployment_residency"] == "us"
        and body["retention_min_days"] == 30
    )
    assert alice.put("/api/tenant", json={"retention_days_matter": 60}).status_code == 403

    ada = _admin(api)
    refused = ada.put("/api/tenant", json={"residency": "ng"})
    assert (
        refused.status_code == 400 and refused.json()["error"]["code"] == "residency_not_honoured"
    )
    short = ada.put("/api/tenant", json={"retention_days_matter": 5})
    assert short.status_code == 400 and short.json()["error"]["code"] == "retention_too_short"
    ok = ada.put(
        "/api/tenant", json={"residency": "us", "retention_days_matter": 60, "name": "Default LLP"}
    )
    assert (
        ok.status_code == 200
        and ok.json()["residency"] == "us"
        and ok.json()["retention_days_matter"] == 60
    )
    assert alice.get("/api/tenant").json()["name"] == "Default LLP"


def test_holds_and_retention_events(api):
    alice = api.login("alice")
    matter = alice.post("/api/matters", json={}).json()
    assert matter["hold"] is False
    held = alice.post(
        f"/api/matters/{matter['id']}/hold", json={"on": True, "reason": "Suit pending"}
    )
    assert (
        held.status_code == 200
        and held.json()["hold"] is True
        and held.json()["hold_reason"] == "Suit pending"
    )
    bob = api.login("bob")
    assert bob.post(f"/api/matters/{matter['id']}/hold", json={"on": False}).status_code == 404
    assert alice.post("/api/matters/phase1-demo/hold", json={"on": True}).status_code == 403

    services = api.services
    tenancy.update_tenant("default", retention_days_matter=30)
    from policy_advisor.db import connect

    with connect() as conn:
        conn.execute(
            "UPDATE matters SET updated_at = now() - interval '60 days' WHERE matter_id = %s",
            (matter["id"],),
        )
    report = run_retention(services.blob_store, apply=True, actor="test")
    assert report.skipped_hold == [matter["id"]] and report.deleted == []
    events = alice.get("/api/tenant/retention-events").json()["events"]
    assert [(e["matter_id"], e["action"]) for e in events] == [(matter["id"], "skipped_hold")]
    lifted = alice.post(f"/api/matters/{matter['id']}/hold", json={"on": False})
    assert lifted.json()["hold"] is False
    report = run_retention(services.blob_store, apply=True, actor="test")
    assert report.deleted == [matter["id"]]
    assert alice.get(f"/api/matters/{matter['id']}").status_code == 404

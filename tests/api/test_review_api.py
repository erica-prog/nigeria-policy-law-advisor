"""The review endpoints and queue (docs/36): a reviewer on a read-only share
approves; the owner cannot; approved artefacts are immutable; events and the
queue are visible to everyone with access."""

from policy_advisor import tenancy
from policy_advisor.api.credentials import create_user
from policy_advisor.artefacts.store import save_artefact
from policy_advisor.bulk.jobs import save_findings
from policy_advisor.tenancy import DEFAULT_TENANT, tenant_context


def _seed(matter_id):
    with tenant_context(DEFAULT_TENANT):
        artefact = save_artefact(
            matter_id=matter_id,
            kind="memo",
            title="Memo",
            instruction="Draft a memo on late defences.",
            outline={"title": "Memo", "sections": []},
            manifest={"entries": [], "counts": {}},
            docx_key=None,
            pptx_key=None,
            template_id=None,
            run_id=None,
            created_by="alice",
        )
        save_findings(
            matter_id,
            None,
            [
                {
                    "kind": "party_mismatch",
                    "severity": "review",
                    "summary": "Acme under two names",
                    "documents": ["a.docx", "b.docx"],
                    "evidence": [],
                }
            ],
        )
    return artefact.artefact_id


def test_review_flow_through_the_api(api):
    alice = api.login("alice")
    matter = alice.post("/api/matters", json={}).json()
    artefact_id = _seed(matter["id"])
    create_user("rita", "Rita", "rita-pass-123", rounds=4)
    tenancy.add_user_to_tenant("rita", "default", "reviewer")
    alice.post(f"/api/matters/{matter['id']}/share", json={"username": "rita"})
    rita = api.login("rita", "rita-pass-123")

    queue = alice.get(f"/api/matters/{matter['id']}/review-queue").json()
    assert [a["artefact_id"] for a in queue["artefacts"]] == [artefact_id] and len(
        queue["findings"]
    ) == 1
    finding_id = queue["findings"][0]["id"]

    reviewed = alice.post(
        f"/api/matters/{matter['id']}/artefacts/{artefact_id}/review",
        json={"action": "review", "note": "read"},
    )
    assert reviewed.status_code == 200 and reviewed.json()["status"] == "reviewed"
    forbidden = alice.post(
        f"/api/matters/{matter['id']}/artefacts/{artefact_id}/review", json={"action": "approve"}
    )
    assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "role_forbidden"
    approved = rita.post(
        f"/api/matters/{matter['id']}/artefacts/{artefact_id}/review",
        json={"action": "approve", "note": "fine"},
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    frozen = rita.post(
        f"/api/matters/{matter['id']}/artefacts/{artefact_id}/review", json={"action": "reject"}
    )
    assert frozen.status_code == 409 and frozen.json()["error"]["code"] == "immutable"

    events = rita.get(f"/api/matters/{matter['id']}/artefacts/{artefact_id}/events").json()[
        "events"
    ]
    assert [(e["action"], e["username"], e["role"]) for e in events] == [
        ("review", "alice", "lawyer"),
        ("approve", "rita", "reviewer"),
    ]

    dismissed = rita.post(
        f"/api/matters/{matter['id']}/bulk/findings/{finding_id}/review", json={"action": "dismiss"}
    )
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "dismissed"
    wrong = alice.post(
        f"/api/matters/{matter['id']}/bulk/findings/{finding_id}/review", json={"action": "dismiss"}
    )
    assert wrong.status_code in (403, 409)
    reopened = alice.post(
        f"/api/matters/{matter['id']}/bulk/findings/{finding_id}/review", json={"action": "reopen"}
    )
    assert reopened.status_code == 200 and reopened.json()["status"] == "open"
    assert rita.post(
        f"/api/matters/{matter['id']}/bulk/findings/{finding_id}/review", json={"action": "reopen"}
    ).status_code in (403, 409)
    trail = alice.get(f"/api/matters/{matter['id']}/bulk/findings/{finding_id}/events").json()[
        "events"
    ]
    assert [e["to_status"] for e in trail] == ["dismissed", "open"]
    assert alice.get(f"/api/matters/{matter['id']}/review-queue").json()["artefacts"] == []

    bob = api.login("bob")
    assert bob.get(f"/api/matters/{matter['id']}/review-queue").status_code == 404
    assert (
        alice.post(
            f"/api/matters/{matter['id']}/artefacts/nope/review", json={"action": "review"}
        ).status_code
        == 404
    )

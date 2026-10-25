"""Review states (docs/30 slice P3-F, D7): every transition and refusal,
the immutability of an approved artefact, and the events trail."""

import pytest

from policy_advisor import review
from policy_advisor.api.credentials import UserRecord
from policy_advisor.api.deps import MatterAccess
from policy_advisor.artefacts.store import get_artefact, save_artefact
from policy_advisor.bulk.jobs import get_finding, save_findings
from policy_advisor.ingestion import matter_store

OWNER = UserRecord("alice", "Alice", tenant_id="default", role="lawyer")
REVIEWER = UserRecord("rita", "Rita", tenant_id="default", role="reviewer")
ADMIN = UserRecord("ada", "Ada", tenant_id="default", role="admin")
WRITE = MatterAccess(id="case-1", owner="alice", read_only=False)
READ = MatterAccess(id="case-1", owner="alice", read_only=True)


@pytest.fixture
def subjects(fresh_db):
    matter_store.set_matter_owner("case-1", "alice")
    artefact = save_artefact(
        matter_id="case-1",
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
        "case-1",
        None,
        [
            {
                "kind": "date_conflict",
                "severity": "high",
                "summary": "Term differs",
                "documents": ["a.docx", "b.docx"],
                "evidence": [],
            }
        ],
    )
    finding_id = review.review_queue("case-1")["findings"][0]["id"]
    return artefact.artefact_id, finding_id


def test_artefact_walks_draft_reviewed_approved_and_then_freezes(subjects):
    artefact_id, _ = subjects
    assert (
        review.transition_artefact(
            artefact_id, "review", user=OWNER, access=WRITE, note="read it"
        ).status
        == "reviewed"
    )
    with pytest.raises(review.NotPermitted):
        review.transition_artefact(
            artefact_id, "approve", user=OWNER, access=WRITE
        )  # the author does not approve
    assert (
        review.transition_artefact(artefact_id, "approve", user=REVIEWER, access=READ).status
        == "approved"
    )
    for action, user in (("reject", ADMIN), ("review", OWNER), ("reopen", OWNER)):
        with pytest.raises(review.Immutable):
            review.transition_artefact(artefact_id, action, user=user, access=WRITE)
    trail = review.events_for("artefact", artefact_id)
    assert [(e.action, e.from_status, e.to_status, e.username, e.role) for e in trail] == [
        ("review", "draft", "reviewed", "alice", "lawyer"),
        ("approve", "reviewed", "approved", "rita", "reviewer"),
    ]
    assert trail[0].note == "read it"
    assert review.review_queue("case-1")["artefacts"] == []  # approved is no longer queued


def test_rejected_artefact_reopens_only_for_a_writer(subjects):
    artefact_id, _ = subjects
    assert (
        review.transition_artefact(artefact_id, "reject", user=ADMIN, access=READ).status
        == "rejected"
    )
    with pytest.raises(review.NotPermitted):
        review.transition_artefact(artefact_id, "reopen", user=REVIEWER, access=READ)
    assert (
        review.transition_artefact(artefact_id, "reopen", user=OWNER, access=WRITE).status
        == "draft"
    )
    with pytest.raises(review.WrongState):
        review.transition_artefact(
            artefact_id, "approve", user=REVIEWER, access=READ
        )  # draft cannot be approved
    with pytest.raises(KeyError):
        review.transition_artefact("missing", "review", user=OWNER, access=WRITE)


def test_findings_review_dismiss_and_reopen_with_the_same_roles(subjects):
    _, finding_id = subjects
    assert (
        review.transition_finding(finding_id, "review", user=REVIEWER, access=READ)["status"]
        == "reviewed"
    )
    with pytest.raises(review.NotPermitted):
        review.transition_finding(finding_id, "dismiss", user=OWNER, access=WRITE)
    assert (
        review.transition_finding(finding_id, "dismiss", user=ADMIN, access=READ, note="duplicate")[
            "status"
        ]
        == "dismissed"
    )
    assert review.review_queue("case-1")["findings"] == []
    with pytest.raises(review.WrongState):
        review.transition_finding(
            finding_id, "approve", user=ADMIN, access=READ
        )  # no such finding action
    assert (
        review.transition_finding(finding_id, "reopen", user=OWNER, access=WRITE)["status"]
        == "open"
    )
    assert [e.to_status for e in review.events_for("finding", str(finding_id))] == [
        "reviewed",
        "dismissed",
        "open",
    ]
    assert get_finding(finding_id)["status"] == "open"
    assert get_artefact(subjects[0]).status == "draft"

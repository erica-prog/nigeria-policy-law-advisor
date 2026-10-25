"""Bulk analysis through the API (docs/33): start, follow, records, findings,
resume; a reviewer may read but not start."""

from unittest.mock import patch

from policy_advisor import tenancy
from policy_advisor.api.credentials import create_user
from policy_advisor.bulk.extract import ModelResult
from policy_advisor.bulk.schemas import ContractExtraction, EvidenceRef, Obligation
from tests.api.helpers import synthetic_docx_bytes


def _upload_ready(client, matter_id, name):
    response = client.post(
        f"/api/matters/{matter_id}/documents",
        files={"file": (name, synthetic_docx_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 202, response.text
    for _ in range(50):
        docs = client.get(f"/api/matters/{matter_id}/documents").json()["documents"]
        if all(d["status"] == "ready" for d in docs if d["name"] == name):
            return


def _fake_caller(*, model, schema, document_text, api_key):
    if schema.schema_id == "obligation_conflicts":
        from policy_advisor.bulk.checks import ObligationConflicts

        return ModelResult(record=ObligationConflicts(), input_tokens=10, output_tokens=1)
    locator = document_text.split("]", 1)[0].lstrip("[")
    record = ContractExtraction(
        classification="complete executed agreement",
        obligations=[
            Obligation(
                obligor="the Buyer",
                obligee="the Supplier",
                obligation="pay each invoice",
                deadline="thirty days",
                evidence=[EvidenceRef(locator=locator, quote="within thirty days")],
            )
        ],
    )
    return ModelResult(record=record, input_tokens=500, output_tokens=50)


def test_bulk_job_runs_inline_and_exposes_records_and_findings(api_with_llm):
    alice = api_with_llm.login("alice")
    matter = alice.post("/api/matters", json={}).json()
    _upload_ready(alice, matter["id"], "supply.docx")
    with patch("policy_advisor.bulk.extract.langchain_caller", lambda settings=None: _fake_caller):
        started = alice.post(
            f"/api/matters/{matter['id']}/bulk",
            json={"schema_id": "contract", "ceiling_tokens": 50_000},
        )
    assert started.status_code == 202, started.text
    job = started.json()
    assert (
        job["status"] in ("queued", "running", "done")
        and job["backend"] == "sync"
        and job["documents"] == ["supply.docx"]
    )
    final = alice.get(f"/api/matters/{matter['id']}/bulk/{job['job_id']}").json()
    assert final["status"] == "done" and final["done_documents"] == 1 and final["spent_tokens"] > 0
    records = alice.get(f"/api/matters/{matter['id']}/bulk/records/contract").json()["records"]
    assert (
        len(records) == 1
        and records[0]["document"] == "supply.docx"
        and records[0]["record"]["obligations"]
    )
    assert alice.get(f"/api/matters/{matter['id']}/bulk/findings/all").json()["findings"] == []
    assert [j["job_id"] for j in alice.get(f"/api/matters/{matter['id']}/bulk").json()["jobs"]] == [
        job["job_id"]
    ]
    unknown = alice.post(f"/api/matters/{matter['id']}/bulk", json={"schema_id": "nope"})
    assert unknown.status_code == 400 and unknown.json()["error"]["code"] == "unknown_schema"
    not_paused = alice.post(
        f"/api/matters/{matter['id']}/bulk/{job['job_id']}/resume", json={"ceiling_tokens": 90_000}
    )
    assert not_paused.status_code == 409


def test_reviewer_reads_bulk_output_but_cannot_start_a_job(api_with_llm):
    alice = api_with_llm.login("alice")
    matter = alice.post("/api/matters", json={}).json()
    create_user("rita", "Rita Reviewer", "rita-pass-123", rounds=4)
    tenancy.add_user_to_tenant("rita", "default", "reviewer")
    alice.post(f"/api/matters/{matter['id']}/share", json={"username": "rita", "can_write": True})
    rita = api_with_llm.login("rita", "rita-pass-123")
    assert rita.get(f"/api/matters/{matter['id']}/bulk").status_code == 200
    forbidden = rita.post(f"/api/matters/{matter['id']}/bulk", json={"schema_id": "contract"})
    assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "matter_read_only"

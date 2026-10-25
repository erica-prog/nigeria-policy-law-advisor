"""Tenancy (docs/30 slice P3-A, AC-1): the database policies, not the
handlers, keep firms apart. Two firms write rows through the same pool; a
query that forgets every filter still sees only its own firm and the shared
libraries; a cross-tenant write is refused; the worker sees every queue but
runs each job as its own tenant; roles and shares hold through the API."""

import numpy as np
import psycopg
import pytest

from policy_advisor import tenancy
from policy_advisor.api.credentials import create_user
from policy_advisor.api.jobs import JobTable
from policy_advisor.config import PHASE1_DEMO_MATTER_ID
from policy_advisor.db import connect
from policy_advisor.ingestion import matter_store

DIM = 384


def _vectors(n):
    return np.ones((n, DIM), dtype=np.float32) / np.sqrt(DIM)


def _record(chunk_id, text, matter_id):
    return {
        "chunk_id": chunk_id,
        "text": text,
        "source_document": "doc.pdf",
        "locator": f"Clause {chunk_id[-1]}",
        "doc_type": "generic",
        "matter_id": matter_id,
        "jurisdiction": None,
        "page": 1,
    }


@pytest.fixture
def two_firms(fresh_db):
    tenancy.create_tenant("firm-a", "Firm A")
    tenancy.create_tenant("firm-b", "Firm B")
    for name, firm in (("ann", "firm-a"), ("ben", "firm-b")):
        create_user(name, name.title(), f"{name}-pass-123", rounds=4)
        tenancy.add_user_to_tenant(name, firm, "lawyer")
    with tenancy.tenant_context("firm-a"):
        matter_store.set_matter_owner("a-matter", "ann")
        matter_store.replace_matter_chunks(
            "a-matter",
            [
                _record("a1", "Firm A clause one", "a-matter"),
                _record("a2", "Firm A clause two", "a-matter"),
            ],
            _vectors(2),
        )
    with tenancy.tenant_context("firm-b"):
        matter_store.set_matter_owner("b-matter", "ben")
        matter_store.replace_matter_chunks(
            "b-matter", [_record("b1", "Firm B clause", "b-matter")], _vectors(1)
        )
    with tenancy.tenant_context(tenancy.SHARED_TENANT):
        matter_store.set_matter_tier(PHASE1_DEMO_MATTER_ID, "authority")
        matter_store.replace_matter_chunks(
            PHASE1_DEMO_MATTER_ID,
            [_record("s1", "Shared rule", PHASE1_DEMO_MATTER_ID)],
            _vectors(1),
        )
    return fresh_db


def _count(table):
    with connect() as conn:
        row = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()
    return row["n"]


def test_a_query_with_no_filter_sees_only_its_firm_and_the_shared_libraries(two_firms):
    # The same pool serves both firms in turn: the tenant is per transaction.
    with tenancy.tenant_context("firm-a"):
        assert _count("chunks") == 3 and _count("matters") == 2  # a's two + the shared one
        assert matter_store.get_matter_owner("b-matter") is None
        assert matter_store.list_matters() == ["a-matter", PHASE1_DEMO_MATTER_ID]
    with tenancy.tenant_context("firm-b"):
        assert _count("chunks") == 2 and matter_store.get_matter_owner("a-matter") is None
        assert [c["text"] for c in matter_store.load_matter_chunks("a-matter")] == []
    with tenancy.tenant_context(None):
        assert _count("chunks") == 1 and matter_store.list_matters() == [PHASE1_DEMO_MATTER_ID]


def test_the_policy_refuses_a_row_written_into_another_firm(two_firms):
    with tenancy.tenant_context("firm-a"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connect() as conn:
                conn.execute(
                    "INSERT INTO matters (matter_id, owner, tenant_id) VALUES ('sneaky', 'ann', 'firm-b')"
                )
        with pytest.raises(tenancy.TenantRequired):
            with tenancy.tenant_context(None):
                matter_store.set_matter_owner("no-tenant", "ann")
    with tenancy.tenant_context("firm-b"):
        assert matter_store.get_matter_owner("sneaky") is None


def test_the_worker_sees_every_queue_but_runs_each_job_as_its_firm(two_firms, monkeypatch):
    jobs = JobTable(worker_id="w1")
    with tenancy.tenant_context("firm-a"):
        jobs.create("a-matter", "a.pdf", "a-matter/a.pdf", uploaded_by="ann")
    with tenancy.tenant_context("firm-b"):
        jobs.create("b-matter", "b.pdf", "b-matter/b.pdf", uploaded_by="ben")
        assert {j.matter_id for j in [jobs.claim_next()]} == {"b-matter"}  # only its own queue
    seen = []

    def fake_run(self, job, services, jurisdiction, api_key, uploaded_by):
        seen.append((job.matter_id, tenancy.current_tenant()))

    monkeypatch.setattr(JobTable, "_run_job", fake_run)
    with tenancy.tenant_context(tenancy.ALL_TENANTS):
        ran = jobs.run_pending(services=None, limit=10)
    assert ran == 1 and seen == [("a-matter", "firm-a")]

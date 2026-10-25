"""Residency, retention and holds (docs/30 slice P3-G, AC-8)."""

import numpy as np
import pytest

from policy_advisor import retention, tenancy
from policy_advisor.config import get_settings
from policy_advisor.db import connect
from policy_advisor.ingestion import matter_store
from policy_advisor.storage import LocalBlobStore, document_key

DIM = 384


def _settings(monkeypatch, **overrides):
    patched = get_settings().model_copy(update=overrides)
    import policy_advisor.config as config

    monkeypatch.setattr(config, "get_settings", lambda: patched)
    return patched


def test_residency_must_match_the_deployment_and_retention_has_a_floor(fresh_db, monkeypatch):
    _settings(monkeypatch, database_residency="us", retention_min_days=30)
    tenancy.create_tenant("firm-us", "US firm", residency="us")
    tenancy.create_tenant("firm-none", "Anywhere firm")
    with pytest.raises(tenancy.ResidencyNotHonoured, match="honours residency 'us', not 'ng'"):
        tenancy.create_tenant("firm-ng", "Lagos firm", residency="ng")
    with pytest.raises(tenancy.RetentionTooShort):
        tenancy.update_tenant("firm-us", retention_days_matter=7)
    updated = tenancy.update_tenant("firm-us", retention_days_matter=90, retention_days_firm=None)
    assert (updated.residency, updated.retention_days_matter, updated.retention_days_firm) == (
        "us",
        90,
        None,
    )
    assert tenancy.get_tenant("firm-ng") is None
    with pytest.raises(ValueError):
        tenancy.create_tenant("firm-x", "X", residency="mars")


@pytest.fixture
def aged(fresh_db, tmp_path):
    """One firm with a 30-day retention; three matters: expired, expired but
    on hold, fresh; each with a chunk and an original in the blob store."""
    tenancy.create_tenant("firm-a", "Firm A")
    tenancy.update_tenant("firm-a", retention_days_matter=30)
    blobs = LocalBlobStore(tmp_path / "blobs")
    with tenancy.tenant_context("firm-a"):
        for matter_id in ("old", "held", "fresh"):
            matter_store.set_matter_owner(matter_id, "ann")
            matter_store.replace_document_chunks(
                matter_id,
                "doc.pdf",
                [
                    {
                        "chunk_id": f"{matter_id}-c1",
                        "text": "t",
                        "source_document": "doc.pdf",
                        "locator": "Section 1",
                        "doc_type": "generic",
                        "matter_id": matter_id,
                        "jurisdiction": None,
                        "page": 1,
                    }
                ],
                np.ones((1, DIM), dtype=np.float32) / np.sqrt(DIM),
            )
            original = tmp_path / f"{matter_id}.pdf"
            original.write_bytes(b"%PDF-1.4 synthetic")
            blobs.put(document_key(matter_id, "doc.pdf"), original)
        matter_store.set_hold("held", True, reason="litigation hold", by="ann")
        with connect() as conn:
            conn.execute(
                "UPDATE matters SET updated_at = now() - interval '45 days' WHERE matter_id IN ('old', 'held')"
            )
            conn.execute(
                "UPDATE matters SET updated_at = now() - interval '2 days' WHERE matter_id = 'fresh'"
            )
    return blobs


def test_retention_dry_run_records_and_deletes_nothing_then_apply_deletes_expired_unheld_matters(
    aged,
):
    blobs = aged
    report = retention.run_retention(blobs, apply=False)
    assert (
        report.would_delete == ["old"] and report.skipped_hold == ["held"] and report.deleted == []
    )
    with tenancy.tenant_context("firm-a"):
        assert matter_store.get_matter_owner("old") == "ann"  # nothing deleted on a dry run
        events = retention.retention_events("firm-a")
    assert sorted((e["matter_id"], e["action"]) for e in events) == [
        ("held", "skipped_hold"),
        ("old", "would_delete"),
    ]
    assert next(e for e in events if e["matter_id"] == "old")["counts"]["chunks"] == 1

    report = retention.run_retention(blobs, apply=True, actor="test")
    assert report.deleted == ["old"] and report.skipped_hold == ["held"]
    with tenancy.tenant_context("firm-a"):
        assert matter_store.get_matter_owner("old") is None
        assert (
            matter_store.get_matter_owner("held") == "ann"
            and matter_store.get_matter_owner("fresh") == "ann"
        )
        assert matter_store.load_matter_chunks("old") == []
        deleted = [e for e in retention.retention_events("firm-a") if e["action"] == "deleted"]
    assert [(e["matter_id"], e["actor"], e["counts"]["chunks"]) for e in deleted] == [
        ("old", "test", 1)
    ]
    assert not blobs.exists(document_key("old", "doc.pdf")) and blobs.exists(
        document_key("held", "doc.pdf")
    )
    # The shared libraries are never candidates, and a tenant filter narrows the run.
    assert [c.matter_id for c in retention.candidates(tenant_id="default")] == []
    assert all(c.matter_id not in ("phase1-demo", "us-library") for c in retention.candidates())


def test_lifting_a_hold_makes_the_matter_a_candidate_again(aged):
    with tenancy.tenant_context("firm-a"):
        matter_store.set_hold("held", False, reason=None, by="ann")
        assert matter_store.get_matter_meta("held")["hold"] is False
    assert sorted(c.matter_id for c in retention.candidates(tenant_id="firm-a") if not c.hold) == [
        "held",
        "old",
    ]

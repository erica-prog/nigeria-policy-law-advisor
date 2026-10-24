"""The database-backed document queue (docs/22 slice P1-C3, AC-9)."""

from unittest.mock import MagicMock

from policy_advisor.api.jobs import GENERIC_FAILURE, JobTable
from policy_advisor.db import connect
from policy_advisor.ingestion.matter_store import set_matter_owner


def _services(blob_store) -> MagicMock:
    services = MagicMock()
    services.blob_store = blob_store
    return services


def test_two_workers_never_claim_the_same_job(fresh_db):
    set_matter_owner("m", "alice")
    first, second = JobTable("w1"), JobTable("w2")
    first.create("m", "a.pdf", "m/a.pdf")
    first.create("m", "b.pdf", "m/b.pdf")

    claimed = {first.claim_next().document_name, second.claim_next().document_name}

    assert claimed == {"a.pdf", "b.pdf"}
    assert first.claim_next() is None
    assert {j.status for j in first.latest_for_matter("m").values()} == {"processing"}


def test_a_queued_job_survives_a_restart_and_is_picked_up_later(fresh_db, tmp_path):
    # AC-9: the row outlives the process that wrote it; a fresh JobTable (a new
    # worker) finds and runs it. Ingestion itself is stubbed: this test is
    # about the queue, not the parser.
    from policy_advisor.storage import LocalBlobStore

    set_matter_owner("m", "alice")
    store = LocalBlobStore(tmp_path / "originals")
    source = tmp_path / "memo.docx"
    source.write_bytes(b"bytes")
    store.put("m/memo.docx", source)
    JobTable("api-process").create("m", "memo.docx", "m/memo.docx", uploaded_by="alice")

    import policy_advisor.api.jobs as jobs_module

    def fake_add_document(matter_id, file_path, jurisdiction=None, translate=None, **kwargs):
        assert file_path.read_bytes() == b"bytes"
        return 7

    original = jobs_module.add_document if hasattr(jobs_module, "add_document") else None
    import policy_advisor.ingestion.ingest_document as ingest

    saved = ingest.add_document
    ingest.add_document = fake_add_document
    try:
        ran = JobTable("worker-after-restart").run_pending(_services(store))
    finally:
        ingest.add_document = saved
        if original is not None:
            jobs_module.add_document = original

    assert ran == 1
    (job,) = JobTable().latest_for_matter("m").values()
    assert job.status == "ready" and job.chunk_count == 7


def test_cancelled_during_processing_removes_the_chunks(fresh_db, tmp_path):
    from policy_advisor.storage import LocalBlobStore

    set_matter_owner("m", "alice")
    store = LocalBlobStore(tmp_path / "originals")
    source = tmp_path / "memo.docx"
    source.write_bytes(b"bytes")
    store.put("m/memo.docx", source)
    table = JobTable()
    table.create("m", "memo.docx", "m/memo.docx")
    removed = []

    import policy_advisor.ingestion.ingest_document as ingest

    def add_then_cancel(matter_id, file_path, jurisdiction=None, translate=None, **kwargs):
        table.forget_document(matter_id, file_path.name)  # the user clicks remove mid-read
        return 3

    saved_add, saved_remove = ingest.add_document, ingest.remove_document
    ingest.add_document = add_then_cancel
    ingest.remove_document = lambda matter_id, name: removed.append(name)
    try:
        table.run_pending(_services(store))
    finally:
        ingest.add_document, ingest.remove_document = saved_add, saved_remove

    assert removed == ["memo.docx"]
    assert table.latest_for_matter("m") == {}
    assert not store.exists("m/memo.docx")


def test_missing_bytes_fail_with_a_curated_message(fresh_db, tmp_path):
    from policy_advisor.storage import LocalBlobStore

    set_matter_owner("m", "alice")
    table = JobTable()
    table.create("m", "gone.pdf", "m/gone.pdf")
    table.run_pending(_services(LocalBlobStore(tmp_path / "originals")))
    (job,) = table.latest_for_matter("m").values()
    assert job.status == "failed" and "Upload it again" in (job.error or "")


def test_stale_processing_jobs_are_requeued_then_failed(fresh_db):
    set_matter_owner("m", "alice")
    table = JobTable()
    job = table.create("m", "slow.pdf", "m/slow.pdf")
    with connect() as conn:
        conn.execute(
            "UPDATE document_jobs SET status = 'processing', locked_at = now() - interval '1 hour', attempts = 1 "
            "WHERE job_id = %s",
            (job.job_id,),
        )
    assert table.requeue_stale() == 1
    assert table.get(job.job_id).status == "queued"

    with connect() as conn:
        conn.execute(
            "UPDATE document_jobs SET status = 'processing', locked_at = now() - interval '1 hour', attempts = 3 "
            "WHERE job_id = %s",
            (job.job_id,),
        )
    assert table.requeue_stale() == 1
    failed = table.get(job.job_id)
    assert failed.status == "failed" and failed.error == GENERIC_FAILURE

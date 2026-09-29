"""Upload -> background ingestion -> ready -> listed -> removed, against a
temporary Chroma index and matter store with a synthetic DOCX. TestClient
runs BackgroundTasks before returning, so status is observable immediately."""

from tests.api.helpers import synthetic_docx_bytes


def _upload(client, matter_id, name="supply-agreement.docx", data=None):
    return client.post(
        f"/api/matters/{matter_id}/documents",
        files={
            "file": (
                name,
                data if data is not None else synthetic_docx_bytes(),
                "application/octet-stream",
            )
        },
    )


def test_synthetic_docx_upload_reaches_ready_and_is_listed(api):
    alice = api.login("alice")
    assert alice.post("/api/matters", json={"id": "alice-v-acme"}).status_code == 201

    response = _upload(alice, "alice-v-acme")
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["name"] == "supply-agreement.docx"
    assert job["job_id"]

    detail = alice.get("/api/matters/alice-v-acme/documents/supply-agreement.docx").json()
    assert detail["status"] == "ready", detail
    assert detail["chunk_count"] and detail["chunk_count"] > 0
    assert detail["error"] is None

    listed = alice.get("/api/matters/alice-v-acme/documents").json()["documents"]
    assert [d["name"] for d in listed] == ["supply-agreement.docx"]
    assert alice.get("/api/matters/alice-v-acme").json()["document_count"] == 1

    # The temp upload directory is gone once processing finished.
    assert api.services.ingest_lock.acquire(blocking=False)
    api.services.ingest_lock.release()


def test_remove_document_clears_it_from_the_list(api):
    alice = api.login("alice")
    alice.post("/api/matters", json={"id": "alice-v-acme"})
    _upload(alice, "alice-v-acme")

    assert (
        alice.delete("/api/matters/alice-v-acme/documents/supply-agreement.docx").status_code == 204
    )
    assert alice.get("/api/matters/alice-v-acme/documents").json() == {"documents": []}
    missing = alice.delete("/api/matters/alice-v-acme/documents/supply-agreement.docx")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "document_not_found"


def test_unsupported_extension_is_rejected_before_any_processing(api):
    alice = api.login("alice")
    alice.post("/api/matters", json={"id": "alice-v-acme"})
    response = _upload(alice, "alice-v-acme", name="notes.txt", data=b"plain text")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_document"
    assert alice.get("/api/matters/alice-v-acme/documents").json() == {"documents": []}


def test_filename_is_reduced_to_a_safe_basename(api):
    alice = api.login("alice")
    alice.post("/api/matters", json={"id": "alice-v-acme"})
    response = _upload(alice, "alice-v-acme", name="../../etc/passwd.docx")
    assert response.status_code == 202
    assert response.json()["name"] == "passwd.docx"


def test_corrupt_docx_fails_with_curated_message_not_exception_text(api):
    alice = api.login("alice")
    alice.post("/api/matters", json={"id": "alice-v-acme"})
    response = _upload(alice, "alice-v-acme", name="broken.docx", data=b"not really a docx")
    assert response.status_code == 202

    detail = alice.get("/api/matters/alice-v-acme/documents/broken.docx").json()
    assert detail["status"] == "failed"
    assert detail["error"] == "Processing failed. The server log has the details."
    assert "Traceback" not in detail["error"]
    assert "/" not in detail["error"]

    # A failed upload can be dismissed.
    assert alice.delete("/api/matters/alice-v-acme/documents/broken.docx").status_code == 204
    assert alice.get("/api/matters/alice-v-acme/documents").json() == {"documents": []}


def test_oversized_upload_is_rejected(api, monkeypatch):
    from policy_advisor.config import get_settings

    monkeypatch.setenv("MAX_UPLOAD_MB", "0")
    get_settings.cache_clear()
    alice = api.login("alice")
    alice.post("/api/matters", json={"id": "alice-v-acme"})
    response = _upload(alice, "alice-v-acme")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "upload_too_large"

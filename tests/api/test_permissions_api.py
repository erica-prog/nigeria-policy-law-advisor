"""AGENTS.md rule 1: ownership is enforced on the backend before any document,
retrieval or generation access. These tests use the real dependency chain and
real matter metadata on disk; only the language model is faked, and the
assertions check it was never invoked for a foreign matter."""

from tests.api.helpers import (
    FakeCaseChain,
    FakeRagChain,
    FakeRetriever,
    analysis_result,
    corpus_answer,
    synthetic_docx_bytes,
)


def _create(client, matter_id="alice-v-acme"):
    response = client.post("/api/matters", json={"id": matter_id})
    assert response.status_code == 201, response.text
    return matter_id


def test_matter_list_only_shows_own_matters_plus_shared_demo(api):
    alice, bob = api.login("alice"), api.login("bob")
    _create(alice, "alice-v-acme")
    _create(bob, "bob-v-globex")

    alice_ids = [m["id"] for m in alice.get("/api/matters").json()["matters"]]
    bob_ids = [m["id"] for m in bob.get("/api/matters").json()["matters"]]

    assert alice_ids == ["alice-v-acme", "phase1-demo"]
    assert bob_ids == ["bob-v-globex", "phase1-demo"]
    demo = next(m for m in alice.get("/api/matters").json()["matters"] if m["id"] == "phase1-demo")
    assert demo["read_only"] is True


def test_other_users_matter_is_404_everywhere(api_with_llm):
    api = api_with_llm
    alice, bob = api.login("alice"), api.login("bob")
    _create(alice, "alice-v-acme")
    rag = FakeRagChain(result=corpus_answer())
    case = FakeCaseChain(result=analysis_result())
    api.services._rag_chain = rag
    api.services._case_chain = case
    api.services._retriever = FakeRetriever(chunks=[])

    attempts = [
        bob.get("/api/matters/alice-v-acme"),
        bob.get("/api/matters/alice-v-acme/documents"),
        bob.post(
            "/api/matters/alice-v-acme/documents",
            files={"file": ("contract.docx", synthetic_docx_bytes(), "application/octet-stream")},
        ),
        bob.delete("/api/matters/alice-v-acme/documents/contract.docx"),
        bob.post("/api/matters/alice-v-acme/ask", json={"question": "What are the payment terms?"}),
        bob.post("/api/matters/alice-v-acme/analyze", json={"case_facts": "Some facts."}),
    ]
    for response in attempts:
        assert response.status_code == 404, response.text
        assert response.json() == {
            "error": {"code": "matter_not_found", "message": "Matter not found."}
        }

    assert rag.calls == [], "retrieval/generation ran for a matter the caller does not own"
    assert case.calls == []
    # Nothing was ingested into Alice's matter either.
    assert alice.get("/api/matters/alice-v-acme/documents").json() == {"documents": []}


def test_nonexistent_and_malformed_matter_ids_are_404(api):
    alice = api.login("alice")
    assert alice.get("/api/matters/never-created").status_code == 404
    assert alice.get("/api/matters/..%2F..%2Fetc").status_code == 404
    assert alice.get("/api/matters/UPPER").status_code == 404


def test_matter_id_must_be_a_safe_slug(api):
    alice = api.login("alice")
    for bad in ["../escape", "has space", "UpperCase", "a", "x" * 70]:
        response = alice.post("/api/matters", json={"id": bad})
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "validation_error"
    matters_dir = api.root / "matters"
    assert not matters_dir.exists() or list(matters_dir.iterdir()) == []
    assert not (api.root / "escape").exists()


def test_duplicate_matter_id_is_rejected_even_across_users(api):
    alice, bob = api.login("alice"), api.login("bob")
    _create(alice, "shared-name")
    response = bob.post("/api/matters", json={"id": "shared-name"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "matter_exists"
    assert bob.get("/api/matters/shared-name").status_code == 404
    assert bob.post("/api/matters", json={"id": "phase1-demo"}).status_code == 409


def test_shared_demo_matter_is_readable_but_not_writable(api):
    bob = api.login("bob")
    assert bob.get("/api/matters/phase1-demo").status_code == 200
    assert bob.get("/api/matters/phase1-demo/documents").status_code == 200
    upload = bob.post(
        "/api/matters/phase1-demo/documents",
        files={"file": ("contract.docx", synthetic_docx_bytes(), "application/octet-stream")},
    )
    assert upload.status_code == 403
    assert upload.json()["error"]["code"] == "matter_read_only"
    assert bob.delete("/api/matters/phase1-demo/documents/anything.pdf").status_code == 403

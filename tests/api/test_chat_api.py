"""The chat endpoint (revision 2): server-side routing between case analysis,
research mode and questions, research-mode labelling, ownership, titles and
the persisted conversation. Chains are faked; the model is never called."""

import pytest

from policy_advisor.api.conversations import derive_title
from policy_advisor.api.routers.chat import RESEARCH_NOTICE
from tests.api.helpers import (
    FakeCaseChain,
    FakeRagChain,
    FakeRetriever,
    analysis_result,
    chunk,
    corpus_answer,
    not_found_answer,
    synthetic_docx_bytes,
    web_answer,
)

CASE = "Acme stopped paying our invoices in March and terminated the supply contract by email."


def _new_case(client, title=None):
    body = {"title": title} if title else {}
    response = client.post("/api/matters", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _chat(client, matter_id, message, **extra):
    return client.post(f"/api/matters/{matter_id}/chat", json={"message": message, **extra})


def _upload_ready(client, matter_id, name="supply.docx"):
    response = client.post(
        f"/api/matters/{matter_id}/documents",
        files={"file": (name, synthetic_docx_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 202, response.text
    detail = client.get(f"/api/matters/{matter_id}/documents/{name}").json()
    assert detail["status"] == "ready", detail


@pytest.fixture
def fakes(api_with_llm):
    rag = FakeRagChain(result=corpus_answer())
    case = FakeCaseChain(result=analysis_result())
    api_with_llm.services._rag_chain = rag
    api_with_llm.services._case_chain = case
    api_with_llm.services._retriever = FakeRetriever(
        chunks=[chunk("Section 3", "Either party may terminate on ninety days written notice.")]
    )
    return rag, case


# ---- matters created behind the scenes ----


def test_matter_can_be_created_without_an_id_and_gets_a_generated_slug(api):
    alice = api.login("alice")
    matter = _new_case(alice)
    assert matter["id"].startswith("case-") and len(matter["id"]) == 17
    assert matter["title"] is None and matter["read_only"] is False
    assert matter["created_at"] and matter["updated_at"] == matter["created_at"]
    assert alice.get(f"/api/matters/{matter['id']}").status_code == 200

    titled = _new_case(alice, title="Late invoices")
    assert titled["title"] == "Late invoices"
    assert alice.post("/api/matters", json={"id": "explicit-id"}).status_code == 201


def test_shared_library_has_a_human_title_and_is_listed_last(api):
    alice = api.login("alice")
    _new_case(alice, title="First")
    matters = alice.get("/api/matters").json()["matters"]
    assert [m["title"] for m in matters] == ["First", "Reference library (shared, read-only)"]
    assert matters[-1]["id"] == "phase1-demo" and matters[-1]["read_only"] is True


# ---- routing ----


def test_first_message_with_documents_runs_case_analysis(api_with_llm, fakes):
    rag, case = fakes
    alice = api_with_llm.login("alice")
    matter = _new_case(alice)
    _upload_ready(alice, matter["id"])

    response = _chat(alice, matter["id"], CASE)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["mode"] == "analysis"
    assert body["analysis"]["overall_position"] == "Claimant likely succeeds."
    assert body["answer"] is None and body["notice"] is None
    assert body["avatar_state"] == "verified_source"
    assert [c["locator"] for c in body["citations"]] == ["Section 3"]
    assert body["citations"][0]["kind"] == "evidence"
    assert "analysed your case" in body["bubble"]
    assert {c["action"] for c in body["chips"]} == {"ask"}
    assert case.calls[0]["case_facts"] == CASE and case.calls[0]["matter_id"] == matter["id"]
    assert rag.calls == []


def test_first_message_without_documents_is_research_mode_forcing_web(api_with_llm, fakes):
    rag, case = fakes
    rag.result = web_answer()
    alice = api_with_llm.login("alice")
    matter = _new_case(alice)

    body = _chat(alice, matter["id"], CASE, allow_web=False).json()

    assert body["mode"] == "research"
    assert rag.calls[0]["allow_web_fallback"] is True, "research mode must force the web search"
    assert case.calls == [], "no documents: case analysis must not run"
    assert body["answer"]["source"] == "web"
    assert body["avatar_state"] == "web_source"
    assert body["notice"] == RESEARCH_NOTICE
    assert "not evidence" in body["notice"] and "not evidence" in body["bubble"]
    assert [c["kind"] for c in body["citations"]] == ["web"]
    assert all(c["kind"] != "evidence" for c in body["citations"])
    assert [c["action"] for c in body["chips"]] == ["add_documents"]


def test_research_mode_with_nothing_found_still_asks_for_documents(api_with_llm, fakes):
    rag, _ = fakes
    rag.result = not_found_answer()
    alice = api_with_llm.login("alice")
    matter = _new_case(alice)

    body = _chat(alice, matter["id"], CASE).json()
    assert body["mode"] == "research" and body["avatar_state"] == "no_results"
    assert body["notice"] == RESEARCH_NOTICE
    assert [c["action"] for c in body["chips"]] == ["add_documents"]


def test_follow_up_messages_are_questions_and_respect_allow_web(api_with_llm, fakes):
    rag, case = fakes
    alice = api_with_llm.login("alice")
    matter = _new_case(alice)
    _upload_ready(alice, matter["id"])
    assert _chat(alice, matter["id"], CASE).json()["mode"] == "analysis"

    body = _chat(alice, matter["id"], "When are invoices due?").json()
    assert body["mode"] == "question"
    assert body["answer"]["source"] == "corpus" and body["analysis"] is None
    assert rag.calls[-1]["allow_web_fallback"] is False
    assert {(c["document"], c["kind"]) for c in body["citations"]} == {
        ("supply.docx", "evidence"),
        ("rules.pdf", "authority"),
    }
    assert len(case.calls) == 1

    rag.result = web_answer()
    body = _chat(
        alice, matter["id"], "What does the Data Protection Act say?", allow_web=True
    ).json()
    assert body["mode"] == "question" and body["answer"]["source"] == "web"
    assert rag.calls[-1]["allow_web_fallback"] is True
    assert body["notice"] and "cannot replace missing case evidence" in body["notice"]


def test_analyze_chip_forces_analysis_even_after_earlier_messages(api_with_llm, fakes):
    rag, case = fakes
    alice = api_with_llm.login("alice")
    matter = _new_case(alice)
    rag.result = not_found_answer()
    first = _chat(alice, matter["id"], CASE).json()  # no documents yet -> research
    assert first["mode"] == "research"

    _upload_ready(alice, matter["id"])
    body = _chat(alice, matter["id"], CASE, intent="analyze").json()
    assert body["mode"] == "analysis"
    assert len(case.calls) == 1

    # A plain second message after the analysis is a question again.
    rag.result = corpus_answer()
    assert _chat(alice, matter["id"], "And the interest rate?").json()["mode"] == "question"


def test_no_results_question_offers_the_web_chip_only_when_web_was_off(api_with_llm, fakes):
    rag, _ = fakes
    rag.result = not_found_answer()
    alice = api_with_llm.login("alice")
    matter = _new_case(alice)
    _upload_ready(alice, matter["id"])
    _chat(alice, matter["id"], CASE)  # analysis

    off = _chat(alice, matter["id"], "Is there a penalty clause?").json()
    assert [c["action"] for c in off["chips"]] == ["ask_web"]
    on = _chat(alice, matter["id"], "Is there a penalty clause?", allow_web=True).json()
    assert [c["action"] for c in on["chips"]] == ["ask"]


def test_shared_library_first_message_is_a_question_and_is_not_persisted(api_with_llm, fakes):
    rag, case = fakes
    alice = api_with_llm.login("alice")
    body = _chat(alice, "phase1-demo", "How long do I have to file a defence?").json()
    assert body["mode"] == "question" and case.calls == []
    history = alice.get("/api/matters/phase1-demo/chat").json()
    assert history["messages"] == [] and history["title"] is None
    assert not (api_with_llm.root / "matters" / "phase1-demo" / "conversation.json").exists()


# ---- titles and history ----


def test_first_message_sets_the_case_title_and_history_is_persisted(api_with_llm, fakes):
    alice = api_with_llm.login("alice")
    matter = _new_case(alice)
    _upload_ready(alice, matter["id"])

    reply = _chat(alice, matter["id"], CASE).json()
    _chat(alice, matter["id"], "When are invoices due?")

    listed = alice.get(f"/api/matters/{matter['id']}").json()
    assert listed["title"] == "Acme stopped paying our invoices in March and terminated the…"
    assert listed["updated_at"] >= listed["created_at"]

    history = alice.get(f"/api/matters/{matter['id']}/chat").json()
    assert history["title"] == listed["title"] and history["has_analysis"] is True
    roles = [m["role"] for m in history["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert history["messages"][0]["text"] == CASE
    assert history["messages"][1]["id"] == reply["id"]
    assert history["messages"][1]["analysis"]["issues"][0]["issue"].startswith("Whether")
    assert history["messages"][3]["mode"] == "question"


def test_explicit_title_is_not_overwritten_by_the_first_message(api_with_llm, fakes):
    alice = api_with_llm.login("alice")
    matter = _new_case(alice, title="Acme supply dispute")
    _chat(alice, matter["id"], CASE)
    assert alice.get(f"/api/matters/{matter['id']}").json()["title"] == "Acme supply dispute"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Short case", "Short case"),
        ("  lots   of\n\nwhitespace   here ", "lots of whitespace here"),
        ("   ", "New case"),
        (
            "Acme stopped paying our invoices in March and terminated the supply contract.",
            "Acme stopped paying our invoices in March and terminated the…",
        ),
        ("x" * 100, "x" * 60 + "…"),
    ],
)
def test_derive_title(message, expected):
    title = derive_title(message)
    assert title == expected
    assert len(title) <= 80 and "\n" not in title


# ---- permissions and errors ----


def test_chat_on_another_users_matter_is_404_and_never_reaches_the_chains(api_with_llm, fakes):
    rag, case = fakes
    alice, bob = api_with_llm.login("alice"), api_with_llm.login("bob")
    matter = _new_case(alice, title="Alice's case")

    for response in [
        _chat(bob, matter["id"], CASE),
        _chat(bob, matter["id"], CASE, intent="analyze"),
        bob.get(f"/api/matters/{matter['id']}/chat"),
        _chat(bob, "never-created", CASE),
    ]:
        assert response.status_code == 404, response.text
        assert response.json() == {
            "error": {"code": "matter_not_found", "message": "Matter not found."}
        }
    assert rag.calls == [] and case.calls == []
    assert alice.get(f"/api/matters/{matter['id']}/chat").json()["messages"] == []
    assert [m["title"] for m in bob.get("/api/matters").json()["matters"]] == [
        "Reference library (shared, read-only)"
    ]


def test_chat_requires_login_and_a_usable_claude_key(api):
    assert (
        api.client.post("/api/matters/phase1-demo/chat", json={"message": "x"}).status_code == 401
    )
    alice = api.login("alice")
    matter = _new_case(alice)
    for response in [
        _chat(alice, matter["id"], CASE),
        alice.post(f"/api/matters/{matter['id']}/chat/jobs", json={"message": CASE}),
    ]:
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "claude_key_required"
    assert alice.get(f"/api/matters/{matter['id']}/chat").json()["messages"] == []


def test_chat_validation_error_shape(api_with_llm, fakes):
    alice = api_with_llm.login("alice")
    matter = _new_case(alice)
    response = _chat(alice, matter["id"], "", intent="nonsense")
    assert response.status_code == 422
    assert {tuple(d["loc"]) for d in response.json()["error"]["details"]} == {
        ("body", "message"),
        ("body", "intent"),
    }

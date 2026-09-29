"""Ask and analyze against fake chains: contract shape, citation resolution,
evidence/authority/web labelling and avatar_state. The model is never called."""

from tests.api.helpers import (
    FakeCaseChain,
    FakeRagChain,
    FakeRetriever,
    analysis_result,
    chunk,
    corpus_answer,
    not_found_answer,
    web_answer,
)

ASK = "/api/matters/alice-v-acme/ask"
ANALYZE = "/api/matters/alice-v-acme/analyze"


def _alice_with_matter(api):
    alice = api.login("alice")
    assert alice.post("/api/matters", json={"id": "alice-v-acme"}).status_code == 201
    return alice


def test_ask_without_model_key_is_a_clear_503(api):
    alice = _alice_with_matter(api)
    response = alice.post(ASK, json={"question": "What are the payment terms?"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_unavailable"
    assert "ANTHROPIC_API_KEY" in response.json()["error"]["message"]


def test_grounded_answer_resolves_citations_and_labels_kinds(api_with_llm):
    api = api_with_llm
    alice = _alice_with_matter(api)
    rag = FakeRagChain(result=corpus_answer(faithful=True))
    api.services._rag_chain = rag

    response = alice.post(ASK, json={"question": "What are the payment terms?", "language": "fr"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["source"] == "corpus"
    assert body["faithful"] is True
    assert body["avatar_state"] == "verified_source"
    assert body["unsupported_citations"] == []
    assert body["usage"] == {"input_tokens": 10, "output_tokens": 5}

    kinds = {(c["document"], c["kind"]) for c in body["citations"]}
    assert kinds == {("supply.docx", "evidence"), ("rules.pdf", "authority")}
    evidence = next(c for c in body["citations"] if c["kind"] == "evidence")
    assert evidence["locator"] == "Section 2"
    assert evidence["cited_as"] == "Section 2"
    assert evidence["quote"] == "The Buyer shall pay each invoice within thirty days of delivery."
    assert evidence["page"] == 1
    pinpoint = next(c for c in body["citations"] if c["kind"] == "authority")
    assert pinpoint["locator"] == "Order 5 Rule 3"
    assert pinpoint["cited_as"] == "Order 5 Rule 3(1)"
    assert len(body["retrieved"]) == 2

    call = rag.calls[0]
    assert call["matter_id"] == "alice-v-acme"
    assert call["allow_web_fallback"] is False
    assert call["conversation_language"] == "fr"


def test_unfaithful_answer_is_unverified_and_never_cites_the_unretrieved_locator(api_with_llm):
    api = api_with_llm
    alice = _alice_with_matter(api)
    api.services._rag_chain = FakeRagChain(result=corpus_answer(faithful=False))

    body = alice.post(ASK, json={"question": "q"}).json()
    assert body["faithful"] is False
    assert body["avatar_state"] == "unverified"
    assert body["unsupported_citations"] == ["Order 99 Rule 1"]
    assert all(c["locator"] != "Order 99 Rule 1" for c in body["citations"])
    assert all(c["cited_as"] != "Order 99 Rule 1" for c in body["citations"])


def test_no_results_answer(api_with_llm):
    api = api_with_llm
    alice = _alice_with_matter(api)
    api.services._rag_chain = FakeRagChain(result=not_found_answer())

    body = alice.post(ASK, json={"question": "q"}).json()
    assert body["source"] == "none"
    assert body["avatar_state"] == "no_results"
    assert body["citations"] == [] and body["retrieved"] == []


def test_web_answer_is_labelled_web_never_evidence(api_with_llm):
    api = api_with_llm
    alice = _alice_with_matter(api)
    rag = FakeRagChain(result=web_answer())
    api.services._rag_chain = rag

    body = alice.post(ASK, json={"question": "q", "allow_web": True}).json()
    assert body["source"] == "web"
    assert body["avatar_state"] == "web_source"
    assert [c["kind"] for c in body["citations"]] == ["web"]
    assert body["citations"][0]["url"] == "https://ndpc.gov.ng/act"
    assert body["citations"][0]["document"] == "NDPA 2023"
    assert body["citations"][0]["quote"] is None
    assert body["retrieved"] == []
    assert rag.calls[0]["allow_web_fallback"] is True


def test_llm_failure_inside_the_chain_becomes_503(api_with_llm):
    from policy_advisor.generation.chain import LLM_UNAVAILABLE_MESSAGE, AnswerResult

    api = api_with_llm
    alice = _alice_with_matter(api)
    api.services._rag_chain = FakeRagChain(
        result=AnswerResult(
            answer=LLM_UNAVAILABLE_MESSAGE, retrieved=[chunk("Section 1", "t")], faithful=True
        )
    )
    response = alice.post(ASK, json={"question": "q"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_unavailable"


def test_ask_validation_error_shape(api_with_llm):
    alice = _alice_with_matter(api_with_llm)
    response = alice.post(ASK, json={"question": "", "language": "de"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert {tuple(d["loc"]) for d in error["details"]} == {
        ("body", "question"),
        ("body", "language"),
    }


def test_analyze_resolves_authorities_and_reports_gaps(api_with_llm):
    api = api_with_llm
    alice = _alice_with_matter(api)
    case = FakeCaseChain(result=analysis_result())
    api.services._case_chain = case
    api.services._retriever = FakeRetriever(
        chunks=[chunk("Section 3", "Either party may terminate on ninety days written notice.")]
    )

    response = alice.post(ANALYZE, json={"case_facts": "Acme terminated on 10 days notice."})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["avatar_state"] == "verified_source"
    assert body["disclaimer"] == "Research aid only."
    assert body["overall_position"] == "Claimant likely succeeds."
    assert body["missing_evidence"] == [] and body["contradictions"] == []
    assert body["counterarguments"] == []

    issue = body["issues"][0]
    assert issue["confidence"] == "strongly supported" and issue["unverified"] is False
    claimant, respondent = issue["arguments"]
    resolved = claimant["authorities"][0]
    assert resolved["locator"] == "Section 3"
    assert resolved["source"]["kind"] == "evidence"
    assert resolved["source"]["quote"].startswith("Either party may terminate")
    unresolved = respondent["authorities"][0]
    assert unresolved["locator"] == "Order 77 Rule 1"
    assert unresolved["source"] is None

    assert [c["locator"] for c in body["citations"]] == ["Section 3"]
    assert case.calls[0]["matter_id"] == "alice-v-acme"


def test_analyze_avatar_states_for_unverified_and_no_authority(api_with_llm):
    api = api_with_llm
    alice = _alice_with_matter(api)
    api.services._retriever = FakeRetriever(chunks=[])

    api.services._case_chain = FakeCaseChain(result=analysis_result(unverified=True))
    assert alice.post(ANALYZE, json={"case_facts": "f"}).json()["avatar_state"] == "unverified"

    api.services._case_chain = FakeCaseChain(result=analysis_result(no_authority=True))
    body = alice.post(ANALYZE, json={"case_facts": "f"}).json()
    assert body["avatar_state"] == "no_results"
    assert body["missing_evidence"] == [
        {
            "issue": "Whether the notice of termination was valid",
            "note": "No passage in this matter's documents was retrieved for this issue.",
        }
    ]


def test_analyze_without_model_key_is_503(api):
    alice = _alice_with_matter(api)
    response = alice.post(ANALYZE, json={"case_facts": "f"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_unavailable"

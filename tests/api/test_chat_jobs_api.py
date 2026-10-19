"""Background chat jobs (revision 3): the client starts a job, polls it and
reads the stage the pipeline is in. Ownership: a job is visible only to the
user who started it, on that matter. Stages are emitted through
policy_advisor.progress by fake chains here."""

import time

import pytest

from policy_advisor.progress import report_stage
from tests.api.helpers import (
    FakeCaseChain,
    FakeRagChain,
    FakeRetriever,
    analysis_result,
    chunk,
    corpus_answer,
    synthetic_docx_bytes,
)

CASE = "Acme stopped paying our invoices in March and terminated the supply contract by email."


class StagedRagChain(FakeRagChain):
    """Reports the stages the real RAGChain reports, in the same order."""

    def answer(self, question, **kwargs):
        report_stage("reading_documents")
        if kwargs.get("allow_web_fallback"):
            report_stage("searching_web")
        report_stage("writing")
        report_stage("checking_citations")
        return super().answer(question, **kwargs)


class StagedCaseChain(FakeCaseChain):
    def analyze(self, case_facts, **kwargs):
        report_stage("writing")
        report_stage("reading_documents")
        report_stage("writing")
        report_stage("checking_citations")
        return super().analyze(case_facts, **kwargs)


class BlockingRagChain(FakeRagChain):
    """Holds the job in `reading_documents` until released, so a test can
    observe a running job."""

    def __init__(self, result):
        super().__init__(result=result)
        import threading

        self.release = threading.Event()
        self.started = threading.Event()

    def answer(self, question, **kwargs):
        report_stage("reading_documents")
        self.started.set()
        assert self.release.wait(5), "test never released the chain"
        report_stage("writing")
        return super().answer(question, **kwargs)


@pytest.fixture
def fakes(api_with_llm):
    rag = StagedRagChain(result=corpus_answer())
    case = StagedCaseChain(result=analysis_result())
    api_with_llm.services._rag_chain = rag
    api_with_llm.services._case_chain = case
    api_with_llm.services._retriever = FakeRetriever(
        chunks=[chunk("Section 3", "Either party may terminate on ninety days written notice.")]
    )
    return rag, case


def _new_case(client):
    response = client.post("/api/matters", json={})
    assert response.status_code == 201
    return response.json()


def _start(client, matter_id, message, **extra):
    response = client.post(
        f"/api/matters/{matter_id}/chat/jobs", json={"message": message, **extra}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] in ("queued", "running") and body["reply"] is None
    return body["job_id"]


def _wait(client, matter_id, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/matters/{matter_id}/chat/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def _upload_ready(client, matter_id, name="supply.docx"):
    response = client.post(
        f"/api/matters/{matter_id}/documents",
        files={"file": (name, synthetic_docx_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 202, response.text
    assert client.get(f"/api/matters/{matter_id}/documents/{name}").json()["status"] == "ready"


def test_job_produces_the_same_reply_as_the_synchronous_endpoint_and_records_stages(
    api_with_llm, fakes
):
    alice = api_with_llm.login("alice")
    matter = _new_case(alice)
    _upload_ready(alice, matter["id"])

    job_id = _start(alice, matter["id"], CASE)
    done = _wait(alice, matter["id"], job_id)
    assert done["status"] == "done" and done["error"] is None
    reply = done["reply"]
    assert reply["mode"] == "analysis" and reply["role"] == "assistant"
    assert reply["analysis"]["overall_position"] == "Claimant likely succeeds."
    # Chain stages, then the API's own final assembly.
    assert done["stages"] == [
        "writing",
        "reading_documents",
        "writing",
        "checking_citations",
        "writing",
    ]
    assert done["stage"] == "writing"

    # The reply was persisted exactly like the synchronous endpoint's.
    history = alice.get(f"/api/matters/{matter['id']}/chat").json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    assert history["messages"][1]["id"] == reply["id"]
    assert alice.get(f"/api/matters/{matter['id']}").json()["title"].startswith("Acme stopped")


def test_question_job_stages_include_web_search_only_when_allowed(api_with_llm, fakes):
    rag, _ = fakes
    alice = api_with_llm.login("alice")
    matter = _new_case(alice)
    _upload_ready(alice, matter["id"])
    _wait(alice, matter["id"], _start(alice, matter["id"], CASE))  # analysis first

    off = _wait(alice, matter["id"], _start(alice, matter["id"], "When are invoices due?"))
    assert off["reply"]["mode"] == "question"
    assert off["stages"] == ["reading_documents", "writing", "checking_citations", "writing"]

    on = _wait(
        alice, matter["id"], _start(alice, matter["id"], "When are invoices due?", allow_web=True)
    )
    assert on["stages"] == [
        "reading_documents",
        "searching_web",
        "writing",
        "checking_citations",
        "writing",
    ]


def test_running_job_exposes_its_current_stage(api_with_llm):
    rag = BlockingRagChain(result=corpus_answer())
    api_with_llm.services._rag_chain = rag
    api_with_llm.services._retriever = FakeRetriever(chunks=[])
    alice = api_with_llm.login("alice")
    matter = _new_case(alice)
    _upload_ready(alice, matter["id"])
    api_with_llm.services._case_chain = StagedCaseChain(result=analysis_result())
    _wait(alice, matter["id"], _start(alice, matter["id"], CASE))

    job_id = _start(alice, matter["id"], "When are invoices due?")
    assert rag.started.wait(5)
    running = alice.get(f"/api/matters/{matter['id']}/chat/jobs/{job_id}").json()
    assert running["status"] == "running" and running["stage"] == "reading_documents"
    assert running["reply"] is None
    rag.release.set()
    done = _wait(alice, matter["id"], job_id)
    assert done["status"] == "done" and done["reply"]["mode"] == "question"


def test_job_is_only_visible_to_its_owner_on_its_matter(api_with_llm, fakes):
    alice, bob = api_with_llm.login("alice"), api_with_llm.login("bob")
    alice_case = _new_case(alice)
    bob_case = _new_case(bob)
    job_id = _start(alice, alice_case["id"], CASE)
    _wait(alice, alice_case["id"], job_id)

    for response in [
        bob.get(f"/api/matters/{alice_case['id']}/chat/jobs/{job_id}"),  # not his matter
        bob.get(f"/api/matters/{bob_case['id']}/chat/jobs/{job_id}"),  # his matter, her job
        alice.get(f"/api/matters/{alice_case['id']}/chat/jobs/does-not-exist"),
        bob.post(f"/api/matters/{alice_case['id']}/chat/jobs", json={"message": CASE}),
    ]:
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] in ("matter_not_found", "not_found")

    # The shared library: each user's job is their own even on the same matter.
    shared_job = _start(alice, "phase1-demo", "How long do I have to file a defence?")
    assert bob.get(f"/api/matters/phase1-demo/chat/jobs/{shared_job}").status_code == 404
    assert _wait(alice, "phase1-demo", shared_job)["status"] == "done"


def test_job_failure_is_reported_with_the_api_error_envelope_not_exception_text(api_with_llm):
    from policy_advisor.generation.chain import AnswerResult, LLM_UNAVAILABLE_MESSAGE

    api_with_llm.services._rag_chain = FakeRagChain(
        result=AnswerResult(answer=LLM_UNAVAILABLE_MESSAGE, retrieved=[], faithful=True)
    )
    api_with_llm.services._retriever = FakeRetriever(chunks=[])
    alice = api_with_llm.login("alice")
    matter = _new_case(alice)
    done = _wait(alice, matter["id"], _start(alice, matter["id"], CASE))
    assert done["status"] == "failed" and done["reply"] is None
    assert done["error"] == {
        "code": "llm_unavailable",
        "message": "The advisor model did not respond. Try again shortly.",
    }

    class Exploding(FakeRagChain):
        def answer(self, question, **kwargs):
            raise RuntimeError("secret path /srv/keys and token sk-ant-xyz")

    api_with_llm.services._rag_chain = Exploding(result=corpus_answer())
    done = _wait(alice, matter["id"], _start(alice, matter["id"], "again"))
    assert done["status"] == "failed"
    assert done["error"]["code"] == "internal_error"
    assert "sk-ant" not in done["error"]["message"] and "/srv" not in done["error"]["message"]


def test_starting_a_job_without_a_usable_key_is_403_before_any_job_exists(api_byok):
    alice = api_byok.login("alice")
    matter = _new_case(alice)
    response = alice.post(f"/api/matters/{matter['id']}/chat/jobs", json={"message": CASE})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "claude_key_required"

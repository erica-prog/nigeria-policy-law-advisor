"""Bring-your-own Claude key (revision 3): storing, checking, removing and
resolving per-user keys; the key never leaves the server in any form; the
shared server key is off by default; user A's key is never used for user B.
The Anthropic verification call is mocked; chains are faked."""

import json
import logging
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest

from policy_advisor.api import user_keys
from tests.api.helpers import (
    FakeCaseChain,
    FakeRagChain,
    FakeRetriever,
    analysis_result,
    corpus_answer,
    synthetic_docx_bytes,
)

KEY_URL = "/api/me/claude-key"
ALICE_KEY = "sk-ant-api03-alice-unit-test-key-0000000000000000000000abcd"
BOB_KEY = "sk-ant-api03-bob-unit-test-key-00000000000000000000000wxyz"
MODULE = "policy_advisor.api.user_keys"


def _anthropic_response(status: int) -> httpx.Response:
    request = httpx.Request("GET", "https://api.anthropic.com/v1/models")
    return httpx.Response(status, request=request, json={"error": {"message": "x"}})


def _client_ok() -> MagicMock:
    return MagicMock()


def _client_raising(exc: Exception) -> MagicMock:
    client = MagicMock()
    client.models.list.side_effect = exc
    return client


def _put(client, key):
    with patch(f"{MODULE}.anthropic.Anthropic", return_value=_client_ok()) as factory:
        response = client.put(KEY_URL, json={"api_key": key})
    return response, factory


def _new_case(client):
    response = client.post("/api/matters", json={})
    assert response.status_code == 201
    return response.json()


# ---- GET / PUT / DELETE ----


def test_no_key_by_default_and_me_reports_not_ready(api_byok):
    alice = api_byok.login("alice")
    assert alice.get(KEY_URL).json() == {
        "configured": False,
        "last4": None,
        "source": None,
        "advisor_ready": False,
    }
    me = alice.get("/api/me").json()
    assert me["advisor_ready"] is False and me["key_source"] is None
    # The server has a key, but health is the server-level view only.
    assert api_byok.client.get("/api/health").json() == {
        "status": "ok",
        "llm_configured": True,
        "shared_key_allowed": False,
    }


def test_put_valid_key_verifies_with_anthropic_stores_and_reports_last4(api_byok):
    alice = api_byok.login("alice")
    response, factory = _put(alice, ALICE_KEY)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "configured": True,
        "last4": "abcd",
        "source": "user",
        "advisor_ready": True,
    }
    # The cheapest authenticated call, made with the candidate key.
    factory.assert_called_once()
    assert factory.call_args.kwargs["api_key"] == ALICE_KEY
    factory.return_value.models.list.assert_called_once_with(limit=1)

    me = alice.get("/api/me").json()
    assert me["advisor_ready"] is True and me["key_source"] == "user"
    assert alice.get(KEY_URL).json()["last4"] == "abcd"


def test_put_malformed_key_is_rejected_without_calling_anthropic(api_byok):
    alice = api_byok.login("alice")
    for bad in ["not-a-key", "sk-ant-short", "sk-ant-" + "x" * 30 + " with space", ""]:
        with patch(f"{MODULE}.anthropic.Anthropic") as factory:
            response = alice.put(KEY_URL, json={"api_key": bad})
        assert response.status_code in (400, 422), (bad, response.text)
        factory.assert_not_called()
    response = alice.put(KEY_URL, json={"api_key": "not-a-key"})
    assert response.json()["error"]["code"] == "invalid_api_key"
    assert alice.get(KEY_URL).json()["configured"] is False


def test_put_key_anthropic_rejects_is_400_invalid_api_key(api_byok):
    alice = api_byok.login("alice")
    exc = anthropic.AuthenticationError(
        "invalid x-api-key", response=_anthropic_response(401), body=None
    )
    with patch(f"{MODULE}.anthropic.Anthropic", return_value=_client_raising(exc)):
        response = alice.put(KEY_URL, json={"api_key": ALICE_KEY})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_api_key"
    assert "Anthropic did not accept" in response.json()["error"]["message"]
    assert alice.get(KEY_URL).json()["configured"] is False, "a rejected key must not be stored"


def test_put_key_when_anthropic_is_unreachable_is_502(api_byok):
    alice = api_byok.login("alice")
    exc = anthropic.APIConnectionError(
        request=httpx.Request("GET", "https://api.anthropic.com/v1/models")
    )
    with patch(f"{MODULE}.anthropic.Anthropic", return_value=_client_raising(exc)):
        response = alice.put(KEY_URL, json={"api_key": ALICE_KEY})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "anthropic_unreachable"
    assert alice.get(KEY_URL).json()["configured"] is False


def test_put_is_rate_limited_per_user(api_byok):
    alice, bob = api_byok.login("alice"), api_byok.login("bob")
    exc = anthropic.AuthenticationError("bad", response=_anthropic_response(401), body=None)
    with patch(f"{MODULE}.anthropic.Anthropic", return_value=_client_raising(exc)):
        codes = [
            alice.put(KEY_URL, json={"api_key": ALICE_KEY}).status_code
            for _ in range(user_keys.RATE_LIMIT_ATTEMPTS + 2)
        ]
    assert codes[: user_keys.RATE_LIMIT_ATTEMPTS] == [400] * user_keys.RATE_LIMIT_ATTEMPTS
    assert codes[user_keys.RATE_LIMIT_ATTEMPTS :] == [429, 429]
    blocked = alice.put(KEY_URL, json={"api_key": ALICE_KEY})
    assert blocked.json()["error"]["code"] == "rate_limited"
    # Bob is not affected by Alice's attempts.
    response, _ = _put(bob, BOB_KEY)
    assert response.status_code == 200


def test_delete_removes_the_key_and_is_idempotent(api_byok):
    alice = api_byok.login("alice")
    assert _put(alice, ALICE_KEY)[0].status_code == 200
    assert alice.delete(KEY_URL).status_code == 204
    assert alice.get(KEY_URL).json() == {
        "configured": False,
        "last4": None,
        "source": None,
        "advisor_ready": False,
    }
    assert alice.delete(KEY_URL).status_code == 204


def test_replacing_the_key_overwrites_the_previous_one(api_byok):
    alice = api_byok.login("alice")
    assert _put(alice, ALICE_KEY)[0].status_code == 200
    assert _put(alice, BOB_KEY)[0].status_code == 200
    assert alice.get(KEY_URL).json()["last4"] == "wxyz"
    assert user_keys.get_user_key("alice") == BOB_KEY


def test_keys_cannot_be_stored_on_the_insecure_default_cookie_key(api_byok, monkeypatch):
    from policy_advisor.config import get_settings

    monkeypatch.delenv("AUTH_COOKIE_KEY")
    get_settings.cache_clear()
    alice = api_byok.login("alice")
    response, factory = _put(alice, ALICE_KEY)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "server_not_configured"
    assert "AUTH_COOKIE_KEY" in response.json()["error"]["message"]
    assert not user_keys.user_keys_path().exists()


# ---- the key never leaves the server ----


@pytest.fixture
def capture_policy_advisor_logs(caplog):
    """The project's loggers do not propagate to root, so attach caplog's
    handler to each of them for the duration of the test."""
    caplog.set_level(logging.DEBUG)
    attached = [
        logger
        for name, logger in logging.Logger.manager.loggerDict.items()
        if name.startswith("policy_advisor") and isinstance(logger, logging.Logger)
    ]
    for logger in attached:
        logger.addHandler(caplog.handler)
    yield caplog
    for logger in attached:
        logger.removeHandler(caplog.handler)


def test_key_is_encrypted_at_rest_and_never_in_a_response_or_log(
    api_byok, capture_policy_advisor_logs
):
    caplog = capture_policy_advisor_logs
    alice = api_byok.login("alice")
    bodies = []
    response, _ = _put(alice, ALICE_KEY)
    bodies.append(response.text)
    bodies.append(alice.get(KEY_URL).text)
    bodies.append(alice.get("/api/me").text)

    path = api_byok.root / "auth" / "user_keys.json"
    assert path.exists()
    raw = path.read_text()
    assert ALICE_KEY not in raw
    assert ALICE_KEY[7:-4] not in raw, "no partial plaintext either"
    stored = json.loads(raw)["users"]["alice"]
    assert stored["last4"] == "abcd" and stored["token"].startswith("gAAAA")  # Fernet token
    assert (path.stat().st_mode & 0o777) == 0o600

    # Round trip through the store, using the fake chain to prove the request
    # path sees the real key while no response body does.
    api_byok.services._rag_chain = FakeRagChain(result=corpus_answer())
    api_byok.services._retriever = FakeRetriever(chunks=[])
    matter = _new_case(alice)
    reply = alice.post(
        f"/api/matters/{matter['id']}/chat", json={"message": "When are invoices due?"}
    )
    assert reply.status_code == 200, reply.text
    bodies.append(reply.text)
    bodies.append(alice.get(f"/api/matters/{matter['id']}/chat").text)
    bodies.append(alice.get("/api/matters").text)

    # A wrong key is rejected; its error must not echo it either.
    exc = anthropic.AuthenticationError("bad", response=_anthropic_response(401), body=None)
    with patch(f"{MODULE}.anthropic.Anthropic", return_value=_client_raising(exc)):
        bodies.append(alice.put(KEY_URL, json={"api_key": BOB_KEY}).text)

    for body in bodies:
        assert ALICE_KEY not in body and BOB_KEY not in body
        assert "sk-ant-" not in body
    log_text = "\n".join(
        r.getMessage() + json.dumps(getattr(r, "fields", {}), default=str) for r in caplog.records
    )
    assert caplog.records, "expected log records from the API"
    assert ALICE_KEY not in log_text and BOB_KEY not in log_text and "sk-ant-" not in log_text


def test_stored_key_survives_a_restart_but_not_a_rotated_cookie_key(api_byok, monkeypatch):
    from policy_advisor.config import get_settings

    alice = api_byok.login("alice")
    assert _put(alice, ALICE_KEY)[0].status_code == 200
    assert user_keys.get_user_key("alice") == ALICE_KEY  # same process, same secret

    monkeypatch.setenv("AUTH_COOKIE_KEY", "a-different-cookie-key")
    get_settings.cache_clear()
    assert user_keys.get_user_key("alice") is None, (
        "wrong secret must read as 'no key', never garbage"
    )
    assert user_keys.get_user_key_info("alice") is not None


# ---- resolution: whose key does a request run on? ----


def test_chat_is_403_without_a_key_and_works_once_the_user_adds_one(api_byok):
    alice = api_byok.login("alice")
    api_byok.services._rag_chain = FakeRagChain(result=corpus_answer())
    api_byok.services._case_chain = FakeCaseChain(result=analysis_result())
    api_byok.services._retriever = FakeRetriever(chunks=[])
    matter = _new_case(alice)
    chat = f"/api/matters/{matter['id']}/chat"

    denied = alice.post(chat, json={"message": "When are invoices due?"})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "claude_key_required"
    assert api_byok.services._rag_chain.calls == []
    # Uploads and history keep working without a key.
    upload = alice.post(
        f"/api/matters/{matter['id']}/documents",
        files={"file": ("supply.docx", synthetic_docx_bytes(), "application/octet-stream")},
    )
    assert upload.status_code == 202
    assert (
        alice.get(f"/api/matters/{matter['id']}/documents/supply.docx").json()["status"] == "ready"
    )
    assert alice.get(chat).status_code == 200

    assert _put(alice, ALICE_KEY)[0].status_code == 200
    ok = alice.post(chat, json={"message": "When are invoices due?"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["mode"] == "analysis"  # first message with a document
    assert len(api_byok.services._case_chain.calls) == 1
    follow_up = alice.post(chat, json={"message": "And the interest rate?"})
    assert follow_up.status_code == 200 and follow_up.json()["mode"] == "question"
    assert len(api_byok.services._rag_chain.calls) == 1


def test_shared_server_key_is_off_by_default(api_byok):
    # Server key configured, sharing off (the default) -> not ready.
    alice = api_byok.login("alice")
    assert alice.get("/api/me").json() == {
        "username": "alice",
        "display_name": "Alice",
        "advisor_ready": False,
        "key_source": None,
    }
    assert alice.get(KEY_URL).json()["source"] is None


def test_shared_server_key_when_enabled_is_reported_as_shared(api_with_llm):
    alice = api_with_llm.login("alice")
    me = alice.get("/api/me").json()
    assert me["advisor_ready"] is True and me["key_source"] == "shared"
    assert alice.get(KEY_URL).json() == {
        "configured": False,
        "last4": None,
        "source": "shared",
        "advisor_ready": True,
    }
    assert api_with_llm.client.get("/api/health").json()["shared_key_allowed"] is True
    # A user's own key takes precedence over the shared one.
    assert _put(alice, ALICE_KEY)[0].status_code == 200
    assert alice.get("/api/me").json()["key_source"] == "user"
    assert user_keys.resolve_anthropic_key("alice") == ALICE_KEY
    assert user_keys.resolve_anthropic_key("bob") == "fake-key-for-tests"


def test_each_users_chain_is_built_with_their_own_key_never_anothers(api_byok):
    services = api_byok.services
    built: list[str] = []
    rag_by_key: dict[str, FakeRagChain] = {}
    case_by_key: dict[str, FakeCaseChain] = {}

    def rag_factory(api_key):
        built.append(api_key)
        return rag_by_key.setdefault(api_key, FakeRagChain(result=corpus_answer()))

    def case_factory(api_key):
        built.append(api_key)
        return case_by_key.setdefault(api_key, FakeCaseChain(result=analysis_result()))

    services.rag_chain_factory = rag_factory
    services.case_chain_factory = case_factory
    services._retriever = FakeRetriever(chunks=[])

    alice, bob = api_byok.login("alice"), api_byok.login("bob")
    assert _put(alice, ALICE_KEY)[0].status_code == 200
    assert _put(bob, BOB_KEY)[0].status_code == 200
    alice_case, bob_case = _new_case(alice), _new_case(bob)

    assert (
        alice.post(f"/api/matters/{alice_case['id']}/ask", json={"question": "q"}).status_code
        == 200
    )
    assert bob.post(f"/api/matters/{bob_case['id']}/ask", json={"question": "q"}).status_code == 200
    assert (
        bob.post(f"/api/matters/{bob_case['id']}/analyze", json={"case_facts": "f"}).status_code
        == 200
    )
    assert (
        alice.post(f"/api/matters/{alice_case['id']}/chat", json={"message": "m"}).status_code
        == 200
    )

    assert set(built) == {ALICE_KEY, BOB_KEY}
    assert "server-key-never-shared" not in built, (
        "the server key must never be used when sharing is off"
    )
    assert len(rag_by_key[ALICE_KEY].calls) == 2 and len(rag_by_key[BOB_KEY].calls) == 1
    assert len(case_by_key[BOB_KEY].calls) == 1 and ALICE_KEY not in case_by_key
    # Chains are cached per key: a second request builds nothing new.
    before = len(built)
    assert (
        bob.post(f"/api/matters/{bob_case['id']}/ask", json={"question": "q2"}).status_code == 200
    )
    assert len(built) == before

    # Bob removes his key: he is locked out immediately; Alice is unaffected.
    assert bob.delete(KEY_URL).status_code == 204
    assert bob.post(f"/api/matters/{bob_case['id']}/ask", json={"question": "q"}).status_code == 403
    assert (
        alice.post(f"/api/matters/{alice_case['id']}/ask", json={"question": "q"}).status_code
        == 200
    )


def test_upload_translation_runs_on_the_uploaders_key_or_is_skipped(api_byok, monkeypatch):
    """Web uploads index the original text and never translate, so a document
    becomes reviewable without a Claude call per chunk. The uploader's key is
    still the one recorded for the job; the server key is never used."""
    seen: list[dict] = []

    def fake_add_document(matter_id, file_path, jurisdiction=None, *, api_key=None, translate=None):
        seen.append({"api_key": api_key, "translate": translate})
        return 3

    # jobs.run imports add_document at call time, so patching the module attribute is enough.
    monkeypatch.setattr("policy_advisor.ingestion.ingest_document.add_document", fake_add_document)

    alice = api_byok.login("alice")
    matter = _new_case(alice)

    def upload(name):
        # TestClient runs BackgroundTasks before returning, so `seen` is filled here.
        response = alice.post(
            f"/api/matters/{matter['id']}/documents",
            files={"file": (name, synthetic_docx_bytes(), "application/octet-stream")},
        )
        assert response.status_code == 202, response.text

    upload("before-key.docx")
    assert seen[-1] == {"api_key": None, "translate": False}

    assert _put(alice, ALICE_KEY)[0].status_code == 200
    upload("after-key.docx")
    assert seen[-1] == {"api_key": ALICE_KEY, "translate": False}


@pytest.mark.parametrize(
    "candidate",
    ["sk-ant-api03-" + "a" * 40, "sk-ant-" + "Z9_-" * 10],
)
def test_validate_key_format_accepts_plausible_keys(candidate):
    assert user_keys.validate_key_format(f"  {candidate}\n") == candidate

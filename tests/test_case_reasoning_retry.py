"""Tests the resolved retry-then-flag contract (CLAUDE-2.md capability 2):
one corrective retry on a failed citation check, then a low-confidence flag
if still unresolved - never a silent pass-through. Mocks the LLM/retriever so
this runs fast and offline, without a real API key or built index."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage

from policy_advisor.generation.case_reasoning import (
    ARGUMENT_MAX_TOKENS,
    ARGUMENT_TIMEOUT_SECONDS,
    CaseReasoningChain,
)
from policy_advisor.generation.case_reasoning_models import (
    Argument,
    Authority,
    IssueArguments,
    StepCheckOutcome,
)
from policy_advisor.retrieval.hybrid_retriever import RetrievedChunk

JUDGE_CHECK_PATH = "policy_advisor.generation.case_reasoning.judge_check"


def _issue_args(locator: str) -> IssueArguments:
    return IssueArguments(
        issue="some issue",
        arguments=[Argument(side="claimant", summary="s", supporting_authorities=[Authority(locator=locator, relevance="r")])],
        assessment="z",
    )


def _chain_with_retrieved(chunks: list[RetrievedChunk]) -> CaseReasoningChain:
    chain = object.__new__(CaseReasoningChain)
    chain._retriever = MagicMock()
    chain._retriever.retrieve.return_value = chunks
    # These chunks are the premise of every test here, so keep the relevance
    # gate out of the way - it has its own tests in test_relevance_gate.py.
    chain._retriever.needs_relevance_adjudication.return_value = False
    chain._logger = MagicMock()
    chain._settings = MagicMock(retrieval_top_k=8)
    chain._llm = MagicMock()
    return chain


def _chunk(locator: str) -> RetrievedChunk:
    return RetrievedChunk(text="authority text", metadata={"locator": locator, "chunk_id": locator})


def test_no_retry_needed_when_first_attempt_is_clean():
    chain = _chain_with_retrieved([_chunk("Order 1 Rule 1")])
    clean = _issue_args("Order 1 Rule 1")

    with patch.object(chain, "_generate_issue_arguments", return_value=clean) as gen, patch(
        JUDGE_CHECK_PATH, return_value=StepCheckOutcome(faithful=True)
    ):
        result = chain._analyze_issue("facts", "some issue", "matter-a", None, "English")

    assert gen.call_count == 1
    assert result.unverified is False
    assert result.confidence == "strongly supported"


def test_one_retry_succeeds_and_clears_the_flag():
    chain = _chain_with_retrieved([_chunk("Order 1 Rule 1")])
    bad_then_good = [_issue_args("Order 99 Rule 99"), _issue_args("Order 1 Rule 1")]

    with patch.object(chain, "_generate_issue_arguments", side_effect=bad_then_good) as gen, patch(
        JUDGE_CHECK_PATH, return_value=StepCheckOutcome(faithful=True)
    ):
        result = chain._analyze_issue("facts", "some issue", "matter-a", None, "English")

    assert gen.call_count == 2
    assert result.unverified is False


def test_still_unsupported_after_one_retry_is_flagged_not_retried_again():
    chain = _chain_with_retrieved([_chunk("Order 1 Rule 1")])
    always_bad = _issue_args("Order 99 Rule 99")

    with patch.object(chain, "_generate_issue_arguments", return_value=always_bad) as gen, patch(
        JUDGE_CHECK_PATH, return_value=StepCheckOutcome(faithful=True)
    ):
        result = chain._analyze_issue("facts", "some issue", "matter-a", None, "English")

    assert gen.call_count == 2  # exactly one retry, not an unbounded loop
    assert result.unverified is True
    assert result.confidence == "unverified - flagged by faithfulness check"


def test_judge_flag_marks_unverified_even_with_clean_citations():
    chain = _chain_with_retrieved([_chunk("Order 1 Rule 1")])
    clean = _issue_args("Order 1 Rule 1")

    with patch.object(chain, "_generate_issue_arguments", return_value=clean) as gen, patch(
        JUDGE_CHECK_PATH, return_value=StepCheckOutcome(faithful=False, judge_flagged=True, judge_notes="overreach")
    ):
        result = chain._analyze_issue("facts", "some issue", "matter-a", None, "English")

    assert gen.call_count == 1  # judge flag doesn't trigger a generation retry
    assert result.unverified is True


def test_the_authorities_available_at_analysis_time_are_recorded():
    # Retrieval cannot be replayed afterwards unless every parameter matches,
    # so anything auditing whether a citation was supported has to read what
    # the chain actually had rather than re-running the search. CI caught this
    # the hard way: a checker that re-retrieved without the jurisdiction filter
    # reported violations against authorities the chain never saw.
    chain = _chain_with_retrieved([_chunk("Order 1 Rule 1"), _chunk("Order 2 Rule 5")])

    with patch.object(chain, "_generate_issue_arguments", return_value=_issue_args("Order 1 Rule 1")), patch(
        JUDGE_CHECK_PATH, return_value=StepCheckOutcome(faithful=True)
    ):
        result = chain._analyze_issue("facts", "some issue", "matter-a", None, "English")

    assert result.retrieved_locators == ["Order 1 Rule 1", "Order 2 Rule 5"]


def _raw(stop_reason: str):
    return SimpleNamespace(response_metadata={"stop_reason": stop_reason})


def _argument_chain() -> tuple[CaseReasoningChain, MagicMock]:
    chain = _chain_with_retrieved([])
    structured = MagicMock()
    chain._argument_llm = MagicMock()
    chain._argument_llm.with_structured_output.return_value = structured
    return chain, structured


def test_issue_arguments_use_a_budget_large_enough_for_the_assessment(monkeypatch):
    # case-reasoning-invariants died in _generate_issue_arguments: the shared
    # 2048-token client finished the arguments and never emitted `assessment`,
    # and the retry policy repeated that truncated call until the job failed.
    created = []

    class _FakeLLM:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr("policy_advisor.generation.case_reasoning.ChatAnthropic", _FakeLLM)
    monkeypatch.setattr("policy_advisor.generation.case_reasoning.HybridRetriever", MagicMock)
    monkeypatch.setattr(
        "policy_advisor.generation.case_reasoning.get_logger", lambda *_args, **_kwargs: MagicMock()
    )
    settings = MagicMock()
    settings.anthropic_api_key.get_secret_value.return_value = "test-key"
    monkeypatch.setattr("policy_advisor.generation.case_reasoning.get_settings", lambda: settings)

    CaseReasoningChain()

    argument_client = next(client for client in created if client["max_tokens"] == ARGUMENT_MAX_TOKENS)
    assert argument_client["default_request_timeout"] == ARGUMENT_TIMEOUT_SECONDS
    assert ARGUMENT_MAX_TOKENS > 2048
    shared = [client for client in created if client["max_tokens"] == 2048]
    assert shared  # shorter calls keep the original budget


def test_complete_issue_arguments_are_returned_without_a_shorten_retry():
    chain, structured = _argument_chain()
    good = _issue_args("Order 1 Rule 1")
    structured.invoke.return_value = {"parsed": good, "parsing_error": None, "raw": _raw("tool_use")}

    assert chain._generate_issue_arguments("facts", "some issue", "context", "English") is good
    structured.invoke.assert_called_once()
    chain._argument_llm.with_structured_output.assert_called_once_with(IssueArguments, include_raw=True)


def test_truncated_issue_arguments_are_requested_once_more_briefly():
    chain, structured = _argument_chain()
    good = _issue_args("Order 1 Rule 1")
    structured.invoke.side_effect = [
        {"parsed": None, "parsing_error": ValueError("assessment missing"), "raw": _raw("max_tokens")},
        {"parsed": good, "parsing_error": None, "raw": _raw("tool_use")},
    ]

    assert chain._generate_issue_arguments("facts", "some issue", "context", "English") is good
    assert structured.invoke.call_count == 2
    second_messages = structured.invoke.call_args_list[1].args[0]
    corrections = [message.content for message in second_messages if isinstance(message, HumanMessage)]
    assert any("assessment" in content for content in corrections)


def test_a_second_truncation_is_not_retried_again():
    chain, structured = _argument_chain()
    structured.invoke.return_value = {
        "parsed": None,
        "parsing_error": ValueError("assessment missing"),
        "raw": _raw("max_tokens"),
    }

    try:
        chain._generate_issue_arguments("facts", "some issue", "context", "English")
    except Exception as exc:
        assert type(exc).__name__ == "OutputTruncated"
    else:
        raise AssertionError("expected the second truncation to propagate")

    # Once at the original length, once shorter. Repeating the identical call
    # is the loop CI was stuck in.
    assert structured.invoke.call_count == 2


def test_a_malformed_tool_call_is_still_retried():
    chain, structured = _argument_chain()
    structured.invoke.return_value = {
        "parsed": None,
        "parsing_error": ValueError("not json"),
        "raw": _raw("tool_use"),
    }

    try:
        chain._generate_issue_arguments("facts", "some issue", "context", "English")
    except ValueError as exc:
        assert str(exc) == "not json"
    else:
        raise AssertionError("expected the parse error to propagate")

    assert structured.invoke.call_count == 3


def test_no_retrieved_authorities_yields_no_authority_confidence():
    chain = _chain_with_retrieved([])
    empty = IssueArguments(issue="some issue", arguments=[], assessment="nothing found in context")

    with patch.object(chain, "_generate_issue_arguments", return_value=empty), patch(
        JUDGE_CHECK_PATH, return_value=StepCheckOutcome(faithful=True)
    ):
        result = chain._analyze_issue("facts", "some issue", "matter-a", None, "English")

    assert result.confidence == "no authority found in corpus"

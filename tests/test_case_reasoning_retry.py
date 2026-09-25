"""Tests the resolved retry-then-flag contract (CLAUDE-2.md capability 2):
one corrective retry on a failed citation check, then a low-confidence flag
if still unresolved - never a silent pass-through. Mocks the LLM/retriever so
this runs fast and offline, without a real API key or built index."""

from unittest.mock import MagicMock, patch

from policy_advisor.generation.case_reasoning import CaseReasoningChain
from policy_advisor.generation.case_reasoning_models import Argument, Authority, IssueArguments, StepCheckOutcome
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


def test_no_retrieved_authorities_yields_no_authority_confidence():
    chain = _chain_with_retrieved([])
    empty = IssueArguments(issue="some issue", arguments=[], assessment="nothing found in context")

    with patch.object(chain, "_generate_issue_arguments", return_value=empty), patch(
        JUDGE_CHECK_PATH, return_value=StepCheckOutcome(faithful=True)
    ):
        result = chain._analyze_issue("facts", "some issue", "matter-a", None, "English")

    assert result.confidence == "no authority found in corpus"

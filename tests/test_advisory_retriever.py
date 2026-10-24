"""Regression for the AttributeError that broke the case-reasoning-invariants
CI job on pull request 12: advisory mode reads `case_chain.retriever`, but the
chain only had the private `_retriever`. The advisory tests mock the case chain
with MagicMock, which invents any attribute, so they could not catch it. This
test builds the real chain (offline: no Claude call is made at construction)."""

from policy_advisor.generation.advisory import AdvisoryChain
from policy_advisor.generation.case_reasoning import CaseReasoningChain
from policy_advisor.generation.case_reasoning_models import (
    CONFIDENCE_STRONG,
    Argument,
    Authority,
    CaseReasoningResult,
    IssueAnalysis,
)
from policy_advisor.generation.chain import RAGChain
from tests.api.helpers import FakeRetriever, chunk


def _case_chain(retriever):
    return CaseReasoningChain(api_key="test-key-never-used", retriever=retriever)


def test_case_reasoning_chain_exposes_the_retriever_it_was_built_with():
    fake = FakeRetriever(chunks=[])
    assert _case_chain(fake).retriever is fake


def test_rag_chain_exposes_the_retriever_it_was_built_with():
    fake = FakeRetriever(chunks=[])
    assert RAGChain(api_key="test-key-never-used", retriever=fake).retriever is fake


def test_advisory_authority_chunks_come_from_the_real_case_chain_retriever():
    passage = chunk(
        "Order 5 Rule 3", "A defence shall be filed within 30 days.", doc_type="statute"
    )
    advisory = AdvisoryChain(case_chain=_case_chain(FakeRetriever(chunks=[passage])))
    analysis = CaseReasoningResult(
        case_facts="facts",
        issues=[
            IssueAnalysis(
                issue="whether the defence was in time",
                arguments=[
                    Argument(
                        side="claimant",
                        summary="s",
                        supporting_authorities=[Authority(locator="Order 5 Rule 3", relevance="r")],
                    )
                ],
                assessment="a",
                confidence=CONFIDENCE_STRONG,
            )
        ],
        overall_position="p",
        disclaimer="d",
    )
    assert advisory._authority_chunks(analysis, matter_id="m") == [passage]

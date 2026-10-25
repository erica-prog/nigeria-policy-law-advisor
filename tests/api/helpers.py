"""Synthetic fixtures and fake pipeline objects for the API tests. Nothing
here resembles a real client document (AGENTS.md rule 7)."""

import io
from dataclasses import dataclass, field

from docx import Document as DocxDocument

from policy_advisor.generation.case_reasoning_models import (
    Argument,
    Authority,
    CaseReasoningResult,
    IssueAnalysis,
)
from policy_advisor.agents.research import ResearchResult
from policy_advisor.api.schemas import SourceReference
from policy_advisor.generation.chain import AnswerResult
from policy_advisor.progress import report_stage
from policy_advisor.generation.web_search import WebSearchCitation
from policy_advisor.retrieval.hybrid_retriever import RetrievedChunk

SYNTHETIC_PARAGRAPHS = [
    "SUPPLY AGREEMENT between Foxglove Trading Ltd (the Supplier) and Acme Fabrication "
    "Ltd (the Buyer), made on 3 March 2025 at Ikeja, Lagos State.",
    "Clause 4. Payment terms. The Buyer shall pay each invoice within thirty days of "
    "delivery. Late payment attracts interest at two per cent per month.",
    "Clause 9. Termination. Either party may terminate on ninety days written notice "
    "delivered to the registered office of the other party.",
    "Clause 12. Governing law. This agreement is governed by the laws of the Federal "
    "Republic of Nigeria and disputes go to the High Court of Lagos State.",
]


def synthetic_docx_bytes(paragraphs: list[str] | None = None) -> bytes:
    document = DocxDocument()
    for paragraph in paragraphs or SYNTHETIC_PARAGRAPHS:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def chunk(locator: str, text: str, doc_type: str = "generic", document: str = "supply.docx"):
    return RetrievedChunk(
        text=text,
        metadata={
            "chunk_id": f"m::{document}::{locator}",
            "locator": locator,
            "source_document": document,
            "doc_type": doc_type,
            "page": 1,
            "jurisdiction": "",
            "matter_id": "m",
        },
        fused_score=0.9,
    )


@dataclass
class FakeRagChain:
    result: AnswerResult
    calls: list[dict] = field(default_factory=list)

    def answer(self, question, **kwargs):
        self.calls.append({"question": question, **kwargs})
        return self.result

    def invalidate_matter(self, matter_id):
        pass


@dataclass
class FakeCaseChain:
    result: CaseReasoningResult
    calls: list[dict] = field(default_factory=list)

    def analyze(self, case_facts, **kwargs):
        self.calls.append({"case_facts": case_facts, **kwargs})
        return self.result

    def invalidate_matter(self, matter_id):
        pass


@dataclass
class FakeRetriever:
    chunks: list[RetrievedChunk]

    def retrieve(self, query, top_k, matter_id, jurisdiction=None):
        return self.chunks

    def invalidate_matter(self, matter_id):
        pass


def corpus_answer(faithful: bool = True) -> AnswerResult:
    chunks = [
        chunk("Section 2", "The Buyer shall pay each invoice within thirty days of delivery."),
        chunk(
            "Order 5 Rule 3",
            "A defence shall be filed within 30 days.",
            doc_type="statute",
            document="rules.pdf",
        ),
    ]
    answer = "Invoices are due within thirty days [Source: Section 2]; see also [Source: Order 5 Rule 3(1)]."
    unsupported: list[str] = []
    if not faithful:
        answer += " And [Source: Order 99 Rule 1]."
        unsupported = ["Order 99 Rule 1"]
    return AnswerResult(
        answer=answer,
        retrieved=chunks,
        faithful=faithful,
        unsupported_citations=unsupported,
        usage={"input_tokens": 10, "output_tokens": 5},
        source="corpus",
    )


def web_answer() -> AnswerResult:
    return AnswerResult(
        answer="According to the NDPC, consent must be freely given.",
        retrieved=[],
        faithful=True,
        source="web",
        web_citations=[WebSearchCitation(url="https://ndpc.gov.ng/act", title="NDPA 2023")],
    )


def not_found_answer() -> AnswerResult:
    return AnswerResult(
        answer="I couldn't find anything relevant.", retrieved=[], faithful=True, source="none"
    )


def analysis_result(unverified: bool = False, no_authority: bool = False) -> CaseReasoningResult:
    if no_authority:
        confidence = "no authority found in corpus"
    elif unverified:
        confidence = "unverified - flagged by faithfulness check"
    else:
        confidence = "strongly supported"
    issue = IssueAnalysis(
        issue="Whether the notice of termination was valid",
        arguments=[
            Argument(
                side="claimant",
                summary="Ninety days notice was required.",
                supporting_authorities=[
                    Authority(locator="Section 3", relevance="sets the notice period")
                ],
            ),
            Argument(
                side="respondent",
                summary="Notice was waived by conduct.",
                supporting_authorities=[
                    Authority(locator="Order 77 Rule 1", relevance="not retrieved")
                ],
            ),
        ],
        assessment="The claimant is better supported.",
        confidence=confidence,
        unverified=unverified,
    )
    return CaseReasoningResult(
        case_facts="facts",
        issues=[issue],
        overall_position="Claimant likely succeeds.",
        disclaimer="Research aid only.",
    )


def research_result(*, citations: bool = True, faithful: bool = True) -> ResearchResult:
    """What the research agent (docs/28) returns: mixed provenance, each
    citation labelled, plus the audit run id and budget state."""
    chunks = [
        chunk("Clause 4", "The Buyer shall pay each invoice within thirty days of delivery."),
        chunk(
            "Order 5 Rule 3",
            "A defence shall be filed within 30 days.",
            doc_type="statute",
            document="rules.pdf",
        ),
    ]
    refs = [
        SourceReference(
            id="c1",
            kind="evidence",
            trust_kind="evidence",
            tier="matter",
            document="supply.docx",
            locator="Clause 4",
            cited_as="Clause 4",
            page=1,
            quote=chunks[0].text,
        ),
        SourceReference(
            id="c2",
            kind="authority",
            trust_kind="authority",
            tier="authority",
            document="rules.pdf",
            locator="Order 5 Rule 3",
            cited_as="Order 5 Rule 3",
            page=1,
            quote=chunks[1].text,
        ),
        SourceReference(
            id="s3",
            kind="web",
            trust_kind="web",
            document="NDPA 2023",
            cited_as="https://ndpc.gov.ng/act",
            url="https://ndpc.gov.ng/act",
            doc_type="web",
        ),
    ]
    return ResearchResult(
        answer="Invoices are due in thirty days [Source: Clause 4]; a defence has 30 days [Source: Order 5 Rule 3]; see also [Source: https://ndpc.gov.ng/act].",
        faithful=faithful,
        unsupported_citations=[] if faithful else ["Order 99 Rule 1"],
        citations=refs if citations else [],
        retrieved=chunks if citations else [],
        run_id="run-fake-1",
        tool_calls=3,
        budget_exhausted=False,
        usage={"input_tokens": 12, "output_tokens": 6},
    )


@dataclass
class FakeResearchAgent:
    result: ResearchResult
    calls: list[dict] = field(default_factory=list)

    def run(self, question, **kwargs):
        self.calls.append({"question": question, **kwargs})
        report_stage("researching")
        report_stage("writing")
        return self.result

    def invalidate_matter(self, matter_id):
        pass

"""Second stage of the relevance gate: decide whether borderline retrieval
results actually address the question.

Why this exists rather than a single distance threshold. Calibrating against
eval/golden_set.json (see eval/calibrate_relevance_floor.py) measured a gap of
0.0007 in cosine distance between the hardest question the corpus *does*
answer and the easiest one it does not. The two populations are separable on
that data and meaningless apart in practice - a gap that small is smaller than
the variation from rephrasing the same question, so a threshold tuned to it
would be fitted to twenty-one questions rather than to the corpus.

Both misclassifications matter here and they are not symmetric. Treating
irrelevant chunks as relevant is what docs/11 traced: the lawyer gets a
confident answer assembled from whatever the matter happened to contain, and
the official-sources fallback never fires. Treating relevant chunks as
irrelevant is worse still - it hides an answer their own documents contained
behind an unverified web result.

So the middle band buys a judgement instead of guessing: one small Claude call
that sees the question and the passages and answers the only question distance
was standing in for. Roughly half of golden-set queries land in the band.
"""

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from policy_advisor.llm_retry import call_with_retry
from policy_advisor.retrieval.hybrid_retriever import RetrievedChunk

# Only the first part of each passage is sent: this decides topical relevance,
# not the answer itself, and full chunks would make the check cost as much as
# the generation it is meant to gate.
PASSAGE_PREVIEW_CHARS = 600

RELEVANCE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are deciding whether a set of retrieved passages is on-topic for a lawyer's "
            "question. You are NOT answering the question, and you are NOT judging whether the "
            "passages settle it.\n\n"
            "Answer relevant=true if at least one passage is about the subject matter the question "
            "asks about, even if it only partly addresses it - a partial answer is still the "
            "lawyer's own document and they are entitled to see it.\n\n"
            "Answer relevant=false if the passages are about some other area of law or procedure "
            "and merely share vocabulary with the question. This corpus is civil procedure rules "
            "and judgments; questions about unrelated fields will still retrieve superficially "
            "similar text, and that is exactly what you are here to catch.",
        ),
        ("human", "Question:\n{question}\n\nRetrieved passages:\n{passages}"),
    ]
)


class RelevanceVerdict(BaseModel):
    relevant: bool = Field(description="True if at least one passage is on-topic for the question")
    reason: str = Field(description="One sentence explaining the decision")


def _format_passages(chunks: list[RetrievedChunk]) -> str:
    return "\n\n".join(
        f"[{chunk.metadata['locator']}] {chunk.text[:PASSAGE_PREVIEW_CHARS]}" for chunk in chunks
    )


def judge_relevance(llm: ChatAnthropic, question: str, chunks: list[RetrievedChunk]) -> RelevanceVerdict:
    if not chunks:
        return RelevanceVerdict(relevant=False, reason="Nothing was retrieved.")

    structured = llm.with_structured_output(RelevanceVerdict)
    messages = RELEVANCE_PROMPT.format_messages(question=question, passages=_format_passages(chunks))
    return call_with_retry(lambda: structured.invoke(messages))

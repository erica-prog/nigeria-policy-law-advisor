"""Each lawyer's last five question-and-answer exchanges per matter.

Display only. Nothing stored here is ever sent back to Claude:
`RAGChain.answer()` builds its prompt from the current question and freshly
retrieved passages alone (docs/11), and replaying earlier answers would let an
unverified or web-sourced claim re-enter the context looking like material to
build on. This module deliberately does not import the chain, so that stays
true by construction rather than by convention.

Each exchange keeps the trust signals its answer carried - whether it came from
the matter's documents or an official website, and whether its citations
checked out - because an answer re-rendered after a reload must look exactly as
trustworthy as it did the first time, and no more.

Pruned to five on every save, inside the same transaction. That is data
minimisation rather than a display limit: these rows hold client facts and
answers about client documents, and the fewer of them retained, the less there
is to lose. For the same reason only citation locators are kept, never the
passages they point to - those already live in the matter itself.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from psycopg.types.json import Jsonb

from policy_advisor.db import connect, transaction

if TYPE_CHECKING:
    from policy_advisor.generation.chain import AnswerResult

MAX_EXCHANGES = 5

_SOURCE_FIELDS = ("locator", "doc_type", "jurisdiction", "source_document", "page")


@dataclass
class ChatExchange:
    question: str
    answer: str
    source: str  # "corpus" | "web" | "none", as on AnswerResult
    faithful: bool
    unsupported_citations: list[str] = field(default_factory=list)
    web_citations: list[dict] = field(default_factory=list)  # {"title", "url"}
    sources: list[dict] = field(default_factory=list)  # locator metadata only, never passage text
    created_at: str = ""


def exchange_from_result(question: str, result: "AnswerResult") -> ChatExchange:
    return ChatExchange(
        question=question,
        answer=result.answer,
        source=result.source,
        faithful=result.faithful,
        unsupported_citations=list(result.unsupported_citations),
        web_citations=[{"title": c.title, "url": c.url} for c in result.web_citations],
        sources=[{key: chunk.metadata.get(key) for key in _SOURCE_FIELDS} for chunk in result.retrieved],
    )


def save_exchange(username: str, matter_id: str, exchange: ChatExchange) -> None:
    citations = Jsonb(
        {
            "unsupported": exchange.unsupported_citations,
            "web": exchange.web_citations,
            "sources": exchange.sources,
        }
    )
    with connect() as conn, transaction(conn):
        conn.execute("INSERT INTO matters (matter_id) VALUES (%s) ON CONFLICT (matter_id) DO NOTHING", (matter_id,))
        conn.execute(
            "INSERT INTO chat_exchanges (username, matter_id, question, answer, source, faithful, citations) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (username, matter_id, exchange.question, exchange.answer, exchange.source, exchange.faithful, citations),
        )
        conn.execute(
            "DELETE FROM chat_exchanges WHERE username = %(user)s AND matter_id = %(matter)s AND id NOT IN ("
            "  SELECT id FROM chat_exchanges WHERE username = %(user)s AND matter_id = %(matter)s "
            "  ORDER BY id DESC LIMIT %(keep)s"
            ")",
            {"user": username, "matter": matter_id, "keep": MAX_EXCHANGES},
        )


def recent_exchanges(username: str, matter_id: str) -> list[ChatExchange]:
    """This lawyer's exchanges in this matter, oldest first, for rendering in
    conversation order. Scoped by username as well as matter: the demo matter
    is shared by everyone, and one lawyer must never see another's questions
    in it."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT question, answer, source, faithful, citations, created_at FROM chat_exchanges "
            "WHERE username = %s AND matter_id = %s ORDER BY id DESC LIMIT %s",
            (username, matter_id, MAX_EXCHANGES),
        ).fetchall()
    exchanges = []
    for row in reversed(rows):
        citations = row["citations"]  # jsonb arrives already decoded
        exchanges.append(
            ChatExchange(
                question=row["question"],
                answer=row["answer"],
                source=row["source"],
                faithful=row["faithful"],
                unsupported_citations=citations.get("unsupported", []),
                web_citations=citations.get("web", []),
                sources=citations.get("sources", []),
                created_at=row["created_at"].isoformat(),
            )
        )
    return exchanges

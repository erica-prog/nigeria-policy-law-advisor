from types import SimpleNamespace

from policy_advisor.chat_history import (
    MAX_EXCHANGES,
    ChatExchange,
    exchange_from_result,
    recent_exchanges,
    save_exchange,
)
from policy_advisor.db import connect


def _exchange(question: str, **overrides) -> ChatExchange:
    fields = {"answer": f"answer to {question}", "source": "corpus", "faithful": True, **overrides}
    return ChatExchange(question=question, **fields)


def test_only_the_newest_five_are_kept(fresh_db):
    for n in range(MAX_EXCHANGES + 1):
        save_exchange("jdoe", "matter-a", _exchange(f"q{n}"))

    assert [e.question for e in recent_exchanges("jdoe", "matter-a")] == ["q1", "q2", "q3", "q4", "q5"]


def test_pruning_deletes_the_rows_not_just_hides_them(fresh_db):
    # Data minimisation means the old exchanges are gone from the file, not
    # merely left out of the query.
    for n in range(MAX_EXCHANGES + 3):
        save_exchange("jdoe", "matter-a", _exchange(f"q{n}"))

    with connect() as conn:
        stored = conn.execute("SELECT COUNT(*) AS n FROM chat_exchanges").fetchone()["n"]
    assert stored == MAX_EXCHANGES


def test_lawyers_never_see_each_others_questions_in_the_shared_matter(fresh_db):
    save_exchange("jdoe", "phase1-demo", _exchange("jdoe's question"))
    save_exchange("asmith", "phase1-demo", _exchange("asmith's question"))

    assert [e.question for e in recent_exchanges("jdoe", "phase1-demo")] == ["jdoe's question"]
    assert [e.question for e in recent_exchanges("asmith", "phase1-demo")] == ["asmith's question"]


def test_pruning_one_lawyer_leaves_another_alone(fresh_db):
    save_exchange("asmith", "matter-a", _exchange("keep me"))
    for n in range(MAX_EXCHANGES + 2):
        save_exchange("jdoe", "matter-a", _exchange(f"q{n}"))

    assert [e.question for e in recent_exchanges("asmith", "matter-a")] == ["keep me"]


def test_history_is_kept_per_matter(fresh_db):
    save_exchange("jdoe", "matter-a", _exchange("about a"))
    save_exchange("jdoe", "matter-b", _exchange("about b"))

    assert [e.question for e in recent_exchanges("jdoe", "matter-a")] == ["about a"]


def test_a_web_sourced_answer_is_still_marked_as_web_after_a_reload(fresh_db):
    # The trust signal is the point of storing these fields: a reloaded web
    # answer rendered as a normal bubble would look as checked as one grounded
    # in the lawyer's documents.
    save_exchange(
        "jdoe",
        "matter-a",
        ChatExchange(
            question="q",
            answer="the Act says x",
            source="web",
            faithful=True,
            web_citations=[{"title": "The Act", "url": "https://nass.gov.ng/act"}],
        ),
    )

    [restored] = recent_exchanges("jdoe", "matter-a")
    assert restored.source == "web"
    assert restored.web_citations == [{"title": "The Act", "url": "https://nass.gov.ng/act"}]


def test_a_failed_citation_check_survives_a_reload(fresh_db):
    save_exchange("jdoe", "matter-a", _exchange("q", faithful=False, unsupported_citations=["Order 9 Rule 9"]))

    [restored] = recent_exchanges("jdoe", "matter-a")
    assert restored.faithful is False
    assert restored.unsupported_citations == ["Order 9 Rule 9"]


def test_passage_text_is_never_stored():
    chunk = SimpleNamespace(
        text="The exclusivity clause runs for eighteen months.",
        metadata={
            "locator": "Paragraph 2",
            "doc_type": "contract",
            "jurisdiction": "",
            "source_document": "contract.docx",
            "page": 1,
            "translated_text": "a translation",
        },
    )
    result = SimpleNamespace(
        answer="18 months", source="corpus", faithful=True, unsupported_citations=[], web_citations=[], retrieved=[chunk]
    )

    exchange = exchange_from_result("how long?", result)

    assert exchange.sources == [
        {"locator": "Paragraph 2", "doc_type": "contract", "jurisdiction": "", "source_document": "contract.docx", "page": 1}
    ]
    assert "eighteen months" not in str(exchange.sources)
